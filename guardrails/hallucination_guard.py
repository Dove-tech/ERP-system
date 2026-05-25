from typing import Any, Dict, List


class HallucinationGuard:
    """Deterministic guardrails for ERP Agent tool calls and answers."""

    WRITE_KEYWORDS = ["创建", "添加", "更新", "修改", "分配", "调整", "取消", "删除", "录入"]
    DELETE_KEYWORDS = ["删除", "取消", "移除"]
    READ_KEYWORDS = ["查询", "获取", "查看", "列出"]

    def classify_tool_risk(self, tool) -> str:
        text = f"{getattr(tool, 'name_for_human', '')} {getattr(tool, 'description', '')} {getattr(tool, 'method', '')}".lower()
        if any(keyword in text for keyword in self.DELETE_KEYWORDS) or "delete" in text:
            return "delete"
        if any(keyword in text for keyword in self.WRITE_KEYWORDS) or any(method in text for method in ["post", "put", "patch"]):
            if any(keyword in text for keyword in self.READ_KEYWORDS):
                return "read"
            return "write"
        return "read"

    def validate_tool_call(self, tool, params: Dict[str, Any], query: str = "",
                           selected_skill: str = "", user_permissions: List[str] = None) -> Dict[str, Any]:
        risk_level = self.classify_tool_risk(tool)
        violations = []
        param_sources = {}
        for key, value in (params or {}).items():
            param_sources[key] = "user_or_context"
            if value in ("", None, [], {}):
                violations.append(f"参数 {key} 为空")
            if isinstance(value, (int, float)) and value < 0:
                violations.append(f"参数 {key} 不能为负数")
            if key.lower() in {"quantity", "stock", "quantityinstock"}:
                try:
                    if int(value) > 100000:
                        violations.append(f"参数 {key} 数量异常大，需要人工确认")
                except Exception:
                    pass

        action = "allow"
        if violations:
            action = "clarify"
        elif risk_level in {"write", "delete", "high_risk_process"}:
            action = "confirm"

        return {
            "allow": action != "reject",
            "guardrail_action": action,
            "risk_level": risk_level,
            "violations": violations,
            "param_sources": param_sources,
            "hallucination_type": "param" if violations else "",
            "selected_skill": selected_skill,
        }

    def check_answer_grounding(self, answer: str, evidences: List[str]) -> Dict[str, Any]:
        if not answer:
            return {"unsupported_claims": [], "grounded": True}
        unsupported = []
        evidence_text = "\n".join(evidences or [])
        for marker in ["已经完成", "已成功", "库存充足", "供应商可用"]:
            if marker in answer and marker not in evidence_text:
                unsupported.append(marker)
        return {"unsupported_claims": unsupported, "grounded": len(unsupported) == 0}
