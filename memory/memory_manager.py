from datetime import datetime
from typing import Any, Dict, List

from mongoengine import connect

from entity import SessionMemory, SummaryMemory
from utils import logger


class MemoryManager:
    """Short-term session memory and rolling summary storage."""

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
            {
                "role": item.role,
                "content": item.content,
                "task_id": item.task_id,
                "message_type": item.message_type,
                "metadata": item.metadata,
            }
            for item in reversed(list(memories))
        ]

    def update_summary(self, user_id: str, session_id: str, summary: str) -> None:
        if not user_id or not session_id:
            return
        SummaryMemory.objects(user_id=str(user_id), session_id=str(session_id)).update_one(
            set__summary=summary or "",
            set__updated_at=datetime.utcnow(),
            upsert=True,
        )

    def get_summary(self, user_id: str, session_id: str) -> Dict[str, Any]:
        item = SummaryMemory.objects(user_id=str(user_id), session_id=str(session_id)).first()
        if item is None:
            return {"summary": ""}
        return {"summary": item.summary or ""}
