# 项目借鉴的 LangGraph 思想与技术

本文档整理当前 ERP Agent Copilot 在一系列改造后，借鉴了哪些 LangGraph 的思想、技术和工程实践。

需要先明确边界：

```text
当前项目已经引入 LangGraph StateGraph 作为核心编排层。
但当前项目还没有完整使用 LangGraph 原生 interrupt + checkpointer durable execution。
HITL 挂起和恢复仍然通过现有 Mongo task-state bridge 实现。
```

所以面试表达要稳：

```text
可以说：核心编排已经借鉴并落地 LangGraph StateGraph。
不要说：所有工作流都已经完整迁移到 LangGraph 原生 durable workflow。
```

## 1. 总体借鉴结论

这个项目不是简单把 LLM 调用包进 LangGraph，而是借鉴了 LangGraph 对 Agent 的核心理解：

```text
Agent 不是一段无限循环的 prompt。
Agent 应该是一个有状态、有分支、可中断、可恢复、可观测的工作流。
```

当前项目中的 ERP 工具调用链路天然符合这个模型：

```text
用户请求
-> 上下文构造
-> 任务分类
-> 工具召回与选择
-> 参数抽取
-> 权限校验
-> Guardrail
-> HITL 人工确认
-> 工具执行
-> 结果写回
-> Trace / Eval 复盘
```

这些步骤不是线性脚本，而是有大量分支：

```text
找不到工具 -> no_tool_found
缺少参数 -> missing_params_clarify
无权限 -> permission_blocked
参数异常 -> guardrail_blocked
需要确认 -> tool_execution_confirm
用户取消 -> task_finished
用户修改参数 -> 重新校验
确认执行 -> tool_runtime
```

这就是为什么项目适合借鉴 LangGraph，而不是只写一个 ReAct prompt。

## 2. StateGraph：把业务流程显式建模为状态图

LangGraph 最核心的思想是用 `StateGraph` 表达流程。

当前项目已经落地：

```text
workflows/langgraph_erp_workflow.py
```

当前图结构：

```text
START
-> load_task
-> classify_task
-> select_tool
-> check_tool
-> persist_decision
-> END
```

对应代码使用了：

```python
builder = StateGraph(ERPGraphState)
builder.add_node("load_task", self._load_task)
builder.add_node("classify_task", self._classify_task)
builder.add_node("select_tool", self._select_tool)
builder.add_node("check_tool", self._check_tool)
builder.add_node("persist_decision", self._persist_decision)
builder.add_edge(START, "load_task")
builder.add_conditional_edges(...)
builder.add_edge("persist_decision", END)
```

借鉴点：

```text
把原来隐式的 if/else 流程显式化成图。
每个业务阶段变成一个 node。
阶段之间的跳转变成 edge 或 conditional edge。
失败、阻断、挂起也成为明确路径，而不是散落在代码里的异常返回。
```

面试表达：

```text
我把 ERP Agent 的工具调用链路从线性过程改成了显式状态图。这样工具选择、参数校验、权限阻断、HITL 确认这些关键节点都可以被 trace 和 eval 观测，而不是只看最终回答。
```

## 3. Graph State：用结构化状态承载任务推进

LangGraph 强调 state 是图运行时的核心载体。

当前项目定义了 `ERPGraphState`：

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

这个 state 不是聊天历史，也不是长期记忆。

它只保存当前任务推进需要的最小字段：

```text
task_id：当前任务
query/raw_query：当前请求
task_type/task_desc：任务分类结果
operator_context：当前操作者权限上下文
tool_id/tool_name/operation_id：选中的工具
tool_check_result：参数、权限、guardrail 校验结果
final_status/next_action/error：图状态和分支控制
```

借鉴点：

```text
用结构化 state 代替散落的局部变量。
用 state 传递节点之间的业务结果。
控制 state 的体积，不把所有历史、工具结果和大对象塞进图里。
```

当前项目特别强调：

```text
LangGraph state 只负责当前任务状态。
SessionMemory 负责会话历史。
SummaryMemory 负责上下文压缩。
TraceRecord 负责审计和评测。
Tool Registry 负责工具定义。
User Preference Memory 负责显式长期偏好。
```

这种分层符合 LangGraph 的工程思想：图状态要小而清晰，大对象放外部存储，通过 id 关联。

## 4. Node：把每个业务阶段拆成可观测节点

当前项目的节点不是随便拆的，而是按 ERP Agent 的风险链路拆分：

```text
load_task
加载任务、用户、session、operator_context。

classify_task
判断单工具任务还是多工具任务。

select_tool
通过工具 RAG / rerank / 权限过滤选择候选工具。

check_tool
执行参数抽取、缺参检查、权限校验、guardrail 检查。

persist_decision
根据校验结果决定结束、阻断、缺参澄清或创建 HITL gate。
```

借鉴点：

```text
每个 node 有单一职责。
每个 node 可以单独 trace。
每个 node 可以单独评测。
node 内部复用已有业务模块，而不是把业务逻辑重写进 LangGraph。
```

这体现了一个重要工程判断：

```text
LangGraph 负责编排。
ToolManager、PermissionGuard、MemoryManager、TraceManager 等模块继续负责具体业务能力。
```

面试表达：

```text
我没有为了上 LangGraph 把业务模块推倒重写，而是把已有模块放到不同 node 里。LangGraph 负责状态流转和分支控制，原有组件继续做工具检索、参数校验、权限和 trace。
```

## 5. Edges / Conditional Edges：把分支从 prompt 中拿出来

LangGraph 的一个重要价值是条件边。

当前项目已经使用：

```python
builder.add_conditional_edges(
    "load_task",
    self._route_after_load,
    {"classify_task": "classify_task", "end": END},
)

builder.add_conditional_edges(
    "select_tool",
    self._route_after_select,
    {"check_tool": "check_tool", "end": END},
)
```

借鉴点：

```text
不是让模型自己决定所有分支。
工程代码根据结构化状态决定下一步。
```

例如：

```text
load_task 找不到任务 -> END
select_tool 找不到工具 -> END
select_tool 成功 -> check_tool
check_tool 结果完整 -> persist_decision
persist_decision 根据结果创建 gate 或阻断
```

这比单纯 prompt 更可靠，因为：

```text
流程分支可控。
异常路径明确。
不用依赖模型“记得自己该停”。
测试时可以验证 expected node path。
```

## 6. HITL：借鉴 interrupt / resume 语义

LangGraph 支持 human-in-the-loop 和 interrupt/resume 这类机制。

当前项目没有完整使用原生 interrupt + checkpointer，但借鉴了它的语义：

```text
图运行到某个节点
-> 发现需要人类输入
-> 持久化当前状态
-> 返回前端等待用户
-> 用户确认、取消或补充参数
-> resume 后继续处理
```

当前项目用现有 Mongo task-state bridge 实现：

```text
pending_action
pending_payload
TASK_STATUS_WAIT_CONFIRM
```

当前支持两个主要 gate：

```text
missing_params_clarify
缺少必要参数，等待用户补充。

tool_execution_confirm
工具执行前确认，尤其是写操作和高风险动作。
```

对应 trace：

```text
langgraph_human_gate_created
langgraph_resume_requested
langgraph_resume_completed
```

需要明确边界：

```text
当前是 LangGraph state graph + 外部 task-state HITL bridge。
不是完整原生 interrupt + checkpointer。
```

面试表达：

```text
当前为了兼容已有前端轮询协议，HITL 状态还是落在 Mongo 的 pending_action 和 pending_payload 里；但语义上已经和 LangGraph interrupt 一致：图到达 human gate 后保存状态并退出，用户反馈后 resume。下一步可以把 task_id 映射为 LangGraph thread_id，接 Mongo checkpointer，再替换成原生 interrupt/resume。
```

## 7. Durable Execution 思想：状态持久化和恢复

LangGraph 很强调 long-running agents 和 durable execution。

当前项目借鉴了它的思想，但实现方式是分阶段的。

当前已有：

```text
Task 持久化：任务状态、pending_action、pending_payload。
Trace 持久化：每个关键节点的事件。
Memory 持久化：session history、conversation summary。
Tool 持久化：工具定义、operation_id、schema。
Permission context：当前操作者权限上下文。
```

当前还没有：

```text
LangGraph 原生 checkpointer。
thread_id -> checkpoint 的完整映射。
Command(resume=...) 全链路。
写操作前后原生 checkpoint 分离。
```

借鉴点：

```text
不把 Agent 当成一次 HTTP 请求。
把任务视为可挂起、可恢复、可审计的长流程。
```

未来演进：

```text
LangGraph thread_id = task_id
MongoCheckpointer 保存 graph state
interrupt 创建 human gate
Command(resume=human_feedback) 恢复图
写操作使用 operation_id / idempotency_key 防重复提交
```

## 8. 副作用边界：确认前不执行真实工具

ERP Agent 和普通聊天最大的区别是会执行真实业务动作。

LangGraph 的状态图思想帮助项目明确副作用边界：

```text
select_tool：只选择工具，不执行。
check_tool：只抽参、校验、权限判断，不执行。
persist_decision：创建 HITL gate，不执行。
用户确认后：才进入真实工具调用。
```

当前项目中：

```text
persist_decision
-> _persist_tool_execution_gate
-> pending_action="tool_execution_confirm"
-> 等待用户确认
```

借鉴点：

```text
把“准备执行”和“已经执行”分成不同状态。
确认前绝不调用有副作用的 ERP API。
resume 后也要防止重复执行。
```

这也是为什么后续需要：

```text
operation_id
idempotency_key
tool_execution_started / tool_execution_finished trace
checkpoint before side effect
checkpoint after side effect
```

## 9. Guardrail / Permission Gate 节点化

LangGraph 的价值不是替代权限系统，而是把权限和 guardrail 放进清晰的执行路径。

当前项目在 `check_tool` 节点中统一触发：

```text
参数抽取
缺参判断
参数合法性校验
权限校验
hallucination guardrail
风险判断
```

在 `persist_decision` 中根据结果分流：

```text
TASK_SUCCESS_CODE -> 创建 tool_execution_confirm gate
missing_param_need_user -> 创建 missing_params_clarify gate
permission_denied -> permission_blocked
guardrail blocked -> guardrail_blocked
```

借鉴点：

```text
安全不是 prompt 的一句提示词。
安全应该是 workflow 中明确的 gate。
```

这让面试时可以解释：

```text
模型可以提出工具调用，但不能直接越过系统 gate。
工具执行前必须通过参数、权限、guardrail 和 HITL。
```

## 10. Trace：把图执行路径变成可评测对象

LangGraph 的一个重要工程价值是可观测的状态流。

当前项目配合 TraceManager 记录：

```text
langgraph_node_completed
operator_context_loaded
tool_selected
params_extracted
no_tool_found
missing_params_need_user
langgraph_human_gate_created
permission_blocked
guardrail_blocked
langgraph_resume_requested
langgraph_resume_completed
```

借鉴点：

```text
不只看 final answer。
要看节点路径、每个节点输入输出、分支原因和挂起状态。
```

这直接服务 eval：

```text
expected_node_path
actual_node_path
expected_pending_action
actual_pending_action
forbidden_nodes_before_confirm
resume_path_accuracy
permission_block_accuracy
guardrail_block_accuracy
```

面试表达：

```text
LangGraph 让我们更容易把 Agent 的中间决策暴露出来。线上 bad case 不只是看回答错没错，而是看图跑到哪个节点、在哪个 gate 被阻断、是否在确认前调用了工具。
```

## 11. Memory 与 Graph State 分离

经过后续上下文和记忆改造后，项目也借鉴了 LangGraph 的一个重要工程思想：

```text
Graph state 不等于全部上下文。
Graph state 不等于长期记忆。
Graph state 不应该无限膨胀。
```

当前项目的上下文与记忆分层：

```text
ERPGraphState
当前任务推进状态。

SessionMemory
当前 session 的完整历史。

SummaryMemory
长对话 compaction 的 conversation_summary。

User Preference Memory
用户显式配置的长期偏好，作为稳定 prompt 前缀。

TraceRecord
执行路径、节点、工具调用、HITL、异常和评测证据。
```

借鉴点：

```text
图只携带当前任务必要状态。
历史、摘要、长期偏好、工具结果、trace 都放外部存储。
节点需要时通过 id 查询。
```

这样做的好处：

```text
避免 graph state 过大。
避免 checkpoint 膨胀。
避免跨 session 污染。
便于单独评测 memory、summary、tool trace。
```

## 12. Context Engineering：调用前构造有边界的上下文

虽然上下文压缩不是 LangGraph 独有能力，但 LangGraph 的 stateful agent 思想强调：

```text
每个节点应该拿到当前阶段需要的上下文，而不是无限制塞全部历史。
```

当前项目在后续设计中形成：

```text
System Prompt
+ Safety / Tool Rules
+ User Preference Memory
+ Conversation Summary
+ Recent Working Set
+ Retrieved Evidence / Tool Result Summary
+ Current User Query
```

这和 LangGraph 的分层状态思想一致：

```text
稳定规则放前缀。
长期偏好独立于 summary。
summary 只做 session compaction。
recent working set 按 token budget 保留。
工具结果以 result_id 和摘要进入上下文。
```

图节点不需要直接持有全部上下文，而是消费 ContextManager 组装后的 bounded context。

## 13. Fallback / 灰度迁移思想

当前项目没有一次性删除旧流程，而是增加了开关：

```text
use_langgraph_workflow
```

入口行为：

```text
use_langgraph_workflow=1 且 langgraph 可用
-> ERPAgentLangGraphWorkflow.run()

否则
-> legacy workflow
```

借鉴点：

```text
Agent workflow 重构不能一次性切断旧链路。
要有 fallback、灰度和回滚能力。
```

这在企业项目里非常重要，因为 ERP 工具调用链路有真实业务风险。

## 14. Eval：从答案评测升级为路径评测

LangGraph 的 graph 结构让评测不再只比较最终输出。

当前项目可以评测：

```text
节点是否按预期执行。
是否正确进入 no_tool_found。
缺参是否进入 missing_params_clarify。
写操作是否进入 tool_execution_confirm。
确认前是否禁止 tool runtime。
权限不足是否 permission_blocked。
resume 后是否继续正确路径。
```

推荐 case 结构：

```json
{
  "query": "帮我把 A15 的安全库存改成 150",
  "expected_node_path": [
    "load_task",
    "classify_task",
    "select_tool",
    "check_tool",
    "persist_decision"
  ],
  "expected_pending_action": "tool_execution_confirm",
  "forbidden_events_before_confirm": ["tool_invocation_started"]
}
```

借鉴点：

```text
Agent eval 要看 graph trace。
尤其是 ERP 这种有副作用的系统，中间路径比最终话术更重要。
```

## 15. 和 LangChain 的关系

当前项目也借鉴了 LangGraph 官方生态中的分层思想：

```text
LangChain 更适合作为模型、工具、retriever、structured output 等组件层。
LangGraph 更适合作为有状态 workflow / agent runtime。
```

当前项目没有强行把所有能力都迁到 LangChain，而是：

```text
自研 Tool Registry 继续保留。
自研参数抽取、权限、trace、memory 继续保留。
LangGraph 只做 orchestration layer。
```

这是一种轻量接入方式。

面试表达：

```text
不是 LangChain 做不到，而是这个 ERP 项目的主矛盾不是模型工具封装，而是状态控制、HITL、权限、resume 和审计。所以我把 LangGraph 放在编排层，LangChain 作为后续组件层可选。
```

## 16. 当前已实现、借鉴但未完全实现、未来演进

### 16.1 当前已实现

```text
StateGraph 编排层。
ERPGraphState 状态对象。
load_task / classify_task / select_tool / check_tool / persist_decision 节点。
START / END / add_edge / add_conditional_edges。
LangGraph 优先入口。
legacy fallback。
HITL task-state bridge。
langgraph_node_completed trace。
langgraph_resume_requested / completed trace。
工具选择、权限、guardrail 和 HITL 的图式编排。
```

### 16.2 借鉴但未完全原生实现

```text
interrupt / resume 语义：当前用 pending_action / pending_payload 桥接。
durable execution：当前用 Mongo task state + trace，未接 LangGraph checkpointer。
thread_id：当前可用 task_id 映射，但还没有完整 checkpoint 存储。
path eval：已有 trace 基础，仍可继续补标准化 route eval dataset。
```

### 16.3 未来演进

```text
1. 引入 LangGraph 原生 checkpointer。
2. task_id 映射为 thread_id。
3. 使用 interrupt 创建 HITL gate。
4. 使用 Command(resume=...) 恢复图。
5. 写操作增加 idempotency_key。
6. 将 Knowledge QA、Live Data Query、Write Action 拆成子图。
7. 增加 graph route eval 数据集。
8. 将 token budget compaction 和 prompt cache trace 接入 graph context node。
```

## 17. 一句话面试总结

可以这样讲：

```text
这个项目借鉴 LangGraph 的核心不是“套了一个框架”，而是把 ERP Agent 从一段 prompt 驱动的工具调用，升级为有状态的业务工作流。我们用 StateGraph 显式表达任务分类、工具选择、参数校验、权限校验、HITL 和阻断路径；用 graph state 保存当前任务推进状态；用 pending_action/pending_payload 桥接人工确认；用 trace 记录节点路径做 eval。当前还没有完整替换成原生 interrupt + checkpointer，但整体已经按照 LangGraph 的 stateful、interruptible、observable workflow 思想设计。
```

更短版本：

```text
LangGraph 在这个项目里负责“状态和流程”，不是负责“让模型更聪明”。它解决的是 ERP 工具调用中的分支、挂起、恢复、权限、HITL 和可审计问题。
```

## 18. 参考资料

- LangGraph Overview: https://docs.langchain.com/oss/python/langgraph/overview
- LangGraph Graph API: https://docs.langchain.com/oss/python/langgraph/graph-api
- LangGraph Persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph Human-in-the-loop / Interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangGraph Durable Execution: https://docs.langchain.com/oss/python/langgraph/durable-execution
