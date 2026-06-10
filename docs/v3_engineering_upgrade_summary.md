# V3 工程升级说明

本文档说明 `agent-copilot-hitl-v3-engineering` 相比 `agent-copilot-hitl-prompt-engineering` 的工程升级内容。

本次改造目标不是做 V3 文档里的所有展望项，而是把面试包装中已经达成一致的核心能力补到工程里：session 隔离、短期会话历史、长上下文压缩、上下文改写 grounding、幻觉治理、权限校验、trace、agent eval、成本与速度优化。

未实现的高级展望包括：LangGraph 重构、MCP Server Adapter、多 Agent 拆分、在线评测看板、长期记忆、模糊需求候选生成。

## 1. 新工程

新增工程目录：

```text
agent-copilot-hitl-v3-engineering
```

该目录由 `agent-copilot-hitl-prompt-engineering` 复制而来，原工程未作为本次升级的修改对象。

## 2. 工具选择策略

当前版本已删除业务能力路由预留模块，不再进行业务能力关键词识别，也不再在 `Task` 或 `TraceRecord` 中记录对应的预留字段。

删除原因：

- 该能力原本的目标是通过业务能力路由缩小候选工具范围，提高工具选择效率。
- 当前实现只是静态识别业务关键词，并没有真正减少 `ApiSelectionHub` 的候选工具检索范围。
- 如果继续保留，会让工程说明看起来像已经实现了工具候选集裁剪，但实际工具选择仍然依赖全量工具描述匹配，容易造成架构表达不准确。

当前工具选择链路保持为：

```text
用户请求/子任务
-> ApiSelectionHub 全量工具召回与精排
-> 参数抽取
-> guardrail 校验
-> HITL 确认
-> 工具调用
```

后续如果重新增加业务能力路由，应该让它真正参与候选工具裁剪，例如：

```text
用户请求
-> 业务能力路由
-> 该业务能力绑定的候选工具集合
-> 在小工具集合内做工具选择
```

## 3. Session 隔离与短期会话历史

新增：

```text
entity/memory_entity.py
memory/
  __init__.py
  memory_manager.py
  context_manager.py
```

修改：

```text
entity/task_entity.py
tasks/task_manager.py
app.py
```

实现内容：

- `Task` 增加 `user_id`、`session_id`、`tenant_id`。
- 短期记忆 `SessionMemory` 按 `user_id + session_id` 隔离。
- 摘要记忆 `SummaryMemory` 存储 session 级 conversation summary，并记录 `compacted_message_count`、`recent_window`、`max_summary_chars`。
- `/api_planning` 支持请求中传入 `sessionId/session_id/conversationId`，未传时自动生成新 session。
- 新建任务会写入用户消息，任务结束后写入系统输出，并把掉出 recent window 的旧消息通过 LLM compact 到 summary。
- LLM summary 失败或返回空时，会降级到规则式 extractive fallback，避免影响主流程。
- summary compact 后会写入 `summary_compacted` trace event，包含 `fallback_used` 和 `compacted_message_delta`，便于评测是否触发压缩以及是否走了降级。

设计取舍：

- 当前版本不做长期记忆。
- 不保留 `memory_scope`、`retrieved_memory`、`pinned_facts` 等长期记忆或关键事实预留字段。
- 不把临时订单参数自动写入长期偏好。
- ERP 场景优先保证隔离和可追踪，避免记忆串用。

## 4. 长上下文处理

实现位置：

```text
memory/context_manager.py
app.py
```

实现内容：

- `ContextManager` 不直接把历史全部塞入 prompt，而是构造 bounded context。
- 上下文由 `current_query`、`recent_messages`、`summary` 组成。
- `recent_messages` 至少保留最近 6 条原始消息，避免前端 `contextNumber` 过小导致上下文立即丢失。
- 如果前端没有传 `contexts`，工具模式会使用后端按 `user_id + session_id` 读取出的 recent messages 作为上下文改写输入。
- Copilot 模式会把前端上下文和 session summary 组合成新的 `target_query`，再进入原有 API planning。
- summary 更新使用 `prompt/prompt_registry/conversation_summary_compaction/v1.yaml`，只输出自然语言 conversation summary，不输出复杂 facts 结构。
- 新增 `target_query` grounding 校验：从改写后的请求中抽取产品、订单、数量、交期、区域、生产线、供应商等关键实体，要求它们必须能在当前 query、recent messages 或会话摘要中找到来源。
- 如果关键实体缺少来源，任务不会继续进入 API planning，而是写入 `pending_action=rewrite_grounding_clarify`，进入澄清/确认流程；用户确认后按候选改写继续，用户补充信息时按补充后的请求继续。

面试表达：

> 长上下文不是简单截断，而是把完整历史、rolling summary 和最近窗口分层使用。当前版本不把 summary 当作参数事实库，也不做长期记忆；工具参数仍然优先来自当前 query 和最近原始上下文。对 LLM 改写出的 `target_query` 还要做来源校验，关键实体没有当前上下文或摘要证据时先澄清，不能带着幻觉进入工具调用。

## 5. 已删除能力与未来展望

当前版本已删除：

```text
memory/ambiguity_resolver.py
prompt/prompt_registry/ambiguity_feedback_intent/v1.yaml
prompt/evals/datasets/ambiguity_resolution.json
prompt/evals/datasets/ambiguity_feedback_intent.json
pending_action=ambiguity_confirm
LongTermMemory
memory_scope
retrieved_memory
pinned_facts
```

删除原因：

- 当前项目主线是 ERP 工具调用、权限、HITL、trace 和 eval，不是长期记忆平台。
- 模糊需求候选生成依赖长期记忆、候选 grounding、事实冲突处理和大量 eval，当前版本过重。
- 保留这些字段会让工程看起来已经具备长期记忆和模糊需求解析能力，但实际评测闭环不足。

未来如果重新引入长期记忆或模糊需求候选，需要补齐：

```text
长期记忆存储模型
记忆写入来源和审批策略
记忆过期与冲突处理
候选方案 grounding
模糊需求候选确认 HITL
对应 eval 和 online integration case
```

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

- 新增 `TraceRecord`，存储 `trace_id`、`task_id`、`user_id`、`session_id`、`events`、`final_answer`、`status`。
- 新任务启动时创建 trace。
- 关键节点写入 trace：context 初始化、rewrite grounding、task classified、tool selected、params extracted、missing params、guardrail blocked、tool invocation started/finished、human feedback intent、answer grounding。
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
prompt/evals/datasets/slot_filling_intent.json
prompt/evals/datasets/tool_chain.json
```

新增工作流级集成测试：

```text
prompt/evals/integration_runner.py
prompt/evals/integration_datasets/workflows.json
test/test_integration/test_workflow_metrics.py
docs/integration_testing.md
```

新增 prompt 演进与 bad case 回归说明：

```text
prompt/evals/bad_cases/prompt_bad_cases.json
docs/prompt_eval_badcase_strategy.md
```

保留原有数据集：

```text
human_feedback_intent.json
tool_selection.json
tool_summary.json
```

实现内容：

- eval 从 prompt 级扩展到 agent 链路级。
- 新增任务分类、参数抽取、幻觉护栏、缺参补充、多工具链路评测。
- replay 模式不依赖在线模型，适合作为面试演示和回归验证。
- 新增集成测试 runner，用产品经理整理的“自然语言 -> 标准操作序列”回放 actual trace，计算工具调用准确率、参数正确率、调用时机合理性、无效工具调用占比、工具执行结果利用率、工具异常处理成功率、任务完成度和任务准确率。
- 新增 prompt bad case 台账，用于记录失败来源、错误 trace、期望 trace、根因归类、工程处理动作和补充到哪些 eval 数据集。
- 将“条件式库存不足再下单”“普通库存查询不能过度拆分”“缺少供应商不能硬猜”等 bad case 补入 replay 数据集，防止修复一个 case 后导致原有正确 case 回退。

验证命令：

```powershell
python -m prompt.evals.runner --task all --mode replay --model qwen-max
python -m prompt.evals.integration_runner
python -m unittest discover -s test/test_integration -p "test_*.py"
```

当前结果：

```text
hallucination_guard: 3/3
human_feedback_intent: 6/6
param_extraction: 3/3
slot_filling_intent: 4/4
task_classification: 5/5
tool_chain: 4/4
tool_selection: 5/5
tool_summary: 2/2
integration_workflows: 当前数据集已移除模糊需求 case
```

补充说明：

```text
docs/user_intent_enhancement_upgrade.md
docs/integration_testing.md
```

`docs/user_intent_enhancement_upgrade.md` 专门描述用户意图增强改造，包括参数缺失补全、上下文改写澄清、工具执行确认、pending_action 分流和新增 eval 覆盖。当前版本已删除模糊需求反馈识别。

`docs/integration_testing.md` 专门描述工作流级集成测试，包括 case 数据结构、执行命令和各项指标的计算方式。

`docs/prompt_eval_badcase_strategy.md` 专门描述 prompt 评测如何体现版本演进、bad case 如何搜集与归因、如何通过正反例和回归集避免 prompt 过拟合。

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
python -m py_compile app.py apis\api_planning_hub.py tasks\task_manager.py entity\task_entity.py entity\memory_entity.py memory\memory_manager.py memory\context_manager.py guardrails\hallucination_guard.py trace\trace_manager.py tools\tool_use_hub.py models\remote_embedding_model.py prompt\evals\runner.py
```

已执行：

```powershell
python -m prompt.evals.runner --task all --mode replay --model qwen-max
```

两个检查均通过。

## 11. 面试讲法

推荐主线：

> 这个项目不是普通 Chatbot，而是制造业 ERP Copilot。底层用 Tool Registry 管 ERP OpenAPI，工具选择通过全量工具召回、精排和 LLM 精选完成。执行上用 workflow 和 HITL 控制写操作，用 session scoped memory 和 ContextManager 管住上下文，用 HallucinationGuard 管工具、参数和最终回答幻觉，用 TraceRecord 和 eval datasets 做 badcase 复盘和回归。

边界说明：

> 当前版本已经覆盖企业试点阶段需要的闭环能力。LangGraph、MCP Server、多 Agent 和在线评测看板属于后续平台化演进，不在本次工程升级范围内。
