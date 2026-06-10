from mongoengine import DictField, StringField, IntField, Document, ListField


class User(Document):
    user_id = IntField(required=True)
    userName = StringField(unique=True, required=True)
    password = StringField(required=True)
    user_authority = ListField(StringField(), default=[])
    roles = ListField(StringField(), default=[])
    tool_permissions = ListField(StringField(), default=[])
    allowed_regions = ListField(StringField(), default=[])
    data_scope = DictField(default={})

    meta = {
        "strict": False,
    }
