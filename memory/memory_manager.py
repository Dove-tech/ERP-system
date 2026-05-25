import re
from datetime import datetime
from typing import Any, Dict, List

from mongoengine import connect

from entity import LongTermMemory, SessionMemory, SummaryMemory
from utils import logger


class MemoryManager:
    """Scoped memory layer for ERP Copilot sessions.

    The implementation is deliberately lightweight: short-term memory and
    summaries are stored in MongoDB, while long-term memory only stores
    explicit, low-risk business preferences.
    """

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

    def update_summary(self, user_id: str, session_id: str, summary: str,
                       pinned_facts: Dict[str, Any] = None) -> None:
        if not user_id or not session_id:
            return
        SummaryMemory.objects(user_id=str(user_id), session_id=str(session_id)).update_one(
            set__summary=summary or "",
            set__pinned_facts=pinned_facts or {},
            set__updated_at=datetime.utcnow(),
            upsert=True,
        )

    def get_summary(self, user_id: str, session_id: str) -> Dict[str, Any]:
        item = SummaryMemory.objects(user_id=str(user_id), session_id=str(session_id)).first()
        if item is None:
            return {"summary": "", "pinned_facts": {}}
        return {"summary": item.summary or "", "pinned_facts": item.pinned_facts or {}}

    def upsert_preference(self, user_id: str, memory_scope: str, key: str,
                          value: Dict[str, Any], source: str = "user_confirmed",
                          confidence: float = 1.0, tags: List[str] = None) -> None:
        if not user_id or not key:
            return
        LongTermMemory.objects(
            user_id=str(user_id),
            memory_scope=memory_scope or "erp",
            key=key,
        ).update_one(
            set__value=value or {},
            set__source=source,
            set__confidence=confidence,
            set__tags=tags or [],
            set__updated_at=datetime.utcnow(),
            upsert=True,
        )

    def get_preferences(self, user_id: str, memory_scope: str = "erp",
                        limit: int = 10) -> List[Dict[str, Any]]:
        if not user_id:
            return []
        items = (
            LongTermMemory.objects(user_id=str(user_id), memory_scope=memory_scope or "erp")
            .order_by("-updated_at")
            .limit(limit)
        )
        return [
            {
                "key": item.key,
                "value": item.value,
                "source": item.source,
                "confidence": item.confidence,
                "tags": item.tags,
            }
            for item in items
        ]

    def extract_pinned_facts(self, query: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        facts: Dict[str, Any] = {}
        text = query or ""
        patterns = {
            "product_id": r"(?:产品|物料|成品)\s*(?:ID|编号|编码)?\s*(?:为|是|:|：)?\s*([A-Za-z0-9_-]{1,32})",
            "order_id": r"(?:订单|生产订单)\s*(?:ID|编号|号)?\s*(?:为|是|:|：)?\s*([A-Za-z0-9_-]{1,32})",
            "quantity": r"(?:数量|库存|生产)\s*(?:为|是|:|：)?\s*(\d+)",
            "delivery_date": r"(?:交付|交货|交期|日期)\s*(?:为|是|到|:|：)?\s*([0-9]{4}[-/年][0-9]{1,2}[-/月][0-9]{1,2})",
            "region": r"(?:配送|交付|发往|送到)\s*([\u4e00-\u9fa5]{2,8})",
            "line": r"([0-9A-Za-z_-]+号?生产线)",
        }
        for key, pattern in patterns.items():
            match = re.search(pattern, text)
            if match:
                facts[key] = match.group(1)
        for key, value in (params or {}).items():
            if value not in ("", None, [], {}):
                facts[key] = value
        return facts
