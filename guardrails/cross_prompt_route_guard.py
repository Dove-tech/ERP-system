import json
from typing import Any, Dict, List, Optional

from prompt.prompt_engineering import PromptRegistry, StructuredOutputParser


class CrossPromptRouteGuard:
    """Cross-checks high-risk tool routes with multiple prompts.

    This guard is intentionally narrow: it only runs for risky tools or risky
    user requests. Deterministic permission checks, parameter validation, and
    HITL remain the execution authority.
    """

    PROMPT_IDS = [
        "route_intent_explicit",
        "route_tool_chain_auditor",
        "route_risk_conservative",
    ]

    HIGH_RISK_LEVELS = {"write", "delete", "external_send", "export", "high_risk_process"}
    HIGH_RISK_KEYWORDS = [
        "export", "download", "send", "email", "mail", "delete", "remove", "batch", "all",
        "导出", "下载", "发送", "邮件", "外发", "删除", "移除", "批量", "全部", "所有",
        "创建", "新增", "更新", "修改", "调整",
    ]
    STRICT_KEYWORDS = [
        "delete", "remove", "send", "email", "mail", "batch", "all",
        "删除", "移除", "发送", "邮件", "外发", "批量", "全部", "所有",
    ]

    def __init__(self, prompt_registry: Optional[PromptRegistry] = None, fail_closed: bool = True):
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.fail_closed = fail_closed

    def should_validate(self, tool, raw_query: str = "", risk_level: str = "") -> bool:
        risk = (risk_level or getattr(tool, "risk_level", "") or "").lower()
        if risk in self.HIGH_RISK_LEVELS:
            return True
        text = self._tool_text(tool)
        if any(keyword in text for keyword in self.HIGH_RISK_KEYWORDS):
            return True
        query_text = (raw_query or "").lower()
        return any(keyword in query_text for keyword in self.STRICT_KEYWORDS)

    def validate(
        self,
        tool,
        params: Dict[str, Any],
        raw_query: str,
        task_desc: str,
        llm,
        model: str,
        temperature: float,
        top_p: float,
        risk_level: str = "",
    ) -> Dict[str, Any]:
        if not self.should_validate(tool, raw_query, risk_level=risk_level):
            return {
                "allow": True,
                "guardrail_action": "allow",
                "skipped": True,
                "reason": "low risk route",
                "votes": [],
            }

        if llm is None:
            return self._fallback_result("cross prompt validator has no llm client")

        votes = []
        variables = {
            "raw_query": raw_query or "",
            "task_description": task_desc or "",
            "tool_json": json.dumps(self._tool_payload(tool), ensure_ascii=False, sort_keys=True),
            "params_json": json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
        }
        for prompt_id in self.PROMPT_IDS:
            try:
                prompt = self.prompt_registry.render(prompt_id, model, variables)
                output = llm.chat_completions(prompt, model, temperature, top_p)
                vote = self.parse_vote(output, prompt_id)
            except Exception as exc:
                vote = {
                    "prompt_id": prompt_id,
                    "decision": "clarify" if self.fail_closed else "allow",
                    "reason": f"cross prompt validation failed: {exc}",
                    "parse_error": True,
                }
            votes.append(vote)

        decision = self._decide(votes, tool, raw_query)
        decision["votes"] = votes
        decision["skipped"] = False
        return decision

    def parse_vote(self, output: str, prompt_id: str = "") -> Dict[str, Any]:
        data = StructuredOutputParser.extract_json(output) or {}
        decision = (
            data.get("decision")
            or data.get("risk_decision")
            or data.get("action")
            or data.get("guardrail_action")
        )
        if not decision:
            unsupported = data.get("unsupported_tools") or data.get("unsupported_actions") or []
            decision = "clarify" if unsupported else "allow"
        normalized = self._normalize_decision(str(decision))
        return {
            "prompt_id": prompt_id,
            "decision": normalized,
            "reason": str(data.get("reason") or data.get("rationale") or ""),
            "evidence": data.get("evidence", []),
            "unsupported_tools": data.get("unsupported_tools", []),
            "raw": data,
        }

    def _decide(self, votes: List[Dict[str, Any]], tool, raw_query: str) -> Dict[str, Any]:
        decisions = [vote.get("decision", "clarify") for vote in votes]
        total = len(decisions)
        allow_count = decisions.count("allow")
        block_count = decisions.count("block")
        clarify_count = decisions.count("clarify")
        strict = self._requires_unanimous_allow(tool, raw_query)

        if strict:
            if block_count:
                action = "block"
            elif allow_count == total:
                action = "allow"
            else:
                action = "clarify"
        else:
            if block_count and allow_count < 2:
                action = "block"
            elif block_count:
                action = "clarify"
            elif allow_count >= 2:
                action = "allow"
            else:
                action = "clarify"

        reason = self._build_reason(votes, action, strict, allow_count, clarify_count, block_count)
        return {
            "allow": action == "allow",
            "guardrail_action": action,
            "risk_level": "high_risk_route",
            "reason": reason,
            "vote_summary": {
                "allow": allow_count,
                "clarify": clarify_count,
                "block": block_count,
                "total": total,
                "strict": strict,
            },
            "hallucination_type": "tool_route" if action != "allow" else "",
        }

    def _fallback_result(self, reason: str) -> Dict[str, Any]:
        action = "clarify" if self.fail_closed else "allow"
        return {
            "allow": action == "allow",
            "guardrail_action": action,
            "risk_level": "high_risk_route",
            "reason": reason,
            "votes": [],
            "vote_summary": {"allow": 0, "clarify": 0 if action == "allow" else 1, "block": 0, "total": 0},
            "skipped": False,
            "hallucination_type": "tool_route" if action != "allow" else "",
        }

    def _requires_unanimous_allow(self, tool, raw_query: str) -> bool:
        text = f"{self._tool_text(tool)} {(raw_query or '').lower()}"
        return any(keyword in text for keyword in self.STRICT_KEYWORDS)

    def _build_reason(
        self,
        votes: List[Dict[str, Any]],
        action: str,
        strict: bool,
        allow_count: int,
        clarify_count: int,
        block_count: int,
    ) -> str:
        if action == "allow":
            return "高风险工具意图通过多提示词交叉验证"
        details = [vote.get("reason", "") for vote in votes if vote.get("decision") != "allow" and vote.get("reason")]
        prefix = "高风险工具意图未获得一致确认" if strict else "高风险工具意图未通过多数校验"
        summary = f"{prefix}，投票 allow={allow_count}, clarify={clarify_count}, block={block_count}"
        if details:
            return f"{summary}；原因：{'；'.join(details)}"
        return summary

    def _normalize_decision(self, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized in {"allow", "pass", "approved", "confirm", "continue", "ok"}:
            return "allow"
        if normalized in {"block", "reject", "deny", "denied", "forbid"}:
            return "block"
        return "clarify"

    def _tool_payload(self, tool) -> Dict[str, Any]:
        return {
            "tool_id": getattr(tool, "tool_id", None),
            "operation_id": getattr(tool, "operationId", ""),
            "name": getattr(tool, "name_for_human", ""),
            "description": getattr(tool, "description", ""),
            "method": getattr(tool, "method", ""),
            "risk_level": getattr(tool, "risk_level", ""),
            "required_permissions": list(getattr(tool, "required_permissions", []) or []),
        }

    def _tool_text(self, tool) -> str:
        payload = self._tool_payload(tool)
        return " ".join(str(value) for value in payload.values()).lower()
