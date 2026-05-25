import uuid
from datetime import datetime
from typing import Any, Dict

from mongoengine import connect

from entity import TraceRecord
from utils import logger


class TraceManager:
    """Lightweight trace manager for Agent replay and badcase analysis."""

    def __init__(self, mongo_host: str, mongo_db: str, mongo_port: int):
        self.mongo_client = connect(mongo_db, host=mongo_host, port=mongo_port)

    def start_trace(self, task_id: str, user_id: str, session_id: str,
                    query: str, selected_skill: str = "") -> str:
        trace_id = str(uuid.uuid4())
        try:
            TraceRecord(
                trace_id=trace_id,
                task_id=task_id,
                user_id=str(user_id or ""),
                session_id=str(session_id or ""),
                selected_skill=selected_skill or "",
                query=query or "",
                events=[],
            ).save()
        except Exception as exc:
            logger.error(f"创建 trace 失败: {exc}")
        return trace_id

    def add_event(self, trace_id: str, event_type: str, payload: Dict[str, Any] = None) -> None:
        if not trace_id:
            return
        event = {
            "event_type": event_type,
            "payload": payload or {},
            "created_at": datetime.utcnow().isoformat(),
        }
        try:
            TraceRecord.objects(trace_id=trace_id).update_one(
                push__events=event,
                set__updated_at=datetime.utcnow(),
                upsert=False,
            )
        except Exception as exc:
            logger.error(f"写入 trace 事件失败[{trace_id}:{event_type}]: {exc}")

    def finish_trace(self, trace_id: str, final_answer: str = "", status: str = "finished") -> None:
        if not trace_id:
            return
        try:
            TraceRecord.objects(trace_id=trace_id).update_one(
                set__final_answer=final_answer or "",
                set__status=status,
                set__updated_at=datetime.utcnow(),
            )
        except Exception as exc:
            logger.error(f"结束 trace 失败[{trace_id}]: {exc}")

    def get_trace(self, trace_id: str = "", task_id: str = "") -> Dict[str, Any]:
        if not trace_id and not task_id:
            return {}
        try:
            item = TraceRecord.objects(trace_id=trace_id).first() if trace_id else TraceRecord.objects(task_id=task_id).first()
            if item is None:
                return {}
            return {
                "trace_id": item.trace_id,
                "task_id": item.task_id,
                "user_id": item.user_id,
                "session_id": item.session_id,
                "selected_skill": item.selected_skill,
                "query": item.query,
                "events": item.events,
                "final_answer": item.final_answer,
                "status": item.status,
                "created_at": item.created_at.isoformat() if item.created_at else "",
                "updated_at": item.updated_at.isoformat() if item.updated_at else "",
            }
        except Exception as exc:
            logger.error(f"读取 trace 失败[{trace_id or task_id}]: {exc}")
            return {}
