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

推荐触发条件：

```text
1. 当前 session 消息数超过阈值，例如 12 轮。
2. 当前 prompt token 预计超过阈值，例如 70% 上下文窗口。
3. 工具 trace 太多，导致最近上下文明显膨胀。
4. 用户开启长任务，连续多轮围绕同一业务流程交互。
```

推荐做法：

```text
保留最近 N 轮原文。
把更早的消息压缩进 conversation_summary。
```

例如：

```text
总历史 30 轮
最近 8 轮保留原文
前 22 轮压缩成 summary
```

下一次触发时：

```text
旧 summary + 新增的较早消息 -> 新 summary
继续保留最近 8 轮原文
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
当消息数或 token 数超过阈值时，调用 LLM 压缩较早历史。
只输出自然语言摘要。
不输出复杂 JSON facts。
```

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
