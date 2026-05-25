from mongoengine import Document, StringField, ListField, DictField, IntField


class Task(Document):
    task_id = StringField() # 任务的唯一性编号
    trace_id = StringField() # Agent 执行链路编号
    user_id = StringField() # 用户隔离维度
    session_id = StringField() # 会话隔离维度
    tenant_id = StringField() # 租户隔离维度，试点阶段默认 internal
    memory_scope = StringField() # 记忆作用域
    selected_skill = StringField() # 当前命中的 ERP Skill
    status = IntField() # 任务的状态
    task_type = IntField()  # 任务的类型
    raw_query = StringField() # 用户的最初查询请求
    changed_query = StringField()  # 查询请求，最初与raw_query一致，任务执行中间可能发生变化
    curr_task_desc = StringField() # 任务的当前描述，一般由大模型依据用户的最初查询请求生成也可以是任务执行过程中描述
    pinned_facts = DictField() # 已确认或可追踪来源的关键事实
    context_summary = StringField() # 长上下文压缩摘要
    pending_action = StringField() # 等待用户确认的业务动作，例如 ambiguity_confirm
    pending_payload = DictField() # 等待用户确认的结构化候选方案
    nodes = ListField(DictField()) # 前端界面调用链展示部分
    edges = ListField(DictField()) # 前端界面调用链展示部分
    graph_title = StringField()  # 前端界面调用链展示部分的标题
    system_output = StringField() # 任务的结果文字输出
    curr_tool_id = IntField() # 当前等待被确认的工具ID
    curr_tool_param = DictField() # 当前等待被确认的工具ID的参数

    def to_dict(self):
        """将 Task 对象转换为字典，前端页面使用"""
        return {
            'task_id': self.task_id,
            'trace_id': self.trace_id,
            'user_id': self.user_id,
            'session_id': self.session_id,
            'tenant_id': self.tenant_id,
            'memoryScope': self.memory_scope,
            'selectedSkill': self.selected_skill,
            'status': self.status,
            'nodes': self.nodes,
            'edges': self.edges,
            'isSuccess': self.graph_title,
            'systemOutput': self.system_output,
            'pinnedFacts': self.pinned_facts,
            'contextSummary': self.context_summary,
            'pendingAction': self.pending_action,
            'pendingPayload': self.pending_payload,
        }
