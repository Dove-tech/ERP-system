import json
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

    def update_after_turn(self, user_id: str, session_id: str, query: str,
                          system_output: str = "", pinned_facts: Dict[str, Any] = None) -> None:
        summary_text = f"最近目标：{query}"
        if system_output:
            summary_text += f"\n最近系统输出：{system_output[:500]}"
        self.memory_manager.update_summary(user_id, session_id, summary_text, pinned_facts or {})
