# 长对话压缩与 Summary 简化方案

本文档重新整理当前项目中的 summary 设计。

这次的核心调整是：不再把 summary 包装成复杂的记忆工程能力，也不再强调 `pinned_facts`、`confirmed_facts`、`entities`、`relations`、`active_focus` 这一类结构化状态。当前阶段只把 summary 定位为 **长对话上下文压缩能力**。

也就是说，summary 的主要目标是：

```text
解决长对话 token 过长的问题，让模型在有限上下文窗口里仍然能理解前文大意。
```

它不是：

```text
不是参数事实库
不是长期记忆系统
不是权限系统
不是幻觉治理的核心模块
不是工具参数自动补全的强依据
```

这个设计更接近 OpenClaw 的 Compaction 思路：保留完整历史，压缩较早上下文，最近消息仍然原文进入模型。

## 0. 当前代码落地与目标改造方向

当前工程已经把 summary 从“每轮覆盖最近目标”改成 OpenClaw Compaction 风格的会话压缩。

代码位置：

```text
entity/memory_entity.py
memory/memory_manager.py
memory/context_manager.py
app.py
```

当前实现方式：

```text
1. 所有用户和系统可见消息仍然完整写入 SessionMemory。
2. ContextManager 每次构造上下文时，至少保留最近 6 条原始消息。
3. 当 session 消息数超过 recent_window 时，MemoryManager 把掉出最近窗口的旧消息合并进 SummaryMemory.summary。
4. SummaryMemory 记录 compacted_message_count、recent_window、max_summary_chars。
5. 主路径调用 LLM summarizer，把旧 summary 和掉出窗口的消息压缩成新的 conversation_summary。
6. 如果 LLM 调用失败或返回空，降级到规则式 extractive fallback，保证主流程不中断。
7. 任务结束后写入 summary_compacted trace event，方便后续评测和排查。
```

注意：上面是当前代码现状。它的触发条件仍然是 `total_messages - recent_window > compacted_message_count`，也就是按消息条数判断，而不是按 token budget 判断。

后续目标改造方向是：**从“按消息条数触发压缩”改为“按 prompt token 压力触发压缩”**。也就是说，系统不再因为超过固定轮数就压缩，而是先估算即将发送给模型的 prompt token 数，只有当 prompt 接近上下文窗口阈值时才触发 compaction。

目标设计更接近 OpenClaw 风格的 compaction：

```text
完整历史持久化保存
-> 每次调用模型前估算 prompt token
-> token 压力超过阈值时压缩较早上下文
-> 最近工作集仍然原文保留
-> 压缩后的 summary + 最近原文共同进入模型
```

当前压缩输入是：

```text
旧 conversation_summary
+ 新掉出 recent window 的较早消息
-> 新 conversation_summary
```

当前压缩输出是自然语言摘要，不输出 JSON facts，不维护 `pinned_facts`、`entities`、`relations` 等复杂状态。

触发时机：

```text
任务最终回答写入 SessionMemory 后
-> ContextManager.update_after_turn
-> MemoryManager.compact_session_summary
-> 如果 total_messages - recent_window > compacted_message_count，则触发压缩
```

也就是说，不是每来一条消息都调用 LLM，而是只有存在“已经掉出最近窗口、且还没有压缩过”的旧消息时才压缩。这样能降低调用成本，也能避免 summary 频繁漂移。

当前 LLM prompt 位于：

```text
prompt/prompt_registry/conversation_summary_compaction/v1.yaml
```

prompt 约束：

```text
保留任务背景、已完成步骤、未完成事项、用户明确约束、HITL 状态和必要工具结果。
删除寒暄、重复内容和无关解释。
不编造输入中没有的信息。
不把临时参数写成长期偏好。
不声称用户已确认，除非输入中明确出现确认。
summary 只作为上下文背景，不是工具参数事实库。
```

失败处理：

```text
LLM summarizer 成功 -> 使用 LLM conversation_summary
LLM summarizer 失败或返回空 -> 使用 deterministic extractive fallback
无新增待压缩消息 -> 不更新 summary
```

fallback 的作用不是追求最佳摘要，而是保证系统不会因为 summarizer 异常影响工具调用主流程。

## 1. 为什么要简化

之前讨论过一种较复杂的方案：

```text
summary_state
confirmed_facts
active_focus
entities
relations
pending_questions
source_span
fact_status
expires_at
```

这类设计本身没有错。在非常成熟的 Agent 系统里，它确实可以用于：

```text
指代消解
参数补全
上下文事实追踪
跨轮任务状态维护
幻觉治理辅助
```

但对于当前项目来说，它会带来明显成本。

第一，工程实现成本会上升。

系统不仅要生成摘要，还要维护结构化实体、事实来源、事实状态、更新时间、冲突处理和过期策略。

第二，评测成本会上升。

一旦 summary_state 参与工具参数补全，就必须评测：

```text
事实是否抽取正确
事实是否来自真实上下文
事实是否过期
多个候选时是否误选
当前 query 和历史事实冲突时是否以当前 query 为准
LLM summary 是否产生幻觉
被错误 summary 补出来的参数是否会触发错误工具调用
```

第三，面试解释成本会上升。

如果项目主线是 ERP Agent、RAG 工具选择、HITL、权限控制和集成评测，那么 summary_state 不应该抢主线。否则面试官很容易追问：

```text
事实怎么抽取？
怎么证明事实可靠？
怎么处理事实冲突？
怎么评测事实补全？
怎么避免 summary 幻觉污染工具参数？
```

如果这些能力没有完整代码和评测支撑，就会显得包装过度。

所以当前更合理的判断是：

```text
当前项目先只做上下文压缩。
不要把 summary 设计成复杂记忆系统。
幻觉治理主要交给工具选择、参数校验、权限校验、HITL、循环检测和评测体系。
```

## 2. 最终定位

当前项目的 summary 只承担一个职责：

```text
把较早的多轮对话压缩成一段较短的 conversation_summary。
```

后续构造 LLM 输入时使用：

```text
system prompt
+ conversation_summary
+ recent_messages
+ current_user_query
```

其中：

```text
conversation_summary
较早历史的压缩摘要，只帮助模型理解对话背景。

recent_messages
最近 N 轮原始消息，保留完整表达、工具调用和工具结果。

current_user_query
当前用户输入，是本轮意图识别和参数抽取的最高优先级来源。
```

完整历史仍然要保存，但不必每轮全部塞给模型。

## 3. 与 OpenClaw Compaction 的关系

OpenClaw 的 Compaction 主要解决上下文窗口管理问题：

```text
完整长对话太长
-> 压缩较早消息
-> 保留最近消息原文
-> 下一轮把压缩摘要和最近窗口一起发给模型
```

当前项目可以采用类似思路。

需要注意的是，Compaction 不是长期记忆本身，也不是工具参数事实库。它只是把长上下文压短，让模型还能看懂前文发生了什么。

当前项目中的 summary 可以理解为：

```text
面向当前 session 的 conversation compaction。
```

不建议当前阶段扩展为：

```text
跨 session 长期记忆
自动事实库
用户偏好库
实体关系图谱
```

这些可以作为后续增强方向，但不是当前版本的主线。

## 4. 上下文分层

简化后，项目只需要维护四层上下文。

### 4.1 full_message_history

完整历史消息。

保存内容包括：

```text
用户输入
模型输出
工具选择结果
参数抽取结果
工具执行结果
HITL 确认记录
异常和降级记录
```

作用：

```text
用于审计
用于 debug
用于离线评测
用于生成或更新 summary
用于复盘 bad case
```

关键点：

```text
完整历史不等于每轮都进入 prompt。
```

它是系统记录，不是每轮模型上下文。

### 4.1.1 短期记忆、Task、Trace 和 Graph State 的区别

这里容易混淆，需要明确当前代码里的真实存储落点。

项目里所谓“短期记忆”主要不是 LangGraph node 信息，也不是 graph state，而是 MongoDB 中的 `SessionMemory`。

当前可以分成四类：

```text
SessionMemory
保存用户和助手在当前 session 中说过什么。

Task
保存当前任务状态、HITL 挂起状态和前端展示需要的 nodes/edges。

TraceRecord
保存工具选择、参数抽取、权限校验、HITL、工具调用、异常和 LangGraph node 执行事件。

LangGraph State
只保存本次图执行时节点之间传递的临时任务状态。
```

#### SessionMemory：真正会话短期记忆

`SessionMemory` 按 `user_id + session_id` 隔离，字段包括：

```text
user_id
session_id
task_id
role
content
message_type
metadata
created_at
```

它主要保存：

```text
用户原始请求：role=user, message_type=user_query
助手最终回答：role=assistant, message_type=system_output
用户补参反馈：role=user, message_type=missing_params_feedback
```

所以 `SessionMemory` 更像真正的聊天短期记忆：

```text
用户问了什么
系统最终回答了什么
用户在 HITL / 补参阶段补充了什么
```

`ContextManager.build_context_state()` 中的 `recent_messages`，主要就是从这里读取。

#### Task：当前任务和 HITL 挂起状态

HITL 当前等待状态不主要存在 `SessionMemory`，而是存在 `Task` 中。

关键字段：

```text
pending_action
pending_payload
curr_tool_id
curr_tool_param
system_output
nodes
edges
graph_title
```

例如：

```text
pending_action = missing_params_clarify
pending_action = tool_execution_confirm
```

`pending_payload` 会保存当前等待用户处理所需的结构化信息：

```text
tool_id
tool_name
operation_id
known_params
missing_params
params
raw_query
permission
```

因此：

```text
SessionMemory 记录“用户和系统说了什么”。
Task 记录“当前任务卡在哪一步、等用户做什么”。
```

#### TraceRecord：过程记忆和评测证据

工具选择、参数抽取、权限校验、工具调用过程和 LangGraph 节点执行过程，主要写入 `TraceRecord.events`。

典型事件包括：

```text
tool_selected
params_extracted
permission_check_started
permission_check_passed
human_feedback_intent
tool_invocation_started
tool_invocation_finished
langgraph_node_completed
langgraph_human_gate_created
langgraph_resume_requested
langgraph_resume_completed
summary_compacted
```

它的主要用途是：

```text
debug
集成测试
指标计算
bad case 复盘
面试展示 trace 链路
```

默认情况下，`TraceRecord.events` 不会原样作为短期记忆注入 prompt。原因是 trace 内容通常很细、很长，而且包含大量过程信息。需要进入上下文时，应该先整理成：

```text
tool_result_summary
result_id
必要字段
```

而不是把完整 trace 直接塞给模型。

#### LangGraph State：临时运行状态，不是记忆库

LangGraph node 例如：

```text
load_task
classify_task
select_tool
check_tool
persist_decision
```

只是执行阶段。

`ERPGraphState` 只保存当前图执行需要的临时状态：

```text
query
task_id
task_desc
tool_id
tool_check_result
next_action
error
```

当前项目还没有接 LangGraph 原生 checkpointer，所以 graph state 不承担长期保存记忆的职责。

node 执行过程中产生的重要信息，会写到：

```text
Task
TraceRecord
SessionMemory
```

#### 一句话区分

```text
SessionMemory 是“用户和助手说过什么”。
Task 是“当前任务卡在哪一步”。
TraceRecord 是“系统中间到底做了什么”。
LangGraph State 是“这一次图执行时节点之间传什么”。
```

所以当文档里说“上下文工程注入短期记忆”时，严格来说主要注入的是：

```text
SessionMemory 里的 recent_messages
+ SummaryMemory 里的 conversation_summary
```

而不是把所有 node 信息、TraceRecord 事件和工具调用细节都注入 prompt。

### 4.2 conversation_summary

压缩摘要。

它保存较早历史的大意，例如：

```text
用户前面主要在处理华东区供应商对账问题。
已查询过 A 供应商和 B 供应商的上月对账单。
用户要求发送邮件前必须进行人工确认。
最近讨论重点是 B 供应商的对账差异。
```

作用：

```text
减少 token
帮助模型理解前文
避免完全丢失早期对话背景
```

边界：

```text
不能把它当作唯一事实来源直接补工具参数。
不能因为 summary 中出现某个 ID，就直接执行高风险工具。
不能用 summary 覆盖当前用户 query。
```

### 4.3 recent_messages

最近窗口。

例如保留最近 6 到 10 轮原始消息，或者按 token 预算保留最近 3000 到 6000 tokens。

作用：

```text
保留用户最近真实表达
保留最近工具调用和结果
保留当前任务的直接上下文
支持简单指代消解
```

当前项目中，工具参数抽取应该主要依赖：

```text
current_user_query
+ recent_messages
```

而不是依赖较早的 conversation_summary。

### 4.4 current_user_query

当前用户输入。

这是本轮最重要的信号。

原则：

```text
当前 query 明确说出的参数优先级最高。
当前 query 与历史 summary 冲突时，以当前 query 为准。
当前 query 表达不完整时，只能结合 recent_messages 做有限补全。
如果 recent_messages 也无法唯一确定，就澄清或进入 HITL。
```

## 5. 推荐的数据结构

不再设计复杂 `summary_state`。

推荐只保存一个轻量对象：

```json
{
  "conversation_summary": "用户前面主要在处理华东区供应商对账。已查询过 A 供应商和 B 供应商的上月对账单，当前重点是 B 供应商的差异项。用户要求发送邮件前必须人工确认。",
  "covered_message_count": 18,
  "last_compacted_message_id": "msg_018",
  "updated_at": "2026-06-10T10:30:00+08:00"
}
```

字段说明：

```text
conversation_summary
压缩后的文本摘要。

covered_message_count
摘要已经覆盖了多少条历史消息。

last_compacted_message_id
摘要最后覆盖到哪条消息，避免重复压缩或漏压缩。

updated_at
最近更新时间。
```

这几个字段已经足够支持上下文压缩，不需要维护复杂事实结构。

## 6. Prompt 组装方式

每次调用大模型时，可以按照下面方式组织输入：

```text
System:
你是一个 ERP Agent，需要根据用户请求判断是否调用业务工具。
必须遵守权限校验、参数校验和 HITL 流程。

Conversation Summary:
{conversation_summary}

Recent Messages:
{recent_messages}

Current User Query:
{current_user_query}
```

注意：

```text
Conversation Summary 只是背景。
Recent Messages 和 Current User Query 才是当前任务判断的主要依据。
```

建议在 prompt 中明确写出：

```text
摘要可能不完整，不能仅凭摘要中的信息执行工具调用。
涉及具体工具参数时，优先使用当前用户输入和最近原始消息。
如果无法唯一确定参数，应向用户澄清或进入人工确认。
```

## 7. Summary 更新时机

不建议每增加一条 message 就立刻更新 summary。

原因是：

```text
每轮都更新会增加 LLM 调用成本。
短对话没必要压缩。
频繁更新会增加 summary 漂移风险。
```

推荐改造成基于 token budget 的触发条件：

```text
1. 构造候选 prompt。
2. 估算 system prompt、工具描述、conversation_summary、recent_messages、RAG 结果、当前 query 的总 token。
3. 如果预计 prompt token <= soft_budget，则不压缩。
4. 如果预计 prompt token > soft_budget，则触发 compaction。
5. 如果预计 prompt token > hard_budget，则必须压缩；压缩后仍超限时，继续压缩工具结果、减少检索片段或保留更小的最近工作集。
```

推荐做法：

```text
不再按固定 N 轮保留原文。
改为按 token budget 保留最近工作集。
较早且还没有压缩过的消息进入 conversation_summary。
当前用户 query、最近 HITL 状态、最近工具结果摘要必须优先保留。
```

例如：

```text
模型上下文窗口：32k tokens
预留输出：4k tokens
系统 prompt + 工具描述：6k tokens
conversation_summary：1.5k tokens
可用于最近消息和检索结果的预算：约 20k tokens

如果候选 prompt 预计达到 24k tokens 以上，就触发 compaction。
系统从最近消息开始向前累计 token，保留最近工作集。
更早的消息压缩进 conversation_summary。
```

下一次触发时：

```text
旧 summary + 新增的较早消息 -> 新 summary
继续按 token budget 保留最近工作集
```

## 8. Summary 生成 Prompt

可以使用一个独立 prompt 让 LLM 做压缩。

示例：

```text
你负责压缩 ERP Agent 的历史对话。

目标：
1. 保留用户当前任务背景。
2. 保留已经讨论过的重要业务对象名称，例如产品、供应商、订单、客户、区域。
3. 保留用户明确提出的约束，例如必须确认、不要发送邮件、只查询不修改。
4. 保留已经发生过的重要工具结果摘要。
5. 删除寒暄、重复表达、无关闲聊和过期中间过程。

严格要求：
1. 不要编造对话中没有出现的信息。
2. 不要把不确定内容写成确定事实。
3. 不要生成工具参数 JSON。
4. 不要替用户做新的业务决策。
5. 如果历史中存在多个候选对象，只说明存在多个候选，不要擅自指定当前对象。

输入：
旧摘要：
{old_summary}

需要压缩的历史消息：
{messages_to_compact}

输出：
一段 200 到 500 字的中文摘要。
```

输出示例：

```text
用户前面主要在处理华东区供应商对账问题。系统已查询过 A 供应商和 B 供应商的上月对账单，并返回两者均存在未核销差异项。用户曾明确要求发送邮件前需要人工确认。目前最近讨论集中在 B 供应商的差异项，但历史中同时存在 A、B 两个供应商，若用户后续只说“给它发邮件”，需要结合最近原文判断；如无法唯一确定，应先澄清。
```

## 9. 工具参数抽取边界

这是简化方案里最重要的边界。

summary 不能直接变成工具参数来源。

推荐参数来源优先级：

```text
1. 当前用户 query 中明确出现的参数
2. 当前页面上下文中明确存在的参数
3. 最近几轮原始消息中可以唯一确定的参数
4. 工具 schema 的默认值或系统允许注入的隐式上下文，例如当前时间、当前登录用户、tenant_id
5. 仍不明确时，澄清或进入 HITL
```

不推荐：

```text
仅仅因为 conversation_summary 里出现过某个供应商，就直接把它作为 supplier_id 调用发邮件工具。
仅仅因为 summary 里写过某个产品，就直接创建采购单。
仅仅因为 summary 里提到上个月，就跳过日期确认执行写操作。
```

可以接受：

```text
summary 帮助模型知道前面聊的是供应商对账。
recent_messages 里明确只有 B 供应商是最近讨论对象。
当前用户说“把刚才那个供应商的对账单发给财务”。
系统结合 recent_messages 抽取 B 供应商，并在执行前走 HITL。
```

## 10. 例子一：普通长对话压缩

历史对话：

```text
用户：帮我查一下苹果手机 A15 的库存。
系统：库存 120。
用户：再查一下蓝牙耳机 E7。
系统：库存 20。
用户：查一下 E7 的供应商。
系统：供应商是华东电子。
用户：再看一下上个月华东电子的对账单。
系统：存在 2 条差异。
```

如果每轮都把这些原文塞给模型，后续 token 会越来越多。

压缩后：

```text
用户前面查询过苹果手机 A15 和蓝牙耳机 E7 的库存，并进一步查询了蓝牙耳机 E7 的供应商。系统返回 E7 的供应商是华东电子。随后用户查询了上个月华东电子的对账单，结果存在 2 条差异。
```

后续 prompt：

```text
Conversation Summary:
用户前面查询过苹果手机 A15 和蓝牙耳机 E7 的库存，并进一步查询了蓝牙耳机 E7 的供应商。系统返回 E7 的供应商是华东电子。随后用户查询了上个月华东电子的对账单，结果存在 2 条差异。

Recent Messages:
用户：这两条差异分别是什么？
系统：...

Current User Query:
把结果整理一下。
```

这时 summary 的作用只是让模型知道前面发生过什么。

## 11. 例子二：summary 不能直接补工具参数

summary 中有：

```text
用户前面查询过 A 供应商和 B 供应商的对账单。
```

当前用户说：

```text
给它发邮件。
```

错误做法：

```text
模型自行猜测“它”是 B 供应商，然后调用 send_email。
```

正确做法：

```text
如果 recent_messages 能唯一确定最近讨论的是 B 供应商，则可以准备 B 供应商邮件，但仍要 HITL。
如果 recent_messages 也无法唯一确定，则询问用户：你是要给 A 供应商还是 B 供应商发邮件？
```

这里的核心规则是：

```text
summary 可以提示历史存在多个供应商。
summary 不能直接决定“它”到底是谁。
```

## 12. 例子三：当前 query 与 summary 冲突

summary 中有：

```text
用户此前主要处理华东区供应商对账。
```

当前用户说：

```text
现在查一下华南区大客户的对账单。
```

正确处理：

```text
以当前 query 为准，region=华南区。
summary 只能说明此前背景是华东区，不能覆盖当前 query。
```

错误处理：

```text
因为 summary 里有华东区，所以继续查华东区。
```

面试时可以强调：

```text
当前用户输入的优先级永远高于历史摘要。
```

## 13. 例子四：动态数据不要长期相信

summary 中有：

```text
系统此前查询到苹果手机 A15 库存为 120。
```

20 分钟后用户说：

```text
按刚才那个苹果手机创建采购申请。
```

正确处理：

```text
可以利用最近上下文或用户澄清确定产品是苹果手机 A15。
但库存 120 是动态数据，不能长期信任。
创建采购申请前应该重新查库存或进入人工确认。
```

这个例子说明：

```text
summary 可以保存历史结果摘要。
但动态业务数据不能因为写进 summary 就被当成当前真实状态。
```

## 14. 例子五：普通聊天和工具任务混合

历史：

```text
用户：什么是苹果？
系统：苹果可以指水果，也可以指苹果公司。
用户：帮我查询一下苹果手机 A15 的库存。
系统：调用库存查询工具，返回库存 120。
用户：那它的供应商是谁？
```

summary 可以写：

```text
用户先询问了“苹果”的一般含义，随后转入 ERP 业务场景，查询苹果手机 A15 的库存，系统返回库存 120。最近讨论对象是苹果手机 A15。
```

处理当前请求时：

```text
“它”的解析主要依赖最近几轮原始消息。
summary 只是帮助模型知道对话从闲聊转入了业务工具场景。
```

如果最近窗口还保留：

```text
用户：帮我查询一下苹果手机 A15 的库存。
系统：库存 120。
用户：那它的供应商是谁？
```

则可以判断“它”指苹果手机 A15。

如果最近窗口已经没有这部分原文，只剩 summary，则更稳妥的做法是澄清：

```text
你是要查询苹果手机 A15 的供应商吗？
```

## 15. 与幻觉治理的关系

简化后，summary 不再作为幻觉治理主模块。

它只能间接帮助：

```text
减少上下文丢失导致的误解
降低长对话中模型忘记前文的概率
让模型知道历史上出现过哪些主题
```

它不能保证：

```text
参数一定正确
工具一定选对
模型不会编造
多候选一定不会误选
```

因此，幻觉治理仍然主要依赖：

```text
工具 RAG 召回与 rerank
工具选择 prompt 约束
参数 schema 校验
权限校验
HITL 确认
工具异常处理
循环调用检测
集成测试和 bad case 回归
```

这比把 summary 包装成万能记忆模块更稳。

## 16. 评测方式

简化之后，summary 的评测也更清晰。

### 16.1 覆盖率

检查 summary 是否保留了关键上下文。

例如原始历史中有：

```text
用户要求发邮件前必须确认。
用户正在处理 B 供应商对账。
系统查到两条差异。
```

summary 应该覆盖这些信息。

### 16.2 忠实性

检查 summary 是否编造。

bad case：

```text
原始历史只说“查询对账单”。
summary 写成“用户已确认发送邮件”。
```

这是严重错误，因为它改变了业务动作。

### 16.3 压缩率

检查 token 是否明显降低。

例如：

```text
原始历史 8000 tokens
summary 500 tokens
recent_messages 3000 tokens
最终 prompt 3500 tokens
```

这说明 compaction 达到了降低上下文成本的目的。

### 16.4 下游任务影响

检查加 summary 后，工具任务是否更稳定。

可以比较两组：

```text
无 summary，只保留最近消息
有 summary + 最近消息
```

指标包括：

```text
工具调用准确率
参数正确率
澄清触发是否合理
HITL 是否正常触发
任务完成率
```

注意：这里评测的是 summary 对下游表现的辅助作用，不是把 summary 当成参数真值库。

## 17. 推荐测试 case

### 17.1 长对话后继续查询

场景：

```text
用户前面聊了多个产品和供应商。
后面继续问最近一个产品的库存。
```

测试点：

```text
summary 能否帮助模型理解历史主题。
recent_messages 能否提供当前对象。
工具是否调用库存查询。
```

### 17.2 多供应商歧义

场景：

```text
summary 中存在 A、B 两个供应商。
用户说“给它发邮件”。
```

测试点：

```text
如果 recent_messages 无法唯一确定，系统是否澄清。
系统是否避免直接调用 send_email。
```

### 17.3 当前 query 覆盖历史

场景：

```text
summary 中是华东区。
当前 query 明确要求华南区。
```

测试点：

```text
参数是否以当前 query 为准。
不会被 summary 中的华东区污染。
```

### 17.4 summary 编造防御

场景：

```text
summary 错误写入“用户已确认发送邮件”。
但 recent_messages 中没有确认。
```

测试点：

```text
系统是否仍然要求 HITL。
不会因为 summary 里写了确认就跳过人工确认。
```

### 17.5 普通聊天转工具任务

场景：

```text
用户先问“什么是苹果”。
后面问“帮我查询苹果手机 A15 库存”。
```

测试点：

```text
系统能否从普通聊天切到工具任务。
summary 不会把“苹果”错误理解成水果并干扰工具调用。
```

## 18. 当前项目落地建议

当前项目可以按三步落地。

第一步：保留完整 session history。

```text
保存用户消息、模型消息、工具 trace、HITL 结果和异常记录。
```

第二步：实现 conversation_summary。

```text
当消息数或 token 数超过阈值时，压缩较早历史。
只输出自然语言摘要。
不输出复杂 JSON facts。
```

当前代码采用 LLM summarizer 作为主路径，并保留确定性 extractive fallback。

第三步：构造 prompt。

```text
system prompt
+ conversation_summary
+ recent_messages
+ current_user_query
```

同时在 prompt 中强调：

```text
summary 只是背景。
工具参数必须优先来自当前 query 和最近原始消息。
不明确时必须澄清或 HITL。
```

暂时不做：

```text
pinned_facts
confirmed_facts
entities
relations
active_focus
长期记忆
跨 session 记忆召回
```

这些能力可以放入后续 roadmap，但不要在当前版本作为主能力讲。

## 19. 面试表达口径

可以这样表达：

```text
我们项目里做的 summary 更接近 OpenClaw 的 Compaction，不是复杂的长期记忆系统。它的目标是解决长对话 token 过长的问题：完整历史会持久化保存，较早上下文会被压缩成 conversation_summary，最近几轮原始消息仍然进入模型上下文。

我没有把 summary 直接作为工具参数事实库，因为 LLM 生成的摘要可能不完整或产生幻觉。如果让 summary 参与强参数补全，就必须额外评测事实抽取、事实冲突、过期数据、多候选歧义等问题，工程复杂度会明显上升。

所以当前版本里，summary 只提供背景理解；工具参数仍然优先来自当前用户 query、页面上下文和最近原始消息。不明确时走澄清或 HITL。幻觉治理主要依赖工具选择约束、参数校验、权限校验、HITL、异常处理和集成评测。
```

这套表达比较稳，因为它没有过度包装 summary 的能力，同时也能体现你知道 OpenClaw / LangGraph 这类框架中的长上下文压缩思想。

## 20. 最终结论

当前项目推荐采用：

```text
full_message_history
+ conversation_summary
+ recent_messages
+ current_user_query
```

不推荐当前阶段采用：

```text
summary_state
+ confirmed_facts
+ entities
+ relations
+ active_focus
```

原因是：

```text
复杂状态会增加实现和评测成本。
当前项目的主线不是长期记忆，而是 ERP Agent 工具调用流程。
summary 只做上下文压缩已经足够合理。
工具参数和高风险动作仍然交给 query、recent_messages、参数校验、权限校验和 HITL。
```

一句话总结：

```text
summary 只负责让模型“看懂前文”，不负责替系统“决定事实”。
```

## 21. 业界常见的上下文压缩方式

当前业界对长上下文的处理，通常不是只依赖某一种压缩方法，而是把多种策略组合起来使用。比较常见的工程形态是：

```text
最近几轮原文
+ 较早历史摘要
+ 结构化任务状态
+ 必要时从历史库、工具 trace 或 RAG 中召回原始证据
```

也就是说，线上系统一般不会把所有历史都塞给模型，也不会只相信一段 summary。更稳妥的做法是：**压缩可读上下文，保留原始证据**。

### 21.1 滑动窗口 / 截断

最基础的做法是只保留最近 N 轮对话，超过窗口的历史不再进入 prompt。

例如：

```text
完整历史：30 轮
进入模型：最近 8 轮
更早历史：不进入当前 prompt
```

优点：

```text
实现简单
成本低
行为稳定
不引入 summary 幻觉
```

缺点：

```text
早期关键信息可能丢失
不适合长流程任务
不适合多轮参数补充
不适合用户早期说过关键约束的场景
```

适合场景：

```text
普通闲聊
上下文依赖不强的问答
最近几轮就足够完成任务的 Agent
```

在 ERP Agent 中，单独使用滑动窗口风险较高。比如用户早期说过“只查华东区，不要导出”，后面只说“继续处理”，如果早期约束被截断，就可能导致模型误判。

### 21.2 LLM 摘要压缩

当历史超过 token 阈值时，用一个模型把较早的对话压缩成 summary，然后在后续 prompt 中使用：

```text
system prompt
+ conversation_summary
+ recent_messages
+ current_user_query
```

典型输入：

```text
old_summary
+ messages_to_compact
```

典型输出：

```text
new_conversation_summary
```

优点：

```text
可以保留较早历史的大意
比全量历史更省 token
比纯截断更不容易丢失长期任务背景
适合多轮会话和长流程 Agent
```

缺点：

```text
summary 可能遗漏细节
summary 可能写错细节
summary 可能把不确定内容写成确定事实
summary 不适合直接作为工具参数事实库
```

因此工业项目里通常会把完整历史仍然保存在数据库中，summary 只作为模型理解上下文的背景材料，而不是强事实来源。

当前项目采用的就是这一类思路：较早历史通过 LLM summarizer 压缩成 `conversation_summary`，最近消息仍然原文进入模型。

### 21.3 结构化状态压缩

在 Agent 系统里，很多信息不适合只用自然语言 summary 表达，而应该沉淀成结构化状态。

例如：

```json
{
  "current_task": "查询华东区上个月大客户对账单",
  "confirmed_params": {
    "region": "华东区",
    "date_range": "2026-05-01~2026-05-31",
    "customer_type": "大客户"
  },
  "pending_action": "等待用户确认是否导出",
  "last_tool_result": "查到 2 条差异"
}
```

这类状态更适合做：

```text
流程恢复
HITL 等待与恢复
权限失败后的分支处理
工具异常后的重试或降级
任务当前阶段判断
```

但它也会带来明显成本：

```text
需要设计字段
需要维护状态更新逻辑
需要处理状态冲突
需要测试状态是否准确
需要防止错误状态污染后续工具调用
```

所以当前项目没有把 summary 扩展成复杂的 `summary_state`、`confirmed_facts`、`entities`、`relations`。当前版本只保留轻量的会话压缩，把流程状态交给任务状态、pending_action、HITL 和 trace 机制。

### 21.4 检索式记忆 / 历史召回

另一类常见做法是完整保存历史，但当前轮不直接塞入全部历史。系统根据当前 query，从历史消息、工具 trace、工具结果、文档库中召回相关片段。

例如用户问：

```text
刚才那两个差异分别是什么？
```

系统可以从历史工具结果中召回：

```text
最近一次对账单查询返回 result_id=R123
R123 中存在 2 条差异：
1. PO-001 入库数量与发票数量不一致
2. PO-008 税率不一致
```

然后把召回结果放入 prompt。

这种方式适合：

```text
长对话
多主题会话
历史工具结果查询
跨 session 记忆
RAG 问答
```

缺点是：

```text
召回可能漏
召回可能引入无关内容
需要评测召回率、准确率和下游任务影响
```

更稳的组合方式是：

```text
summary 提供全局背景
retrieval 提供局部证据
recent_messages 提供当前语境
current_query 决定本轮最高优先级意图
```

当前项目暂时没有把 session 历史做成跨 session 长期记忆召回，但工具 trace 和完整 session history 应该持久化保存，便于后续扩展。

### 21.5 工具结果压缩

Agent 项目里最容易撑爆上下文的，往往不是用户消息，而是工具返回。

例如库存接口一次返回 500 条明细，如果原样塞给模型，会导致：

```text
token 成本过高
模型注意力被大量表格数据干扰
后续 prompt 不稳定
接口结果泄露风险增加
```

常见做法是：

```text
完整工具结果保存到数据库或对象存储
prompt 中只放摘要
摘要中附带 result_id
用户追问明细时再按 result_id 查询原始结果
```

例如：

```text
工具返回原始结果已存储 result_id=R123。
摘要：共查询到 500 条库存记录，其中 12 条低于安全库存，3 条缺货。
```

如果用户继续问：

```text
那 3 条缺货的是哪些？
```

系统再根据 `result_id=R123` 查询原始工具结果，而不是要求模型从一大段历史表格里回忆。

这个策略对 ERP Agent 很重要，因为 ERP 接口经常返回库存明细、订单明细、对账差异、供应商列表等结构化数据。

### 21.6 Prompt Compression / Token 级压缩

还有一类更算法化的方案，会对 prompt 或检索结果做 token 级、句子级压缩，保留信息量更高的内容，删除冗余 token。

典型思路是：

```text
输入长 prompt 或长文档片段
模型或小模型判断哪些 token、句子、段落信息量低
删除低价值内容
输出更短的 prompt
```

这种方式适合：

```text
RAG 检索片段压缩
长文档问答
降低 token 成本
多文档输入前的预处理
```

但在 ERP 工具调用场景里要谨慎使用。因为金额、订单号、供应商名称、区域、日期、审批状态这些字段看起来可能只是短 token，但它们对业务动作非常关键。一旦被压缩算法误删，可能影响工具参数和权限判断。

所以当前项目不建议把 token 级压缩用于高风险工具参数、审批内容、金额、订单号等强事实字段。

### 21.7 分层记忆 / Virtual Context

一些更完整的 Agent 系统会把上下文看成多级存储：

```text
热上下文：当前 prompt 中的 recent_messages、current_query
温上下文：conversation_summary、任务状态
冷上下文：数据库、向量库、文件、历史 trace、完整工具结果
```

模型当前只看到热上下文和部分温上下文。需要更多信息时，系统再从冷存储中召回。

这种思想可以理解为：

```text
不是让模型一次性看到全部历史
而是让系统维护完整历史
模型只拿到当前任务需要的工作集
```

这也是当前项目后续可以演进的方向。

### 21.8 对当前 ERP Agent 的推荐方案

结合当前项目的复杂度、面试表达成本和工程可验证性，推荐采用以下组合：

```text
1. 数据库保存完整 session 原文、工具调用 trace、HITL 记录。
2. prompt 中只放 token budget 内的最近工作集原文。
3. 超过 prompt token 阈值后，用 LLM 对较早历史做 conversation_summary。
4. 工具结果不全量进入上下文，只进入摘要 + result_id。
5. 用户追问工具结果细节时，再根据 result_id 查询原始结果。
6. summary 不作为强执行证据，工具调用参数优先来自当前 query、recent_messages、页面上下文和明确工具结果。
7. 高风险动作仍然必须经过权限校验、参数校验和 HITL。
```

最终 prompt 的推荐结构是：

```text
System Prompt
+ Conversation Summary
+ Recent Messages
+ Retrieved Tool Result / Trace Evidence（如有）
+ Current User Query
```

其中：

```text
Conversation Summary 负责提供背景。
Recent Messages 负责提供最近语境。
Retrieved Evidence 负责提供可追溯证据。
Current User Query 负责表达本轮最高优先级意图。
```

### 21.9 面试表达口径

可以这样讲：

```text
我们没有把长对话简单地全部塞进 prompt，而是采用“完整历史持久化 + 最近消息原文 + 较早历史摘要 + 必要时召回原始证据”的方式。

summary 只解决上下文压缩问题，不直接作为工具参数事实库。这样做的原因是，LLM 生成的摘要可能不完整或出现幻觉，如果让 summary 直接参与强参数补全，会放大工具误调用风险。

对 ERP Agent 来说，真正影响业务安全的是工具选择、参数校验、权限校验、HITL 和 trace 可审计。所以我们把 summary 定位为背景理解能力，把强执行依据保留在当前 query、recent messages、工具 trace 和数据库原始记录里。
```

一句话总结：

```text
业界常见做法不是“让模型记住一切”，而是“系统保存一切，模型只读取当前任务需要的压缩工作集”。
```

## 22. 基于 Token Budget 的压缩改造方案

当前代码按消息条数触发压缩，这种方式简单，但不够贴近真实长上下文压力。

原因是：

```text
一条消息可能只有 10 个字，也可能是一大段工具结果。
6 条普通问答可能只有几百 tokens。
1 条工具 observation 可能有上万 tokens。
```

所以“按消息条数压缩”并不能真实反映 prompt 是否接近模型上下文窗口。更合理的做法是参考 OpenClaw 风格的 compaction：**根据 prompt token 压力触发压缩，而不是根据轮数触发压缩**。

### 22.1 核心原则

基于 token 的 compaction 应遵守以下原则：

```text
1. 完整历史永远持久化保存，不因为压缩而删除原始消息。
2. 压缩触发依据是预计 prompt token，而不是消息数、轮数。
3. 当前用户 query 永远原文保留。
4. 最近工作集按 token budget 保留原文，而不是按固定 N 轮保留。
5. 较早历史压缩进 conversation_summary。
6. 工具大结果优先做 observation compression，只把摘要和 result_id 放入 prompt。
7. summary 仍然只作为上下文背景，不作为工具参数事实库。
8. 压缩失败不能阻断主流程，必须有 deterministic fallback。
```

这里的关键点是：**轮数只是一种粗糙代理，token 才是真正约束模型调用的资源**。

### 22.2 推荐参数

可以在配置中增加以下参数：

```text
model_context_window_tokens
模型上下文窗口大小。例如 32768、65536、128000。

reserved_output_tokens
预留给模型输出的 token，例如 2048 或 4096。

soft_compaction_ratio
软触发阈值，例如 0.75。
当预计 prompt tokens 超过可用输入窗口的 75% 时，触发压缩。

hard_compaction_ratio
硬触发阈值，例如 0.90。
当预计 prompt tokens 超过可用输入窗口的 90% 时，必须压缩；压缩后仍超限则继续裁剪检索结果和工具 observation。

summary_max_tokens
summary 最大 token 数，例如 1000 到 2000。

recent_working_set_min_tokens
最近工作集至少保留的 token，例如 2000。
用于保证最近对话、HITL 状态和当前任务上下文不会被过度压缩。

recent_working_set_max_tokens
最近工作集最多保留的 token，例如 6000 到 12000。
具体取决于模型窗口和工具描述长度。

tool_observation_max_tokens
单个工具结果进入 prompt 的最大 token，例如 800 到 1500。
超过时只保留摘要、异常项、TopK 和 result_id。
```

输入预算可以这样计算：

```text
available_input_tokens = model_context_window_tokens - reserved_output_tokens
soft_budget = available_input_tokens * soft_compaction_ratio
hard_budget = available_input_tokens * hard_compaction_ratio
```

例如：

```text
model_context_window_tokens = 32768
reserved_output_tokens = 4096
available_input_tokens = 28672
soft_budget = 21504
hard_budget = 25804
```

当候选 prompt 预计超过 21504 tokens 时，开始压缩；超过 25804 tokens 时，必须压缩并进一步裁剪。

### 22.3 Token 估算方式

工程上需要增加一个 `TokenEstimator`。

优先级建议：

```text
1. 如果模型有明确 tokenizer，使用模型对应 tokenizer。
2. 如果是 OpenAI-compatible 模型，可以使用 tiktoken 或兼容 tokenizer。
3. 如果是本地私有化模型，可以使用 transformers tokenizer。
4. 如果拿不到 tokenizer，使用保守估算：
   中文按 1 到 1.5 字符约等于 1 token 估算；
   英文按 3 到 4 字符约等于 1 token 估算；
   JSON、表格、工具结果额外乘以 1.2 到 1.5 的膨胀系数。
```

不要为了追求绝对精确而让实现变重。token 估算的目标是判断“是否接近窗口上限”，不是精确计费。

推荐提供统一接口：

```python
class TokenEstimator:
    def count_text(self, text: str, model_name: str) -> int:
        ...

    def count_message(self, message: dict, model_name: str) -> int:
        ...

    def count_prompt_parts(self, parts: dict, model_name: str) -> dict:
        ...
```

输出不仅要有总 token，还要有分项：

```json
{
  "system_prompt": 3200,
  "tool_descriptions": 4800,
  "conversation_summary": 1100,
  "recent_messages": 8200,
  "rag_context": 3600,
  "current_query": 120,
  "total": 21020
}
```

这样后续排查时能知道 token 压力来自哪里：是工具描述太多、RAG 片段太长，还是工具结果太大。

### 22.4 触发时机

建议把 compaction 的主要触发点放在 **调用主模型之前**，而不是只放在任务结束之后。

原因是：

```text
真正会因为上下文过长失败的是本次模型调用。
如果只在任务结束后压缩，可能当前这次调用已经超限。
```

推荐流程：

```text
用户输入写入 SessionMemory
-> 构造候选 prompt parts
-> 估算 prompt token
-> 判断是否超过 soft_budget
-> 如超过，执行 compaction
-> 重新构造 prompt parts
-> 再次估算 token
-> token 合规后调用主模型
-> 模型输出和工具 trace 持久化
```

任务结束后可以做一次轻量维护：

```text
如果本轮结束后，下一轮候选上下文预计会超过 soft_budget，可以后台预压缩。
```

但主流程必须保证：**每次真正调用模型前，prompt 已经通过 token budget 检查**。

### 22.5 具体流程

完整流程如下：

```text
1. 保存当前用户消息
   SessionMemory 写入 user_query。

2. 读取完整上下文索引
   读取 SummaryMemory 中已有 conversation_summary。
   读取从 last_compacted_message_id 之后的未压缩消息。
   读取必要的最近工具 trace、HITL 状态、当前 query。

3. 构造候选 prompt
   System Prompt
   + Tool Descriptions / Candidate Tools
   + Conversation Summary
   + Uncompressed Recent Messages
   + Retrieved Evidence
   + Current User Query

4. 估算候选 prompt token
   使用 TokenEstimator 计算各部分 token 和总 token。

5. 判断是否触发压缩
   如果 total_tokens <= soft_budget：不压缩。
   如果 total_tokens > soft_budget：触发 compaction。
   如果 total_tokens > hard_budget：进入强制压缩。

6. 选择最近工作集
   从最新消息开始向前累计 token。
   当前 query、最近 HITL、最近工具结果摘要优先保留。
   累计到 recent_working_set_max_tokens 附近停止。
   保留内容必须以完整 message 为边界，不要把一条自然语言消息切半。

7. 确定待压缩消息
   待压缩消息 = last_compacted_message_id 之后、且不在最近工作集中的较早消息。

8. 生成新 summary
   old_summary + messages_to_compact -> new_summary。
   主路径使用 LLM summarizer。
   LLM 失败时使用 deterministic fallback。

9. 更新 SummaryMemory
   写入 new_summary。
   更新 last_compacted_message_id。
   记录 summary_token_count、prompt_tokens_before、prompt_tokens_after、compaction_reason。

10. 重新构造 prompt
    使用 new_summary + 最近工作集 + 当前 query。

11. 再次估算 token
    如果仍超过 hard_budget：
    - 压缩工具 observation；
    - 减少 RAG topK；
    - 降低 summary_max_tokens；
    - 缩小 recent_working_set_max_tokens；
    - 必要时要求用户缩小范围或重新发起任务。

12. 调用主模型
    token 合规后进入工具选择、参数抽取、HITL 或直接回答流程。
```

### 22.6 最近工作集如何选择

最近工作集不再按“最近 6 条消息”选择，而是按 token budget 选择。

推荐策略：

```text
从最新消息向前扫描
-> 当前 query 必保留
-> 最近 assistant 输出必保留
-> 当前 pending_action / HITL 相关消息必保留
-> 最近一次工具调用摘要和 result_id 必保留
-> 继续向前保留消息，直到达到 recent_working_set_max_tokens
```

示例：

```text
recent_working_set_max_tokens = 6000

从最新消息向前累计：
当前 query：80 tokens
上一条 assistant：600 tokens
用户确认反馈：50 tokens
工具结果摘要：900 tokens
前一轮用户请求：120 tokens
...
累计到 5800 tokens 时停止
更早消息进入待压缩区
```

注意：

```text
保留单位仍然是 message，不是任意切 token。
如果某条工具结果本身超过 observation budget，需要先做工具结果压缩。
自然语言消息不要从中间截断。
```

### 22.7 工具结果优先压缩

在 Agent 系统里，工具 observation 经常比聊天历史更占 token。

所以 token 超限时，不应该只压缩对话，还应该优先处理大工具结果：

```text
完整工具结果保存到数据库。
prompt 中只放：
- result_id
- 总数
- 关键字段
- 异常项
- TopK 示例
- 下一步判断需要的最小信息
```

例如：

```text
原始工具结果：
返回 500 条库存明细。

进入 prompt：
result_id=INV-20260616-001。
共 500 条库存记录，12 条低于安全库存，3 条缺货。
缺货 SKU：A15、E7、M20。
完整结果已保存，用户追问明细时按 result_id 查询。
```

这样比把 500 条明细直接塞进 prompt 更稳定。

### 22.8 SummaryMemory 字段建议

当前 `SummaryMemory` 主要字段是：

```text
summary
compacted_message_count
recent_window
max_summary_chars
updated_at
```

基于 token 的设计建议改为：

```json
{
  "user_id": "u001",
  "session_id": "s001",
  "summary": "用户前面主要在处理华东区供应商对账问题...",
  "last_compacted_message_id": "msg_018",
  "last_compacted_at": "2026-06-16T10:30:00+08:00",
  "summary_token_count": 980,
  "summary_max_tokens": 1500,
  "model_context_window_tokens": 32768,
  "reserved_output_tokens": 4096,
  "soft_budget": 21504,
  "hard_budget": 25804,
  "recent_working_set_token_count": 5800,
  "prompt_tokens_before": 26300,
  "prompt_tokens_after": 14200,
  "compaction_reason": "prompt_tokens_exceeded_soft_budget",
  "compaction_version": "token_budget_v1"
}
```

为了兼容旧代码，可以暂时保留 `compacted_message_count`，但新逻辑不应该再用它作为触发条件。它最多作为迁移期的辅助字段。

### 22.9 Trace 记录建议

每次 compaction 都应该写入 trace event，方便评测和线上排查。

建议事件：

```json
{
  "event_type": "context_compacted",
  "strategy": "token_budget",
  "trigger": "soft_budget_exceeded",
  "model_context_window_tokens": 32768,
  "reserved_output_tokens": 4096,
  "soft_budget": 21504,
  "hard_budget": 25804,
  "prompt_tokens_before": 26300,
  "prompt_tokens_after": 14200,
  "summary_tokens_before": 1200,
  "summary_tokens_after": 980,
  "recent_working_set_tokens": 5800,
  "compacted_message_count_delta": 12,
  "last_compacted_message_id": "msg_018",
  "fallback_used": false
}
```

如果出现问题，可以快速判断：

```text
是不是工具结果太大？
是不是 summary 太长？
是不是 recent working set 预算过大？
是不是 RAG topK 太高？
是不是压缩后仍然接近 hard budget？
```

### 22.10 伪代码

核心伪代码如下：

```python
def build_context_with_token_budget(user_id, session_id, current_query, model_name):
    summary = memory_manager.get_summary(user_id, session_id)
    messages = memory_manager.get_uncompacted_messages(
        user_id=user_id,
        session_id=session_id,
        after_message_id=summary.last_compacted_message_id,
    )

    prompt_parts = build_prompt_parts(
        summary=summary.text,
        messages=messages,
        current_query=current_query,
    )
    token_report = token_estimator.count_prompt_parts(prompt_parts, model_name)

    if token_report["total"] <= soft_budget:
        return prompt_parts, token_report

    recent_working_set = select_recent_working_set_by_tokens(
        messages=messages,
        current_query=current_query,
        max_tokens=recent_working_set_max_tokens,
        min_tokens=recent_working_set_min_tokens,
    )

    messages_to_compact = messages_before(recent_working_set)

    new_summary = summarize(
        old_summary=summary.text,
        messages=messages_to_compact,
        max_tokens=summary_max_tokens,
    )

    memory_manager.update_summary(
        user_id=user_id,
        session_id=session_id,
        summary=new_summary,
        last_compacted_message_id=messages_to_compact[-1].id,
        token_metadata=token_report,
    )

    rebuilt_parts = build_prompt_parts(
        summary=new_summary,
        messages=recent_working_set,
        current_query=current_query,
    )
    rebuilt_token_report = token_estimator.count_prompt_parts(rebuilt_parts, model_name)

    if rebuilt_token_report["total"] > hard_budget:
        rebuilt_parts = shrink_context_until_fit(rebuilt_parts, hard_budget)

    return rebuilt_parts, rebuilt_token_report
```

这段逻辑的重点是：

```text
先估算 token，再决定是否压缩。
先保留最近工作集，再压缩更早消息。
压缩后必须重新估算。
仍然超限时继续缩减工具结果、RAG 片段或最近工作集。
```

### 22.11 具体例子

假设模型窗口是 32k tokens：

```text
model_context_window_tokens = 32768
reserved_output_tokens = 4096
available_input_tokens = 28672
soft_budget = 21504
hard_budget = 25804
summary_max_tokens = 1500
recent_working_set_max_tokens = 6000
```

某次用户请求前，系统构造候选 prompt：

```text
system prompt：2800 tokens
工具描述：5200 tokens
conversation_summary：1300 tokens
历史消息和工具结果：19000 tokens
当前 query：100 tokens
总计：28400 tokens
```

此时：

```text
28400 > hard_budget 25804
必须压缩。
```

系统从最新消息向前保留最近工作集：

```text
当前 query：100 tokens
最近 HITL 确认状态：300 tokens
最近工具结果摘要：900 tokens
最近 5 条自然语言消息：2300 tokens
上一轮 assistant 输出：1200 tokens
合计：4800 tokens
```

较早的历史消息进入 compaction：

```text
old_summary + 较早历史消息 -> new_summary
```

压缩后重新估算：

```text
system prompt：2800 tokens
工具描述：5200 tokens
new conversation_summary：950 tokens
recent working set：4800 tokens
当前 query：100 tokens
总计：13850 tokens
```

此时低于 soft_budget，可以调用主模型。

### 22.12 和 OpenClaw Compaction 的对应关系

参考 OpenClaw 风格时，重点不是照搬某个固定参数，而是借鉴以下机制：

```text
1. 不把完整历史无限塞进上下文。
2. 接近上下文窗口阈值时才触发 compaction。
3. 较早上下文被压缩成 summary。
4. 最近消息仍然原文保留。
5. 完整历史仍然在系统侧保存。
6. compaction 后继续执行当前任务，而不是让用户重新开始。
```

映射到当前 ERP Agent：

```text
OpenClaw 的压缩上下文
-> 当前项目的 conversation_summary

OpenClaw 的最近未压缩上下文
-> 当前项目的 recent_working_set

OpenClaw 的长期完整记录
-> 当前项目的 SessionMemory、TraceRecord、工具结果存储

OpenClaw 的接近窗口时触发
-> 当前项目的 soft_budget / hard_budget token 触发
```

### 22.13 评测方式

改成 token 触发后，需要补充以下评测：

```text
1. token 估算准确性
   对比估算 token 和真实模型调用 token，误差控制在可接受范围。

2. 压缩触发正确性
   未超过 soft_budget 时不压缩。
   超过 soft_budget 时触发压缩。
   超过 hard_budget 时强制压缩并保证最终 prompt 合规。

3. 最近工作集保留正确性
   当前 query 必保留。
   最近 HITL 状态必保留。
   最近工具结果摘要和 result_id 必保留。
   不应该为了压缩把当前任务关键上下文丢掉。

4. summary 忠实性
   summary 不编造用户确认。
   summary 不把临时参数写成长期偏好。
   summary 保留任务背景、已完成步骤、未完成事项和明确约束。

5. 下游任务影响
   压缩前后工具选择准确率不能下降。
   参数正确率不能明显下降。
   HITL 触发不能被 summary 错误绕过。
   任务完成率不能下降。

6. 成本与稳定性
   平均 prompt tokens 下降。
   超上下文失败率下降。
   summarizer 失败时 fallback 可用。
```

建议增加集成测试 case：

```text
短对话不触发压缩。
长对话超过 soft_budget 触发压缩。
工具结果过大时优先压缩 observation。
压缩后仍超 hard_budget 时减少 RAG topK。
HITL 等待中的任务压缩后仍能继续确认执行。
summary 错误声称用户已确认时，系统仍然要求 HITL。
```

### 22.14 面试表达口径

可以这样讲：

```text
我们一开始没有简单按轮数做上下文压缩，因为轮数不能真实代表上下文压力。一条工具结果可能比十轮普通对话还长。所以我们参考 OpenClaw 的 compaction 思路，把触发条件改成 token budget：每次调用模型前都会估算 system prompt、工具描述、summary、recent working set、RAG 片段和当前 query 的 token。

如果没有超过 soft budget，就不压缩；如果超过阈值，就把较早历史合并进 conversation_summary，同时按 token 预算保留最近工作集原文。完整历史仍然保存在数据库里，summary 只用于背景理解，不作为工具参数事实库。

这样做的好处是压缩时机更准确，既避免短对话无意义总结，也能防止工具结果或长历史把上下文撑爆。后续我们通过 token 压缩率、任务完成率、工具调用准确率、HITL 保留正确性和 summary 忠实性来做回归评测。
```

一句话总结：

```text
从“最近 N 轮”改成“token budget 下的最近工作集”，才是真正面向长上下文 Agent 的 compaction。
```
