import json
import re
from typing import Any, Dict, List, Optional

from memory.memory_manager import MemoryManager
from prompt.prompt_engineering import StructuredOutputParser
from utils import logger


class AmbiguityResolver:
    """Turns vague ERP instructions into confirmable, evidence-backed candidate plans."""

    AMBIGUOUS_TERMS = [
        "老样子",
        "上次",
        "之前",
        "照旧",
        "照上次",
        "按原计划",
        "还是用之前",
        "加急处理",
        "默认",
        "按之前",
        "和之前一样",
        "跟上次一样",
    ]

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    def is_ambiguous(self, query: str) -> bool:
        return any(term in (query or "") for term in self.AMBIGUOUS_TERMS)

    def resolve(self, query: str, user_id: str, session_id: str,
                memory_scope: str = "erp", context_state: Dict[str, Any] = None,
                llm: Any = None, model: str = "", temperature: float = 0.01,
                top_p: float = 0.01) -> Dict[str, Any]:
        if not self.is_ambiguous(query):
            return {"is_ambiguous": False}

        evidence_context = self._build_evidence_context(query, user_id, session_id, memory_scope, context_state)

        if llm is not None and model:
            llm_result = self._resolve_with_llm(query, evidence_context, llm, model, temperature, top_p)
            if llm_result is not None:
                return llm_result

        return self._fallback_resolve(query, evidence_context)

    def _resolve_with_llm(self, query: str, evidence_context: Dict[str, Any],
                          llm: Any, model: str, temperature: float, top_p: float) -> Optional[Dict[str, Any]]:
        prompt = self._build_prompt(query, evidence_context)
        try:
            output = llm.chat_completions(prompt, model, temperature, top_p)
            data = StructuredOutputParser.extract_json(output)
        except Exception as exc:
            logger.error(f"使用大模型解析模糊需求失败: {exc}")
            return None

        if not data:
            logger.warning(f"模糊需求大模型输出无法解析为 JSON: {output}")
            return None

        return self._normalize_llm_result(query, data, evidence_context)

    def _normalize_llm_result(self, query: str, data: Dict[str, Any],
                              evidence_context: Dict[str, Any]) -> Dict[str, Any]:
        candidate_query = str(data.get("candidate_query", "") or "").strip()
        confidence = self._safe_float(data.get("confidence", 0.0))
        is_resolvable = bool(data.get("is_resolvable")) and bool(candidate_query) and confidence >= 0.6
        evidence = data.get("evidence", [])
        evidence = evidence if isinstance(evidence, list) else []
        missing_fields = data.get("missing_fields", [])
        missing_fields = missing_fields if isinstance(missing_fields, list) else []
        candidate = {
            "query": query,
            "candidate_action": str(data.get("candidate_action", "") or ""),
            "candidate_query": candidate_query,
            "missing_fields": missing_fields,
            "evidence": evidence,
            "pinned_facts": evidence_context.get("pinned_facts", {}),
            "preferences": evidence_context.get("retrieved_memory", []),
        }

        if is_resolvable:
            grounding = self._validate_candidate_grounding(candidate_query, evidence_context)
            if grounding["is_grounded"] and evidence:
                resolved_query = (
                    f"{candidate_query}\n"
                    f"候选解释来源：{json.dumps(evidence, ensure_ascii=False)}"
                )
                message = str(data.get("confirm_message", "") or "").strip()
                if not message:
                    message = f"我理解你是想：{candidate_query}\n请确认是否按这个方案继续执行；如果不对，请直接补充正确信息。"
                return {
                    "is_ambiguous": True,
                    "confidence": confidence,
                    "candidate": candidate,
                    "resolved_query": resolved_query,
                    "message": message,
                    "llm_used": True,
                    "grounding": grounding,
                }

            candidate["grounding"] = grounding
            missing_fields = missing_fields or [item["key"] for item in grounding.get("unsupported_entities", [])]

        message = str(data.get("confirm_message", "") or "").strip()
        if not message:
            missing_desc = "、".join(str(item) for item in missing_fields) if missing_fields else "产品、订单、数量、交期、供应商或生产线"
            message = f"这个请求缺少可靠上下文证据，我不能直接按记忆猜测执行。请补充：{missing_desc}。"

        return {
            "is_ambiguous": True,
            "confidence": min(confidence, 0.59),
            "candidate": candidate,
            "resolved_query": "",
            "message": message,
            "llm_used": True,
            "grounding": candidate.get("grounding", {}),
        }

    def _fallback_resolve(self, query: str, evidence_context: Dict[str, Any]) -> Dict[str, Any]:
        pinned_facts = evidence_context.get("pinned_facts", {}) or {}
        preferences = evidence_context.get("retrieved_memory", []) or []
        candidate = {
            "query": query,
            "pinned_facts": pinned_facts,
            "preferences": preferences,
        }
        confidence = 0.7 if preferences or pinned_facts else 0.35

        if confidence >= 0.6:
            resolved_query = (
                f"{query}\n"
                f"候选解释来自已确认记忆和当前会话事实："
                f"{json.dumps(candidate, ensure_ascii=False)}"
            )
            message = (
                "我理解你的模糊指令可能对应以下候选方案：\n"
                f"{json.dumps(candidate, ensure_ascii=False, indent=2)}\n"
                "请确认是否按这个候选方案继续执行；如果不对，请直接补充产品、数量、交期、供应商或生产线。"
            )
        else:
            resolved_query = ""
            message = (
                "这个请求缺少关键业务字段，我不能直接按记忆猜测执行。"
                "请补充产品/物料、数量、交期、供应商或生产线等信息。"
            )

        return {
            "is_ambiguous": True,
            "confidence": confidence,
            "candidate": candidate,
            "resolved_query": resolved_query,
            "message": message,
            "llm_used": False,
        }

    def _build_evidence_context(self, query: str, user_id: str, session_id: str,
                                memory_scope: str, context_state: Dict[str, Any] = None) -> Dict[str, Any]:
        summary = self.memory_manager.get_summary(user_id, session_id)
        preferences = self.memory_manager.get_preferences(user_id, memory_scope=memory_scope, limit=5)
        context_state = context_state or {}
        pinned_facts = {}
        pinned_facts.update(summary.get("pinned_facts", {}) or {})
        pinned_facts.update(context_state.get("pinned_facts", {}) or {})
        return {
            "current_query": context_state.get("current_query") or query,
            "recent_messages": context_state.get("recent_messages", []),
            "summary": context_state.get("summary") or summary.get("summary", ""),
            "pinned_facts": pinned_facts,
            "retrieved_memory": context_state.get("retrieved_memory") or preferences,
        }

    def _build_prompt(self, query: str, evidence_context: Dict[str, Any]) -> str:
        context_json = json.dumps(evidence_context, ensure_ascii=False, indent=2)
        return f"""
你是一个制造业 ERP Copilot 的模糊需求解析器。

任务：用户的请求包含“老样子、上次、之前、照旧、默认”等模糊表达。请只基于给定上下文和记忆，生成一个可让用户确认的候选业务方案。

硬性规则：
1. 只能使用输入中出现的事实，不能编造产品、订单、数量、交期、供应商、生产线、仓库或业务结果。
2. 如果证据不足，必须返回 is_resolvable=false，并列出 missing_fields。
3. 如果可以解析，candidate_query 必须是一句完整、可执行的中文 ERP 请求。
4. evidence 必须列出候选方案每个关键事实来自哪里，source 只能是 current_query、recent_messages、summary、pinned_facts、retrieved_memory。
5. 不要直接决定执行，只生成给用户确认的候选方案。
6. 只输出 JSON，不要输出解释文本或 markdown。

用户模糊请求：
{query}

可用上下文和记忆：
{context_json}

输出 JSON 格式：
{{
  "is_resolvable": true,
  "candidate_action": "update_supplier | create_order | query_inventory | expedite_order | other | unclear",
  "candidate_query": "完整中文 ERP 请求；证据不足时为空字符串",
  "confirm_message": "面向用户的确认文案，例如：我理解你是想...是否确认？",
  "missing_fields": [],
  "evidence": [
    {{
      "source": "pinned_facts",
      "key": "product_id",
      "value": "M-1001",
      "quote": "可选，引用原始证据片段"
    }}
  ],
  "confidence": 0.0
}}
"""

    def _validate_candidate_grounding(self, candidate_query: str, evidence_context: Dict[str, Any]) -> Dict[str, Any]:
        entities = self._extract_candidate_entities(candidate_query)
        if not entities:
            return {"is_grounded": True, "entities": {}, "unsupported_entities": []}

        source_text = self._normalize_text(json.dumps(evidence_context, ensure_ascii=False))
        unsupported = []
        for key, value in entities.items():
            normalized_value = self._normalize_text(value)
            if normalized_value and normalized_value not in source_text:
                unsupported.append({"key": key, "value": value})
        return {
            "is_grounded": len(unsupported) == 0,
            "entities": entities,
            "unsupported_entities": unsupported,
        }

    def _extract_candidate_entities(self, text: str) -> Dict[str, Any]:
        if hasattr(self.memory_manager, "extract_pinned_facts"):
            entities = self.memory_manager.extract_pinned_facts(text) or {}
        else:
            entities = {}
        extra_patterns = {
            "supplier_id": r"(?:供应商|物流供应商)\s*(?:ID|编号|编码)?\s*(?:为|是|改为|换成|使用|:|：)?\s*([A-Za-z0-9_-]{1,32})",
            "supplier_name": r"(?:供应商名称|供应商名|物流供应商名称|物流供应商名|供应商)\s*(?:为|是|改为|换成|使用|:|：)?\s*([\u4e00-\u9fa5A-Za-z0-9_-]{1,32})",
        }
        for key, pattern in extra_patterns.items():
            if key in entities:
                continue
            match = re.search(pattern, text or "")
            if match:
                entities[key] = match.group(1)
        return {key: value for key, value in entities.items() if value not in ("", None, [], {})}

    def _safe_float(self, value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except Exception:
            return 0.0

    def _normalize_text(self, value: Any) -> str:
        text = str(value or "").lower()
        text = text.replace("年", "-").replace("月", "-").replace("日", "")
        return re.sub(r"[\s,，。.;；:：\"'“”‘’]+", "", text)
