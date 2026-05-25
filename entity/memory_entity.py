from datetime import datetime

from mongoengine import DateTimeField, DictField, Document, FloatField, ListField, StringField


class SessionMemory(Document):
    """Short-term memory scoped to one user session."""

    user_id = StringField(required=True)
    session_id = StringField(required=True)
    task_id = StringField()
    role = StringField()
    content = StringField()
    message_type = StringField(default="message")
    metadata = DictField()
    created_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "session_memories",
        "indexes": ["user_id", "session_id", "task_id", "created_at"],
    }


class SummaryMemory(Document):
    """Rolling summary for long-running sessions."""

    user_id = StringField(required=True)
    session_id = StringField(required=True)
    summary = StringField(default="")
    pinned_facts = DictField()
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "summary_memories",
        "indexes": ["user_id", "session_id", "updated_at"],
    }


class LongTermMemory(Document):
    """Scoped long-term business preference memory."""

    user_id = StringField(required=True)
    memory_scope = StringField(default="erp")
    key = StringField(required=True)
    value = DictField()
    source = StringField(default="user_confirmed")
    confidence = FloatField(default=1.0)
    tags = ListField(StringField())
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "long_term_memories",
        "indexes": ["user_id", "memory_scope", "key", "updated_at"],
    }


class TraceRecord(Document):
    """Lightweight Agent trace for replay, eval and interview demonstration."""

    trace_id = StringField(required=True, unique=True)
    task_id = StringField()
    user_id = StringField()
    session_id = StringField()
    selected_skill = StringField()
    query = StringField()
    events = ListField(DictField())
    final_answer = StringField()
    status = StringField(default="running")
    created_at = DateTimeField(default=datetime.utcnow)
    updated_at = DateTimeField(default=datetime.utcnow)

    meta = {
        "collection": "agent_traces",
        "indexes": ["trace_id", "task_id", "user_id", "session_id", "selected_skill"],
    }
