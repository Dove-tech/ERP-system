from typing import Any, Callable, Dict, List, Optional

from memory.memory_manager import MemoryManager


class ContextManager:
    """Builds a bounded context instead of blindly sending all chat history."""

    DEFAULT_RECENT_WINDOW = 6
    DEFAULT_MAX_SUMMARY_CHARS = 2000

    def __init__(self, memory_manager: MemoryManager):
        self.memory_manager = memory_manager

    def build_context_state(self, user_id: str, session_id: str, query: str,
                            contexts: List[Dict[str, Any]] = None,
                            context_number: int = 6) -> Dict[str, Any]:
        effective_window = max(int(context_number or self.DEFAULT_RECENT_WINDOW), self.DEFAULT_RECENT_WINDOW)
        recent_from_request = (contexts or [])[-effective_window:]
        recent_from_memory = self.memory_manager.get_recent_messages(user_id, session_id, limit=effective_window)
        summary = self.memory_manager.get_summary(user_id, session_id)
        return {
            "current_query": query,
            "recent_messages": recent_from_request or recent_from_memory,
            "summary": summary.get("summary", ""),
            "summary_metadata": {
                "compacted_message_count": summary.get("compacted_message_count", 0),
                "recent_window": summary.get("recent_window", effective_window),
                "max_summary_chars": summary.get("max_summary_chars", self.DEFAULT_MAX_SUMMARY_CHARS),
            },
            "recent_window": effective_window,
        }

    def rewrite_query_with_context(self, query: str, context_state: Dict[str, Any]) -> str:
        """Make bounded context visible to downstream tool selection and parameter extraction."""
        parts = [f"用户当前请求：{query}"]
        if context_state.get("summary"):
            parts.append(f"会话摘要：{context_state['summary']}")
        return "\n".join(parts)

    def update_after_turn(self, user_id: str, session_id: str, query: str,
                          system_output: str = "",
                          summarizer: Optional[Callable[[str, List[Dict[str, Any]], int], str]] = None
                          ) -> Dict[str, Any]:
        return self.memory_manager.compact_session_summary(
            user_id,
            session_id,
            recent_window=self.DEFAULT_RECENT_WINDOW,
            max_summary_chars=self.DEFAULT_MAX_SUMMARY_CHARS,
            summarizer=summarizer,
        )
