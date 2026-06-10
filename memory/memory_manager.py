from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from mongoengine import connect

from entity import SessionMemory, SummaryMemory
from utils import logger


class MemoryManager:
    """Short-term session memory and rolling summary storage."""

    DEFAULT_RECENT_WINDOW = 6
    DEFAULT_MAX_SUMMARY_CHARS = 2000

    def __init__(self, mongo_host: str, mongo_db: str, mongo_port: int):
        self.mongo_client = connect(mongo_db, host=mongo_host, port=mongo_port)

    def add_message(self, user_id: str, session_id: str, role: str, content: str,
                    task_id: str = "", message_type: str = "message",
                    metadata: Dict[str, Any] = None) -> None:
        if not user_id or not session_id or not content:
            return
        try:
            SessionMemory(
                user_id=str(user_id),
                session_id=str(session_id),
                task_id=task_id or "",
                role=role,
                content=content,
                message_type=message_type,
                metadata=metadata or {},
            ).save()
        except Exception as exc:
            logger.error(f"写入短期记忆失败: {exc}")

    def get_recent_messages(self, user_id: str, session_id: str, limit: int = 8) -> List[Dict[str, Any]]:
        if not user_id or not session_id:
            return []
        memories = (
            SessionMemory.objects(user_id=str(user_id), session_id=str(session_id))
            .order_by("-created_at")
            .limit(limit)
        )
        return [
            self._memory_to_dict(item)
            for item in reversed(list(memories))
        ]

    def count_messages(self, user_id: str, session_id: str) -> int:
        if not user_id or not session_id:
            return 0
        return SessionMemory.objects(user_id=str(user_id), session_id=str(session_id)).count()

    def get_messages_slice(self, user_id: str, session_id: str, skip: int = 0,
                           limit: int = 20) -> List[Dict[str, Any]]:
        if not user_id or not session_id or limit <= 0:
            return []
        memories = (
            SessionMemory.objects(user_id=str(user_id), session_id=str(session_id))
            .order_by("created_at")
            .skip(max(skip, 0))
            .limit(limit)
        )
        return [self._memory_to_dict(item) for item in memories]

    def update_summary(self, user_id: str, session_id: str, summary: str,
                       compacted_message_count: int = None,
                       recent_window: int = None,
                       max_summary_chars: int = None) -> None:
        if not user_id or not session_id:
            return
        updates = {
            "set__summary": summary or "",
            "set__updated_at": datetime.utcnow(),
        }
        if compacted_message_count is not None:
            updates["set__compacted_message_count"] = max(int(compacted_message_count), 0)
        if recent_window is not None:
            updates["set__recent_window"] = max(int(recent_window), 1)
        if max_summary_chars is not None:
            updates["set__max_summary_chars"] = max(int(max_summary_chars), 200)
        SummaryMemory.objects(user_id=str(user_id), session_id=str(session_id)).update_one(
            upsert=True,
            **updates,
        )

    def get_summary(self, user_id: str, session_id: str) -> Dict[str, Any]:
        item = SummaryMemory.objects(user_id=str(user_id), session_id=str(session_id)).first()
        if item is None:
            return {
                "summary": "",
                "compacted_message_count": 0,
                "recent_window": self.DEFAULT_RECENT_WINDOW,
                "max_summary_chars": self.DEFAULT_MAX_SUMMARY_CHARS,
            }
        return {
            "summary": item.summary or "",
            "compacted_message_count": item.compacted_message_count or 0,
            "recent_window": item.recent_window or self.DEFAULT_RECENT_WINDOW,
            "max_summary_chars": item.max_summary_chars or self.DEFAULT_MAX_SUMMARY_CHARS,
        }

    def compact_session_summary(self, user_id: str, session_id: str,
                                recent_window: int = DEFAULT_RECENT_WINDOW,
                                max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS,
                                summarizer: Optional[Callable[[str, List[Dict[str, Any]], int], str]] = None
                                ) -> Dict[str, Any]:
        """Compact messages that have fallen out of the recent window into summary.

        Full history stays in SessionMemory. Summary is only a compressed background
        for older turns and should not be used as a tool-parameter truth source.
        """
        if not user_id or not session_id:
            return {"summary": ""}

        recent_window = max(int(recent_window or self.DEFAULT_RECENT_WINDOW), self.DEFAULT_RECENT_WINDOW)
        max_summary_chars = max(int(max_summary_chars or self.DEFAULT_MAX_SUMMARY_CHARS), 500)
        total_messages = self.count_messages(user_id, session_id)
        compact_until = max(total_messages - recent_window, 0)
        current_summary = self.get_summary(user_id, session_id)
        already_compacted = max(int(current_summary.get("compacted_message_count") or 0), 0)

        if compact_until <= already_compacted:
            current_summary["changed"] = False
            current_summary["total_messages"] = total_messages
            return current_summary

        messages_to_compact = self.get_messages_slice(
            user_id,
            session_id,
            skip=already_compacted,
            limit=compact_until - already_compacted,
        )
        fallback_used = False
        old_summary = current_summary.get("summary", "")
        new_summary = ""
        if summarizer:
            try:
                new_summary = (summarizer(old_summary, messages_to_compact, max_summary_chars) or "").strip()
            except Exception as exc:
                fallback_used = True
                logger.error(f"LLM 压缩会话摘要失败，降级为规则摘要: {exc}")

        if not new_summary:
            fallback_used = True
            new_summary = self.merge_compaction_summary(
                old_summary,
                messages_to_compact,
                max_summary_chars=max_summary_chars,
            )
        else:
            new_summary = self._trim_summary(new_summary, max_summary_chars)

        self.update_summary(
            user_id,
            session_id,
            new_summary,
            compacted_message_count=compact_until,
            recent_window=recent_window,
            max_summary_chars=max_summary_chars,
        )
        return {
            "summary": new_summary,
            "compacted_message_count": compact_until,
            "recent_window": recent_window,
            "max_summary_chars": max_summary_chars,
            "changed": True,
            "total_messages": total_messages,
            "fallback_used": fallback_used,
            "compacted_message_delta": len(messages_to_compact),
        }

    @classmethod
    def merge_compaction_summary(cls, old_summary: str, messages: List[Dict[str, Any]],
                                 max_summary_chars: int = DEFAULT_MAX_SUMMARY_CHARS) -> str:
        old_summary = (old_summary or "").strip()
        new_block = cls._format_messages_for_summary(messages)
        if not old_summary and not new_block:
            return ""
        if not old_summary:
            merged = new_block
        elif not new_block:
            merged = old_summary
        else:
            merged = f"{old_summary}\n\n新增压缩内容：\n{new_block}"
        return cls._trim_summary(merged, max_summary_chars)

    @staticmethod
    def _format_messages_for_summary(messages: List[Dict[str, Any]]) -> str:
        lines = []
        for item in messages or []:
            role = item.get("role") or "message"
            content = " ".join(str(item.get("content") or "").split())
            if not content:
                continue
            message_type = item.get("message_type") or "message"
            lines.append(f"- {role}/{message_type}: {content[:300]}")
        return "\n".join(lines)

    @staticmethod
    def _trim_summary(summary: str, max_summary_chars: int) -> str:
        max_summary_chars = max(int(max_summary_chars or 2000), 500)
        if len(summary) <= max_summary_chars:
            return summary
        marker = "（摘要已压缩，保留较新的会话背景）\n"
        return marker + summary[-(max_summary_chars - len(marker)):]

    @staticmethod
    def _memory_to_dict(item: SessionMemory) -> Dict[str, Any]:
        return {
            "id": str(item.id),
            "role": item.role,
            "content": item.content,
            "task_id": item.task_id,
            "message_type": item.message_type,
            "metadata": item.metadata,
            "created_at": item.created_at.isoformat() if item.created_at else "",
        }
