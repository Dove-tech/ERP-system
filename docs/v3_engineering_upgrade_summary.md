# V3 工程升级说明

本文档说明 `agent-copilot-hitl-v3-engineering` 相比 `agent-copilot-hitl-prompt-engineering` 的工程升级内容。

本次改造目标不是做 V3 文档里的所有展望项，而是把面试包装中已经达成一致的核心能力补到工程里：ERP Skills、session 隔离、scoped memory、长上下文构造、模糊需求澄清、幻觉治理、trace、agent eval、成本与速度优化。

未实现的高级展望包括：LangGraph 重构、MCP Server Adapter、多 Agent 拆分、在线评测看板、更复杂的长期记忆冲突治理。

## 1. 新工程

新增工程目录：

```text
agent-copilot-hitl-v3-engineering
```

该目录由 `agent-copilot-hitl-prompt-engineering` 复制而来，原工程未作为本次升级的修改对象。

## 2. ERP Skills

新增：

```text
skills/
  __init__.py
  erp_skills.py
```

实现内容：

- 增加静态 `ERPSkillRegistry`。
- 支持订单录入、库存查询、供应商选择、生产计划调整、生产进度更新、模糊需求澄清等 ERP Skill。
- 在 `ApiPlanningHub.apis_planning()` 中先识别 Skill，再进入任务分类、工具选择和参数抽取。
- Task 中记录 `selected_skill`，便于 trace、eval 和面试展示。

面试表达：

> 底层仍然是 Tool Registry 管 ERP API，上层用 ERP Skills 封装高频业务流程。Tool 是原子能力，Skill 是业务能力包。

## 3. Session 隔离与 Scoped Memory

新增：

```text
entity/memory_entity.py
memory/
  __init__.py
  memory_manager.py
  context_manager.py
  ambiguity_resolver.py
```

修改：

```text
entity/task_entity.py
tasks/task_manager.py
app.py
```

实现内容：

- `Task` 增加 `user_id`、`session_id`、`tenant_id`、`memory_scope`。
- 短期记忆 `SessionMemory` 按 `user_id + session_id` 隔离。
- 摘要记忆 `SummaryMemory` 存储 session 级摘要和 pinned facts。
- 长期记忆 `LongTermMemory` 只用于低风险、用户确认过的业务偏好。
- `/api_planning` 支持请求中传入 `sessionId/session_id/conversationId`，未传时自动生成新 session。
- 新建任务会写入用户消息，任务结束后写入系统输出和摘要。

设计取舍：

- 不做无限长期记忆。
- 不把临时订单参数自动写入长期记忆。
- ERP 场景优先保证隔离和可追踪，避免记忆串用。

## 4. 长上下文处理

实现位置：

```text
memory/context_manager.py
app.py
```

实现内容：

- `ContextManager` 不直接把历史全部塞入 prompt，而是构造 bounded context。
- 上下文由 `current_query`、`recent_messages`、`summary`、`pinned_facts`、`retrieved_memory` 组成。
- 已确认字段会进入 `pinned_facts`，例如物料、订单、数量、交期、区域、生产线。
- Copilot 模式会把前端上下文和后端 scoped memory 组合成新的 `target_query`，再进入原有 API planning。
- 新增 `target_query` grounding 校验：从改写后的请求中抽取产品、订单、数量、交期、区域、生产线、供应商等关键实体，要求它们必须能在 `contexts`、`pinned_facts`、会话摘要或长期记忆中找到来源。
- 如果关键实体缺少来源，任务不会继续进入 API planning，而是写入 `pending_action=rewrite_grounding_clarify`，进入澄清/确认流程；用户确认后按候选改写继续，用户补充信息时按补充后的请求继续。

面试表达：

> 长上下文不是简单截断，而是状态构造。最新输入、摘要、关键事实和长期偏好有不同优先级，ERP 关键字段必须结构化 pin 住。对 LLM 改写出的 `target_query` 还要做来源校验，关键实体没有上下文或记忆证据时先澄清，不能带着幻觉进入工具调用。

## 5. 模糊需求澄清

实现位置：

```text
memory/ambiguity_resolver.py
app.py
```

实现内容：

- 识别“老样子”“上次”“照旧”“按原计划”“加急处理”等模糊表达。
- 关键词只做低成本初筛；命中模糊表达后，会把 `recent_messages`、`summary`、`pinned_facts`、`retrieved_memory` 交给大模型生成结构化候选方案。
- 大模型输出必须是 JSON，包含 `candidate_action`、`candidate_query`、`confirm_message`、`missing_fields`、`evidence`、`confidence`。
- 候选方案进入用户确认前，会做关键实体来源校验；产品、订单、数量、交期、供应商、生产线等信息必须能在上下文或记忆中找到证据。
- 如果没有可靠记忆，要求用户补充产品、数量、交期、供应商或生产线等关键字段。
- `Task` 增加 `pending_action` 和 `pending_payload`，用于保存等待确认的候选方案。
- 用户确认后继续进入原有 API planning；用户补充信息时，将补充内容合并进任务请求再执行。

关键原则：

> 规则负责判断“这是不是模糊需求”，大模型负责把上下文和记忆整理成用户能看懂的候选业务方案。模型只能生成候选解释，不能替代用户确认；候选解释必须带证据，证据不足时进入澄清。

## 6. Hallucination Control

新增：

```text
guardrails/
  __init__.py
  hallucination_guard.py
```

修改：

```text
apis/api_planning_hub.py
```

实现内容：

- 工具必须来自 Tool Registry 的候选结果，找不到工具时拒绝执行。
- `HallucinationGuard` 对工具调用做风险分级：read/write/delete。
- 对空参数、负数参数、异常大数量等参数幻觉做确定性拦截。
- 写操作和高风险操作进入确认流程。
- 工具调用前记录 `guardrail_action`、`risk_level`、`violations`、`param_sources`。
- 最终回答前增加 `answer_grounding_checked` trace event，检查最终回答是否缺少工具返回证据。

覆盖的幻觉类型：

- Tool hallucination：编造或错选工具。
- Parameter hallucination：编造物料、订单、数量、交期等参数。
- Fact hallucination：最终回答包含工具结果中没有的信息。
- Memory hallucination：把未确认记忆当事实。
- Rewrite hallucination：上下文改写时凭空补充关键实体。
- Workflow hallucination：跳过确认或状态校验直接执行。

## 7. Trace

新增：

```text
trace/
  __init__.py
  trace_manager.py
```

修改：

```text
entity/memory_entity.py
app.py
apis/api_planning_hub.py
utils/const.py
```

实现内容：

- 新增 `TraceRecord`，存储 `trace_id`、`task_id`、`user_id`、`session_id`、`selected_skill`、`events`、`final_answer`、`status`。
- 新任务启动时创建 trace。
- 关键节点写入 trace：context 初始化、ambiguity detected/resolved、skill selected、task classified、tool selected、params extracted、guardrail blocked、tool invocation started/finished、human feedback intent、answer grounding。
- 新增 `/api_trace_status` 接口，支持按 `trace_id` 或 `task_id` 查询 trace。
- 默认权限增加 `get_trace_status`。

面试表达：

> nodes/edges 是前端执行链展示，TraceRecord 是后端可复盘数据。面试时可以用 trace 解释 badcase 是错在任务分类、工具选择、参数抽取、安全校验还是最终总结。

## 8. Agent Eval

修改：

```text
prompt/evals/runner.py
```

新增数据集：

```text
prompt/evals/datasets/task_classification.json
prompt/evals/datasets/param_extraction.json
prompt/evals/datasets/hallucination_guard.json
prompt/evals/datasets/ambiguity_resolution.json
prompt/evals/datasets/tool_chain.json
```

保留原有数据集：

```text
human_feedback_intent.json
tool_selection.json
tool_summary.json
```

实现内容：

- eval 从 prompt 级扩展到 agent 链路级。
- 新增任务分类、参数抽取、幻觉护栏、模糊需求澄清、多工具链路评测。
- replay 模式不依赖在线模型，适合作为面试演示和回归验证。

验证命令：

```powershell
python -m prompt.evals.runner --task all --mode replay --model qwen-max
```

当前结果：

```text
ambiguity_resolution: 3/3
hallucination_guard: 3/3
human_feedback_intent: 6/6
param_extraction: 2/2
task_classification: 2/2
tool_chain: 2/2
tool_selection: 3/3
tool_summary: 2/2
```

## 9. 成本与速度

修改：

```text
tools/tool_use_hub.py
models/remote_embedding_model.py
```

实现内容：

- `ToolUseHub` 对 GET 查询类工具增加短 TTL 缓存，默认 300 秒。
- 写操作、删除操作不缓存，避免 ERP 状态不一致。
- 工具调用缓存 key 由 `tool_id`、`operationId`、`path` 和参数组成。
- `RemoteEmbeddingModel` 移除硬编码 API key，统一从 `utils.config` 读取 `model_api_key` 和 `model_base_url`。

面试表达：

> 成本优化不是简单换便宜模型，而是规则能解决的不走模型，读操作可缓存，写操作不缓存，工具候选先召回再让 LLM 精选，减少 prompt 长度和误选概率。

## 10. 验证

已执行：

```powershell
python -m py_compile app.py apis\api_planning_hub.py tasks\task_manager.py entity\task_entity.py entity\memory_entity.py memory\memory_manager.py memory\context_manager.py memory\ambiguity_resolver.py skills\erp_skills.py guardrails\hallucination_guard.py trace\trace_manager.py tools\tool_use_hub.py models\remote_embedding_model.py prompt\evals\runner.py
```

已执行：

```powershell
python -m prompt.evals.runner --task all --mode replay --model qwen-max
```

两个检查均通过。

## 11. 面试讲法

推荐主线：

> 这个项目不是普通 Chatbot，而是制造业 ERP Copilot。底层用 Tool Registry 管 ERP OpenAPI，上层用 ERP Skills 封装订单录入、库存查询、供应商选择、生产计划调整等业务能力。执行上用 workflow 和 HITL 控制写操作，用 session scoped memory 和 ContextManager 管住上下文，用 HallucinationGuard 管工具、参数和最终回答幻觉，用 TraceRecord 和 eval datasets 做 badcase 复盘和回归。

边界说明：

> 当前版本已经覆盖企业试点阶段需要的闭环能力。LangGraph、MCP Server、多 Agent 和在线评测看板属于后续平台化演进，不在本次工程升级范围内。
