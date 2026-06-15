# ERP Agent Copilot LangGraph 重构与面试说明

生成时间：2026-06-15  
分支：`feature/langgraph-refactor`

## 1. 一句话定位

这个项目可以包装为：**基于 LangGraph 的企业 ERP 可控型 Agent Copilot**。

它不是一个普通 Chatbot，也不是完全自主 ReAct Agent，而是把 ERP 业务 API 调用过程建模为一个可追踪、可挂起、可恢复、带权限和人工确认的状态图：

```text
用户请求
-> 上下文构造
-> LangGraph 状态图
-> 任务分类
-> 工具召回与选择
-> 参数抽取与补参
-> 权限校验
-> Guardrail
-> HITL 人工确认
-> ERP API 调用
-> 结果总结
-> Trace / Memory / Eval 闭环
```

面试中建议说：

> 我们没有把 LangGraph 当成装饰性框架，而是用它承载 ERP Agent 的状态机编排。因为这个项目天然存在分支、状态、人工确认、缺参挂起、权限校验和外部 API 副作用控制，这些都是 LangGraph 相比普通 LangChain agent 更有价值的地方。

## 2. 为什么这个项目适合 LangGraph

知乎面试总结里有一个判断标准：如果你的流程只是“调模型、调工具、再调模型”，上 LangGraph 可能是过度设计；但如果流程里有真实分支、状态持久化、人工协同、恢复和副作用边界，LangGraph 就有充分理由。

这个 ERP 项目满足后者。

### 2.1 真实分支

同一句用户请求进入系统后，至少有这些分支：

- 找不到工具：进入 no_tool_found，拒绝编造工具。
- 找到工具但缺参：进入 `missing_params_clarify`。
- 参数完整但无权限：进入 permission_blocked。
- 参数异常或高风险：进入 guardrail_blocked。
- 写操作或高风险操作：进入 `tool_execution_confirm`。
- 用户确认：继续执行工具。
- 用户取消：终止任务。
- 用户修改参数：重新合并参数并校验。

这些不是 prompt 里“想一想”能解决的问题，而是工程状态机。

### 2.2 真实状态

项目里已经存在状态基础：

- `task_id`：任务生命周期。
- `trace_id`：执行链路复盘。
- `session_id`：会话隔离。
- `pending_action`：等待用户操作的状态。
- `pending_payload`：挂起时保留的结构化上下文。
- `curr_tool_id` / `curr_tool_param`：等待确认的工具与参数。
- `nodes` / `edges`：前端展示的调用链。

这些字段天然可以映射为 LangGraph 的 graph state。

### 2.3 真实人工协同

ERP 写操作不能让模型直接执行。创建订单、修改库存、删除记录、发送邮件等操作都需要 HITL。

这正是 LangGraph 适合的地方：在工具执行前设置 human gate，保存当前状态，等待用户确认、取消或修改参数。

### 2.4 真实副作用边界

工具调用 ERP API 是外部副作用。面试官很容易追问：

- resume 后会不会重复下单？
- 用户确认前有没有真正执行？
- 工具超时后是否会盲目重试？
- 写操作有没有幂等键？

用 LangGraph 后，可以把“执行前”和“执行后”的边界放在图节点里明确表达。

## 3. 当前重构做了什么

本分支新增了 LangGraph 编排层，但保留原有业务组件。

新增文件：

```text
workflows/
  __init__.py
  langgraph_erp_workflow.py
```

修改文件：

```text
apis/api_planning_hub.py
app.py
utils/config.py
requirements.txt
```

### 3.1 新增依赖

`requirements.txt` 新增：

```text
langgraph>=1.0.0
```

### 3.2 新增开关

`utils/config.py` 新增：

```python
use_langgraph_workflow = int(os.getenv("use_langgraph_workflow", "1"))
```

默认启用 LangGraph workflow。若环境没有安装 `langgraph`，系统会自动回退旧流程，避免本地开发或面试演示时因为依赖缺失直接不可用。

### 3.3 新的入口行为

`ApiPlanningHub.apis_planning()` 现在变成 LangGraph 优先入口：

```text
use_langgraph_workflow=1 且 langgraph 可用
-> ERPAgentLangGraphWorkflow.run()

否则
-> _legacy_apis_planning()
```

这样做的好处是：

- 面试时可以说核心编排已迁移到 LangGraph。
- 旧链路仍保留为 fallback，降低迁移风险。
- 便于灰度：可以通过环境变量切回旧流程。

### 3.4 用户反馈恢复路径

`process_human_feedback()` 中也接入了：

```text
ERPAgentLangGraphWorkflow.resume_with_human_feedback()
```

当前版本仍复用原有 `api_planning_handle_human_feedback()` 执行确认后的真实工具调用，但会记录：

- `langgraph_resume_requested`
- `langgraph_resume_completed`

这让当前 task-state 模式与 LangGraph 编排层连起来。

## 4. LangGraph 状态设计

当前图状态定义为 `ERPGraphState`：

```python
class ERPGraphState(TypedDict, total=False):
    query: str
    raw_query: str
    task_id: str
    task_desc: str
    task_type: int
    operator_context: Dict[str, Any]
    tool_id: int
    tool_name: str
    operation_id: str
    tool_check_result: Dict[str, Any]
    final_status: str
    next_action: str
    error: str
```

设计原则：

- 图内 state 只保留推进当前任务所需字段。
- 大对象不长期放在 state 里，工具和任务通过 `tool_id` / `task_id` 重新查询。
- 用户上下文和权限上下文用结构化 dict 表达。
- 外部副作用结果仍进入 task、trace 和 memory，而不是塞进一个无限膨胀的 giant dict。

面试表达：

> 我没有把所有上下文都塞进 LangGraph state。state 只承载当前任务推进所需的最小状态，历史消息、summary、trace 和工具定义仍在外部存储里。这样可以避免 checkpoint 膨胀，也方便后续做 state 生命周期管理。

## 5. LangGraph 节点设计

当前图的节点：

```text
load_task
-> classify_task
-> select_tool
-> check_tool
-> persist_decision
-> END
```

### 5.1 load_task

职责：

- 根据 `task_id` 读取任务。
- 加载 `operator_context`。
- 写入 `langgraph_node_completed` trace。

面试点：

> 这个节点不是业务决策，而是把请求上下文转成可执行图状态。它是 user/session/tenant 隔离和权限上下文的入口。

### 5.2 classify_task

职责：

- 判断单工具任务还是多工具任务。
- 写入 `task_type`。
- 对多工具任务生成首个 `task_desc`。

面试点：

> 任务分类不是为了炫规划能力，而是决定后续图的执行方式。单工具任务走一次工具确认，多工具任务在确认执行后继续根据已有 nodes/edges 规划下一步。

### 5.3 select_tool

职责：

- 调用现有 `ApiSelectionHub`。
- 通过 Tool Registry 做工具召回、rerank 和权限过滤。
- 找不到工具时进入 no_tool_found，不允许模型编造工具。

面试点：

> 工具必须来自 Tool Registry。模型不能凭空编 operationId。工具多的时候不能把所有 schema 塞进 prompt，而是先召回候选，再让模型在候选集合里决策。

### 5.4 check_tool

职责：

- 调用 `_tool_check()`。
- 复用现有参数抽取、缺参补全、权限校验、Prompt 注入检查、HallucinationGuard 和 route cross validation。

面试点：

> 这里是 Agent 稳定性的核心。模型选工具只是第一步，真正决定能不能执行的是参数、权限、guardrail 和业务风险。

### 5.5 persist_decision

职责：

- 根据 `_tool_check()` 结果进入不同出口。
- 参数完整：写入 `tool_execution_confirm` HITL gate。
- 缺参：写入 `missing_params_clarify` HITL gate。
- 权限或 guardrail 失败：结束任务。

面试点：

> 这个节点对应 LangGraph 中的人工挂起点。当前为了兼容前端轮询协议，HITL state 持久化在 MongoDB 的 `pending_action` / `pending_payload`，而不是直接把 HTTP 请求卡在图里。

## 6. 当前图结构

```text
START
  |
  v
load_task
  |
  | task exists
  v
classify_task
  |
  v
select_tool
  |
  | tool found
  v
check_tool
  |
  v
persist_decision
  |
  +-- success -> WAIT_CONFIRM(tool_execution_confirm)
  |
  +-- missing params -> WAIT_CONFIRM(missing_params_clarify)
  |
  +-- permission/guardrail blocked -> FINISH
  |
  v
END
```

## 7. HITL 如何讲

当前项目的 HITL 有两类：

### 7.1 缺参澄清

触发条件：

- 工具必填参数缺失。
- 通过上下文或补充工具无法可靠补齐。

状态：

```text
pending_action = "missing_params_clarify"
pending_payload = {
  original_query,
  current_task_desc,
  tool_id,
  known_params,
  missing_params
}
```

### 7.2 工具执行确认

触发条件：

- 参数完整。
- 权限、guardrail、route validation 通过。
- 即将执行真实 ERP API。

状态：

```text
pending_action = "tool_execution_confirm"
pending_payload = {
  graph,
  thread_id,
  tool_id,
  operation_id,
  params,
  task_desc,
  raw_query,
  permission
}
```

面试回答：

> 我们没有让图直接执行写操作。图跑到 human gate 后，会把待确认工具、参数、权限结果和原始请求落到 `pending_payload`，前端通过 task status 展示给用户。用户确认后再 resume，取消则结束任务，修改参数则重新校验。

## 8. 如果面试官问：你用了 LangGraph 的 interrupt 吗？

建议诚实但有技术含量地回答：

> 当前版本采用的是“LangGraph state graph + 外部 task-state HITL bridge”。也就是说，核心编排已经在 LangGraph 的 StateGraph 里，人工挂起点通过 MongoDB 中的 `pending_action` 和 `pending_payload` 持久化，兼容现有前端轮询协议。  
>  
> 这和 LangGraph 原生 interrupt 的语义是一致的：图跑到某个节点需要用户输入，就保存状态并退出。但当前还没有把它替换成原生 `interrupt + durable checkpointer`，原因是老系统已经有 task 状态和前端轮询协议。下一步会把 `task_id` 映射为 LangGraph `thread_id`，再实现 Mongo checkpointer，这样 resume 时就能用原生 graph resume。

不要说：

> 我们已经完整使用了 LangGraph 原生 checkpoint 和 interrupt。

更稳妥的说法：

> 当前已经把核心编排迁到 LangGraph StateGraph，HITL 挂起仍通过现有任务状态桥接；原生 interrupt/checkpointer 是下一阶段标准化目标。

## 9. 如果面试官问：这是不是过度设计？

回答结构：

1. 先承认判断标准。
2. 再说明本项目为什么不是线性三步。
3. 最后说明重构边界。

示例回答：

> 如果只是简单的 LLM -> Tool -> LLM，我也不会上 LangGraph。但这个 ERP 场景里有工具找不到、参数缺失、权限失败、写操作确认、用户取消、用户修改参数、多工具链路继续规划等真实分支。每个分支都要持久化状态并能复盘。  
>  
> 所以我没有把 LangGraph 当成模型调用封装，而是用它表达状态机。原有 ToolManager、PermissionGuard、TraceManager、MemoryManager 都保留，LangGraph 只承载编排层，这样重构范围可控，不会为了框架重写业务代码。

## 10. 如果面试官问：为什么不用 LangChain Agent？

回答：

> LangChain Agent 更适合快速搭建 model + tools 的调用循环。但这个项目的核心不是工具调用本身，而是 ERP 写操作下的状态控制、HITL、权限、缺参澄清和执行链路复盘。  
>  
> 所以我把 LangChain 作为后续可选组件层，比如标准化工具 wrapper、structured output、model middleware；核心 workflow 用 LangGraph 表达。这样更符合业务风险。

## 11. 如果面试官问：checkpoint 怎么设计？

当前实现：

- 任务状态存在 `Task`。
- 挂起状态存在 `pending_action` / `pending_payload`。
- trace 存在 `TraceRecord`。
- session 上下文存在 `SessionMemory` / `SummaryMemory`。

下一阶段设计：

```text
LangGraph thread_id = task_id
MongoCheckpointer:
  thread_id
  graph_state
  node_name
  pending_payload
  created_at
  updated_at
  version
```

重点回答：

> checkpoint 不能只存对话文本，要存当前节点、路由结果、工具 ID、参数、权限校验结果和 pending gate。写操作前后的 checkpoint 要分开，避免 resume 后重复执行外部副作用。

## 12. 如果面试官问：resume 后会不会重复执行工具？

回答：

> 当前图在工具执行前就进入 human gate，确认前不会调用 ERP API。确认后才进入真实调用节点。后续如果使用原生 checkpoint，需要给写操作生成 `operation_id` 或 `idempotency_key`，并在工具调用前后分别记录状态。这样即使 resume 或重试，也可以判断某个副作用是否已经提交过。

可补充：

当前建议新增：

```text
operation_id / idempotency_key
tool_call_status: pending | started | committed | failed
external_request_id
```

## 13. 如果面试官问：state 会不会越来越大？

回答：

> 我刻意没有把全部历史和工具结果都塞进 graph state。state 只保留当前任务推进必需字段，比如 task_id、tool_id、params、pending_action。大对象仍在 MongoDB、trace 和 memory 里。长对话通过 ContextManager 的 recent window 和 summary memory 控制，summary 只作为背景，不作为自动填参的强事实源。

## 14. 如果面试官问：多工具任务怎么处理？

当前策略：

- `classify_task` 判断单工具或多工具。
- 多工具任务先生成首个 task_desc。
- 用户确认并执行一个工具后，原有 `GenerateTaskHub.gen_from_context_task()` 根据 nodes/edges 生成下一步。
- 下一步再次进入 LangGraph / 工具选择 / HITL。

面试表达：

> 多工具任务没有一次性把所有步骤都交给模型执行，而是每一步执行后用真实工具结果更新上下文，再规划下一步。这样可以避免计划一开始错了后面全错，也方便每一步做 HITL 和 trace。

## 15. 如果面试官问：trace 怎么配合 LangGraph？

新增 trace event：

```text
langgraph_workflow_started
langgraph_node_completed
langgraph_human_gate_created
langgraph_workflow_finished
langgraph_resume_requested
langgraph_resume_completed
```

保留原有 event：

```text
operator_context_loaded
tool_selected
params_extracted
missing_params_need_user
permission_check_started
permission_check_passed
permission_check_failed
guardrail_blocked
permission_blocked
tool_invocation_started
tool_invocation_finished
answer_grounding_checked
```

面试表达：

> LangGraph 负责结构化编排，TraceManager 负责业务可观测。排查 bad case 时，我不只看最终答案，而是看图跑到哪个节点、哪个节点的输入输出是什么、是否在工具选择、参数抽取、权限还是最终总结阶段出错。

## 16. 如果面试官问：记忆和 LangGraph state 什么关系？

回答：

> LangGraph state 是当前任务状态，不等于长期记忆。session memory 和 summary memory 由 MemoryManager 管理，ContextManager 在任务开始时构造 bounded context。图内只消费当前 query、recent messages 的摘要结果和必要状态，不负责无限存储历史。这样能避免 checkpoint 膨胀和跨 session 污染。

## 17. 如果面试官问：工具选择是怎么做的？

回答：

> 工具来自 OpenAPI 解析后的 Tool Registry。结构化信息存在 MongoDB，语义向量存在 Milvus。用户 query 先通过向量检索召回候选工具，再通过 reranker 精排，并结合权限过滤，最后由 LLM 在候选工具集合中做选择。LangGraph 的 `select_tool` 节点只负责编排这一步，不把所有工具 schema 塞进 prompt。

## 18. 如果面试官问：安全怎么保证？

可分四层：

1. 工具来源安全：只能调用 Tool Registry 中存在的工具。
2. 参数安全：必填参数、类型、枚举、负数、异常大数量校验。
3. 权限安全：用户 endpoint 权限、tool 权限、区域/租户/客户范围权限。
4. 执行安全：写操作 HITL，确认前不调用真实 ERP API。

面试表达：

> 模型不负责最终安全决策。模型可以参与选择和抽取，但是否允许执行由确定性代码判断。

## 19. 如果面试官问：如何评测 LangGraph 版本？

不要只说“跑 E2E”。

应拆成：

- 节点评测：分类、工具选择、参数抽取、HITL 意图、guardrail。
- 路由评测：缺参是否进入 clarify，写操作是否进入 confirm，权限失败是否 blocked。
- workflow 评测：多工具任务顺序是否正确。
- replay 评测：bad case 修复后是否回归。
- trace 评测：关键节点是否都写 trace event。

可以使用已有：

```text
prompt/evals/runner.py
prompt/evals/integration_runner.py
prompt/evals/integration_datasets/workflows.json
```

后续建议新增：

```text
prompt/evals/datasets/langgraph_routes.json
test/test_workflows/test_langgraph_workflow.py
```

## 20. 面试时可以讲的架构图

```text
Flask API
  |
  | create task_id / trace_id
  v
ContextManager
  |
  | recent messages + summary
  v
ERPAgentLangGraphWorkflow
  |
  +-- load_task
  +-- classify_task
  +-- select_tool
  +-- check_tool
  +-- persist_decision
         |
         +-- WAIT_CONFIRM: missing_params_clarify
         +-- WAIT_CONFIRM: tool_execution_confirm
         +-- FINISH: permission/guardrail/no_tool
  |
  v
Human Feedback Resume
  |
  v
ToolUseHub -> ERP API
  |
  v
ToolSummaryHub -> final answer
  |
  v
TraceManager + MemoryManager + Eval datasets
```

## 21. 当前版本边界

可以说已经做了：

- LangGraph `StateGraph` 编排层。
- `task_id` 到 graph state 的映射。
- 工具选择、参数校验、HITL gate 纳入图节点。
- graph 节点 trace event。
- 旧流程 fallback。
- 反馈恢复路径的 graph resume bridge。

不要说已经完整做了：

- 原生 LangGraph durable checkpointer。
- 原生 `interrupt` / `Command(resume=...)` 全链路。
- LangSmith 线上观测平台。
- 多 Agent 分布式协作。
- 完整 MCP server 化。

后续演进：

```text
1. MongoCheckpointer
2. Native interrupt / resume
3. LangChain Tool wrapper for OpenAPI tools
4. LangSmith tracing
5. LangGraph route eval dataset
6. Idempotency key for write tools
7. Multi-agent supervisor only after workflow complexity真正上升
```

## 22. 简历描述建议

```text
基于 LangGraph 重构 ERP Agent Copilot 编排层，将自然语言到业务 API 的链路建模为显式状态图，覆盖任务分类、工具召回与选择、参数抽取、缺参澄清、权限校验、Guardrail、HITL 确认和 trace 复盘。保留原有 Tool Registry、Memory、PermissionGuard 和 Eval 体系，通过 graph state 管理任务推进状态，通过 pending_action/pending_payload 桥接现有前端轮询式人工确认流程，并保留 legacy workflow fallback 以支持灰度迁移。
```

## 23. 2 分钟面试讲法

> 这个项目原来是一个自研 workflow 的 ERP Agent。用户输入自然语言后，系统会选择 OpenAPI 工具、抽取参数、做权限校验，再通过 HITL 确认后调用 ERP API。  
>  
> 我后来把核心编排层重构成 LangGraph，因为这个场景天然不是线性 LLM 调工具，而是有真实状态和分支：找不到工具、缺参、权限失败、guardrail 拦截、写操作确认、用户取消、用户修改参数、多工具继续规划。  
>  
> 重构时我没有推翻原来的业务模块，而是新增了 `ERPAgentLangGraphWorkflow`，把 `load_task`、`classify_task`、`select_tool`、`check_tool`、`persist_decision` 做成图节点。Tool Registry、PermissionGuard、HallucinationGuard、TraceManager 和 MemoryManager 都复用。这样 LangGraph 负责状态机编排，原有组件负责具体业务判断。  
>  
> HITL 这块当前为了兼容前端轮询协议，还是通过 MongoDB 里的 `pending_action` 和 `pending_payload` 持久化，相当于一个外部 task-state bridge。后续如果要更标准化，可以把 `task_id` 映射成 LangGraph `thread_id`，接 Mongo checkpointer，再使用原生 interrupt/resume。

## 24. 一句话收束

这个项目使用 LangGraph 的坚实理由是：ERP Agent 的难点不是“模型能不能调 API”，而是“调 API 之前、之中、之后的状态和风险如何被控制”。LangGraph 正好用来表达这条有分支、有挂起、有恢复、有副作用边界的状态机。

