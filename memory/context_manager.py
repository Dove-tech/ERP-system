import json
import re
from typing import Any, Dict, List

from memory.memory_manager import MemoryManager


class ContextManager:
    """Builds a bounded context instead of blindly sending all chat history."""

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    def build_context_state(self, user_id: str, session_id: str, query: str,
                            contexts: List[Dict[str, Any]] = None,
                            context_number: int = 6,
                            memory_scope: str = "erp") -> Dict[str, Any]:
        recent_from_request = (contexts or [])[-context_number:]
        recent_from_memory = self.memory_manager.get_recent_messages(user_id, session_id, limit=context_number)
        summary = self.memory_manager.get_summary(user_id, session_id)
        preferences = self.memory_manager.get_preferences(user_id, memory_scope=memory_scope, limit=5)
        pinned_facts = summary.get("pinned_facts", {}) or {}
        pinned_facts.update(self.memory_manager.extract_pinned_facts(query))
        return {
            "current_query": query,
            "recent_messages": recent_from_request or recent_from_memory,
            "summary": summary.get("summary", ""),
            "pinned_facts": pinned_facts,
            "retrieved_memory": preferences,
        }

    def rewrite_query_with_context(self, query: str, context_state: Dict[str, Any]) -> str:
        """Make context explicit for downstream tool selection and param extraction."""
        parts = [f"用户当前请求：{query}"]
        if context_state.get("summary"):
            parts.append(f"会话摘要：{context_state['summary']}")
        if context_state.get("pinned_facts"):
            parts.append("已确认关键事实：" + json.dumps(context_state["pinned_facts"], ensure_ascii=False))
        if context_state.get("retrieved_memory"):
            parts.append("相关长期偏好：" + json.dumps(context_state["retrieved_memory"], ensure_ascii=False))
        return "\n".join(parts)

    def validate_query_grounding(self, target_query: str, context_state: Dict[str, Any]) -> Dict[str, Any]:
        """Check whether rewritten key entities are grounded in provided context or memory."""
        entities = self._extract_key_entities(target_query)
        if not entities:
            return {
                "is_grounded": True,
                "entities": {},
                "supported_entities": {},
                "unsupported_entities": [],
            }

        source_text = self._normalize_text(self._build_grounding_source_text(context_state))
        supported_entities: Dict[str, Dict[str, Any]] = {}
        unsupported_entities = []
        for key, value in entities.items():
            normalized_value = self._normalize_text(value)
            if not normalized_value:
                continue
            if normalized_value in source_text:
                supported_entities[key] = {"value": value, "source": "context_or_memory"}
            else:
                unsupported_entities.append({"key": key, "value": value})

        return {
            "is_grounded": len(unsupported_entities) == 0,
            "entities": entities,
            "supported_entities": supported_entities,
            "unsupported_entities": unsupported_entities,
        }

    def _extract_key_entities(self, query: str) -> Dict[str, Any]:
        entities = self.memory_manager.extract_pinned_facts(query)
        text = query or ""
        extra_patterns = {
            "product_name": [
                r"(?:产品名称|产品名|物料名称|物料名|成品名称|成品名)\s*(?:为|是|叫|:|：)?\s*([\u4e00-\u9fa5A-Za-z0-9_-]{1,32})",
                r"查询\s*([\u4e00-\u9fa5A-Za-z0-9_-]{1,32})\s*的(?:产品|物料|成品)",
            ],
            "supplier_id": [
                r"(?:供应商|物流供应商)\s*(?:ID|编号|编码)?\s*(?:为|是|:|：)?\s*([A-Za-z0-9_-]{1,32})",
            ],
            "supplier_name": [
                r"(?:供应商名称|供应商名|物流供应商名称|物流供应商名)\s*(?:为|是|叫|:|：)?\s*([\u4e00-\u9fa5A-Za-z0-9_-]{1,32})",
            ],
        }
        for key, patterns in extra_patterns.items():
            if key in entities:
                continue
            for pattern in patterns:
                match = re.search(pattern, text)
                if match:
                    entities[key] = match.group(1)
                    break
        return {key: value for key, value in entities.items() if value not in ("", None, [], {})}

    def _build_grounding_source_text(self, context_state: Dict[str, Any]) -> str:
        chunks: List[str] = []
        if context_state.get("current_query"):
            chunks.append(str(context_state["current_query"]))
        if context_state.get("summary"):
            chunks.append(str(context_state["summary"]))
        for item in context_state.get("recent_messages") or []:
            chunks.append(self._stringify_context_item(item))
        if context_state.get("pinned_facts"):
            chunks.append(json.dumps(context_state["pinned_facts"], ensure_ascii=False))
        if context_state.get("retrieved_memory"):
            chunks.append(json.dumps(context_state["retrieved_memory"], ensure_ascii=False))
        return "\n".join(chunk for chunk in chunks if chunk)

    def _stringify_context_item(self, item: Any) -> str:
        if isinstance(item, dict):
            if "content" in item:
                return str(item.get("content") or "")
            return json.dumps(item, ensure_ascii=False)
        return str(item or "")

    def _normalize_text(self, value: Any) -> str:
        text = str(value or "").lower()
        text = text.replace("年", "-").replace("月", "-").replace("日", "")
        return re.sub(r"[\s,，。.;；:：\"'“”‘’]+", "", text)

    def update_after_turn(self, user_id: str, session_id: str, query: str,
                          system_output: str = "", pinned_facts: Dict[str, Any] = None) -> None:
        summary_text = f"最近目标：{query}"
        if system_output:
            summary_text += f"\n最近系统输出：{system_output[:500]}"
        self.memory_manager.update_summary(user_id, session_id, summary_text, pinned_facts or {})
