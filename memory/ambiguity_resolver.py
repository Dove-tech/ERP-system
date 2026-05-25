import json
from typing import Any, Dict

from memory.memory_manager import MemoryManager


class AmbiguityResolver:
    """Turns vague ERP instructions into confirmable candidate plans."""

    AMBIGUOUS_TERMS = [
        "老样子",
        "上次",
        "之前",
        "照旧",
        "照上次",
        "按原计划",
        "还是用之前",
        "加急处理",
    ]

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    def is_ambiguous(self, query: str) -> bool:
        return any(term in (query or "") for term in self.AMBIGUOUS_TERMS)

    def resolve(self, query: str, user_id: str, session_id: str,
                memory_scope: str = "erp") -> Dict[str, Any]:
        if not self.is_ambiguous(query):
            return {"is_ambiguous": False}

        preferences = self.memory_manager.get_preferences(user_id, memory_scope=memory_scope, limit=5)
        summary = self.memory_manager.get_summary(user_id, session_id)
        pinned_facts = summary.get("pinned_facts", {}) or {}
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
        }
