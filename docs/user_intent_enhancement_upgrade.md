# 用户意图增强升级说明

本文档描述当前 `agent-copilot-hitl-v3-engineering` 在 HITL 用户反馈理解上的保留能力。

当前版本不做全局“是否调用工具”的意图识别，也已经删除“模糊需求澄清”和“上下文改写 grounding 澄清”。系统只在已有任务进入等待状态后，根据 `pending_action` 判断用户本次反馈应该如何处理。

## 1. 当前边界

当前保留的用户反馈识别范围是：

```text
任务已经存在
+ 后端已经进入等待状态
+ 用户本次输入带 taskId
-> 系统判断这句话是确认、取消、补参数，还是表达不清
```

它不负责：

```text
判断普通聊天是否要调用工具
处理“老样子”“按上次方案”这类模糊需求候选
跨 session 长期记忆召回
从长期记忆中自动恢复订单、供应商、产品等参数
用正则或置信度判断上下文改写是否可信
```

这样设计的原因是：ERP 工具调用涉及权限、参数校验、HITL 和审计。当前版本优先保证等待状态机可解释、可测试，而不是把所有自然语言理解能力都塞进一个复杂意图识别模块。

## 2. Pending Action 分流

`Task` 中使用 `pending_action` 和 `pending_payload` 表示当前正在等待用户处理的具体业务动作。

当前支持的 pending action：

```text
missing_params_clarify      等待用户补充工具必填参数
tool_execution_confirm      等待用户确认是否执行工具调用
```

`process_human_feedback()` 会先按 `pending_action` 分流：

```text
missing_params_clarify -> 参数补全反馈处理器
tool_execution_confirm -> 工具执行确认处理器
```

已经删除：

```text
ambiguity_confirm
rewrite_grounding_clarify
```

也就是说，当前不再支持“系统根据长期记忆生成模糊需求候选方案，然后等待用户确认候选”的链路，也不再因为上下文改写后的实体没有通过正则匹配就进入专门澄清分支。

## 3. 参数不足时请求用户补充

实现位置：

```text
apis/api_planning_hub.py
app.py
prompt/prompt_registry/slot_filling_intent/v1.yaml
```

流程：

```text
选中工具
-> 抽取参数
-> 参数完整：进入工具调用确认
-> 参数不完整：尝试原有 API 反向补参
-> 仍缺必填参数：进入 missing_params_clarify，不直接失败
```

`pending_payload` 保存当前工具、已知参数和仍缺字段：

```json
{
  "original_query": "帮我创建订单，产品 1001，数量 20",
  "current_task_desc": "创建订单",
  "tool_id": 12,
  "tool_name": "创建订单",
  "operation_id": "create",
  "known_params": {
    "productId": 1001,
    "quantity": 20
  },
  "missing_params": [
    {
      "name": "supplierId",
      "description": "物流供应商Id",
      "type": "int64",
      "required": true
    }
  ]
}
```

用户可见文案：

```text
还需要补充以下信息才能继续：物流供应商Id。请直接提供这些信息，或取消本次任务。
```

用户补充后，系统用 `slot_filling_intent` prompt 解析本轮回复：

```json
{
  "intent": "provide_info | abort | unclear",
  "filled_params": {
    "supplierId": 3
  },
  "confidence": 0.91,
  "reason": "用户提供了物流供应商ID"
}
```

LLM 只负责把用户回复转为结构化字段。参数合并、类型转换、枚举校验和必填校验仍由后端规则完成。

如果仍缺字段：

```text
继续保持 missing_params_clarify
```

如果参数完整：

```text
进入 tool_execution_confirm
```

## 4. 工具执行确认

`tool_execution_confirm` 是工具执行前的确认阶段。当前工程所有工具调用前都会进入确认，不只限制写接口。

当前支持：

```text
confirm -> 执行当前缓存的 curr_tool_id + curr_tool_param
abort -> 取消任务
unclear -> 继续等待确认
```

注意：当前最终确认阶段不支持直接改参数。

例如：

```text
系统：将调用创建订单工具，参数 quantity=20，请确认。
用户：可以，但数量改成 50。
```

当前会被视为：

```text
unclear
```

不会直接把参数改成 50 后执行。这样更保守，避免最终确认阶段混入参数变更导致误执行。需要支持“确认阶段改参数”时，应先把它作为明确的新需求进入重新规划和重新确认，而不是在确认处理器里静默改参。

## 5. 上下文改写的当前处理

当前仍保留上下文改写，但它只是把 bounded context 显式拼到下游工具选择和参数抽取输入中：

```text
用户当前请求
+ conversation summary
+ recent messages
-> target_query
-> API planning
```

已经删除的旧方案是：

```text
抽取 target_query 中的硬实体
-> 用字符串匹配检查这些实体是否出现在 query / recent messages / summary
-> 不通过则进入 rewrite_grounding_clarify
```

删除原因：

```text
硬实体抽取覆盖不全，容易漏掉复杂业务表达
字符串匹配难以处理同义词、别名、简称和跨句指代
LLM 自报置信度不能作为可靠执行依据
summary 本身是压缩文本，不适合作为强参数事实库
该分支增加了状态机复杂度，但评测闭环不稳定
```

当前如果上下文改写后仍然缺少必填参数，系统会进入 `missing_params_clarify`。如果参数完整，也仍然会进入 `tool_execution_confirm`，由用户确认本次工具和参数是否可以执行。上下文改写不再单独触发 HITL。

## 6. 已删除能力

本次裁剪删除了以下能力：

```text
memory/ambiguity_resolver.py
prompt/prompt_registry/ambiguity_feedback_intent/v1.yaml
prompt/evals/datasets/ambiguity_resolution.json
prompt/evals/datasets/ambiguity_feedback_intent.json
pending_action=ambiguity_confirm
pending_action=rewrite_grounding_clarify
ContextManager.validate_query_grounding
```

删除原因：

```text
依赖长期记忆和复杂候选 grounding
评测成本高
当前工程没有长期记忆事实库
旧上下文 grounding 依赖正则和字符串匹配，不满足工业级可靠性要求
容易给面试和文档造成“已经有强实体溯源”的误导
```

## 7. 评测建议

当前应重点评测：

```text
missing_params_clarify 是否正确触发
用户补参是否正确合并
工具执行确认是否一定出现
确认、取消、不明确反馈是否被正确处理
确认阶段是否会错误执行参数变更
上下文追问是否能在最近消息和 summary 辅助下选对工具、抽对参数
```

不再评测：

```text
rewrite_grounding_pass_rate
rewrite_grounding_clarify 命中率
硬实体字符串来源匹配准确率
```

## 8. 面试表达

可以这样讲：

> 我们后来删掉了一个上下文改写 grounding 分支。原因是它依赖硬实体正则和字符串匹配，看起来像“参数来源校验”，但实际很难覆盖同义词、别名和复杂指代，误拦截和漏拦截都不好评测。当前版本保留上下文改写作为辅助输入，把不确定性放到更可控的缺参澄清、全工具 HITL、权限校验、参数校验和 trace/eval 中处理。这个取舍比堆一个不稳定的置信度判断更适合生产项目。

## 9. 附录：如果临时保留模糊需求匹配和上下文澄清，最小方案应该怎么做

本节只作为方案保留，不代表当前工程已经实现。

当前代码仍然保持以下边界：

```text
不存在 ambiguity_confirm 状态
不存在 rewrite_grounding_clarify 状态
不存在 ambiguity_resolver.py
不存在上下文改写确认代码
不存在基于长期记忆的模糊需求候选生成
```

如果后续为了面试讲述或试点验证，想临时保留一版“最普通、最简单”的模糊需求匹配和上下文澄清能力，建议不要恢复之前复杂的 pinned_facts、硬实体 grounding、置信度判定和长期记忆方案，而是只做一个轻量的 `ContextClarifier`：

```text
current_query
+ recent_messages
+ conversation_summary
-> 判断能否改写成明确 ERP 工具任务
```

它的定位不是幻觉治理核心，也不是权限校验，更不是长期记忆。它只是工具选择前的轻量 query rewrite / clarification。

### 9.1 最小链路

建议链路：

```text
用户输入
-> 读取当前 session 的 recent_messages 和 conversation_summary
-> ContextClarifier 判断
   -> ready：使用 rewritten_query 进入 RAG 工具选择
   -> need_clarification：直接询问用户，不进入工具选择
```

也就是说，工具选择模块尽量不要直接看到：

```text
把它导出来
```

而是看到：

```text
导出上个月华东区大客户的对账单
```

如果无法唯一确定“它”指什么，就不要进入工具选择，而是向用户澄清。

### 9.2 输入输出格式

输入示例：

```json
{
  "current_query": "把它导出来",
  "recent_messages": [
    {
      "role": "user",
      "content": "查一下上个月华东区大客户的对账单"
    },
    {
      "role": "assistant",
      "content": "查询到 12 条对账单记录"
    }
  ],
  "summary": "用户正在处理华东区大客户上个月的对账单。"
}
```

输出只允许两类。

第一类：可以改写。

```json
{
  "status": "ready",
  "rewritten_query": "导出上个月华东区大客户的对账单",
  "clarify_question": "",
  "reason": "当前请求中的“它”可以由最近上下文中的对账单指代补全"
}
```

第二类：需要澄清。

```json
{
  "status": "need_clarification",
  "rewritten_query": "",
  "clarify_question": "你想导出的是刚才查询到的对账单，还是库存结果？",
  "reason": "上下文中存在多个可能指代对象，无法确定“它”指哪个"
}
```

如果项目仍然坚持工具窗口只处理工具任务，可以先不引入 `chat` 状态，只保留：

```text
ready
need_clarification
```

这样状态机最简单，也不会重新引入全局“是否调用工具”的复杂意图识别。

### 9.3 Prompt 最小写法

Prompt 不要复杂，重点是保守：

```text
你是 ERP Agent 的上下文澄清模块。

你的任务是根据当前用户输入、最近对话和会话摘要，判断当前输入能否被改写成一个明确的 ERP 工具任务。

规则：
1. 只允许使用当前输入、最近对话和摘要中的信息。
2. 如果当前输入本身已经明确，直接返回 ready。
3. 如果当前输入包含“它、这个、刚才那个、这些结果”等指代，并且上下文中只有一个明确对象，可以改写。
4. 如果上下文中有多个可能对象，必须返回 need_clarification。
5. 不要猜测用户没有提供的信息。
6. 输出 JSON，不要输出解释性文本。

输出格式：
{
  "status": "ready | need_clarification",
  "rewritten_query": "...",
  "clarify_question": "...",
  "reason": "..."
}
```

后端只做两个兜底：

```text
JSON 解析失败 -> need_clarification
rewritten_query 为空 -> need_clarification
```

不要在这一版里做硬实体正则、字符串来源匹配、LLM 自报置信度拦截。那些能力看起来更强，但评测和解释成本会明显上升。

### 9.4 典型 Case

#### Case 1：单一上下文，可以改写

对话：

```text
用户：查一下苹果手机 A15 的库存
系统：库存还有 120 台
用户：把它导出来
```

期望：

```json
{
  "status": "ready",
  "rewritten_query": "导出苹果手机 A15 的库存结果"
}
```

原因：

```text
最近上下文中只有一个明确业务对象：苹果手机 A15 的库存结果。
```

#### Case 2：多个候选对象，必须澄清

对话：

```text
用户：查一下苹果手机 A15 的库存
系统：库存还有 120 台
用户：再查一下蓝牙耳机 E7 的供应商
系统：供应商是华东电子
用户：把它导出来
```

期望：

```json
{
  "status": "need_clarification",
  "clarify_question": "你想导出苹果手机 A15 的库存结果，还是蓝牙耳机 E7 的供应商信息？"
}
```

原因：

```text
上下文里同时存在库存结果和供应商信息，“它”无法唯一确定。
```

#### Case 3：缺少动作，必须澄清

对话：

```text
用户：帮我处理一下刚才那个问题
```

期望：

```json
{
  "status": "need_clarification",
  "clarify_question": "你想处理的是哪个问题？需要查询、导出、发送还是创建业务单据？"
}
```

原因：

```text
既不知道“刚才那个问题”是什么，也不知道用户要执行什么业务动作。
```

#### Case 4：summary 能辅助理解，但不能当强事实库

上下文：

```text
conversation_summary:
用户前面主要在处理华东区大客户上个月的对账单，并查询到 2 条差异。

recent_messages:
用户：这两条差异分别是什么？
系统：第一条是金额不一致，第二条是发票状态不一致。

current_query:
把结果整理一下。
```

期望：

```json
{
  "status": "ready",
  "rewritten_query": "整理华东区大客户上个月对账单中 2 条差异的结果"
}
```

注意：

```text
这里可以用于整理和总结，因为不涉及危险工具执行。
如果用户说“把它发给财务”，即使能改写成发送邮件任务，后续仍必须经过工具选择、参数校验、权限校验和 HITL。
```

### 9.5 如何测评

建立一个小型 eval 数据集即可，先不要做得过重。

建议 30 到 50 条 case，覆盖：

```text
query 本身明确，不依赖上下文
单一上下文指代，可以改写
多个候选对象，必须澄清
缺少动作，必须澄清
上下文较长，需要 summary 辅助
上下文中有相似实体，不能乱猜
```

每条数据标注：

```json
{
  "case_id": "ctx_001",
  "current_query": "把它导出来",
  "recent_messages": [],
  "summary": "",
  "expected_status": "ready",
  "expected_rewritten_query_contains": ["导出", "苹果手机 A15", "库存"],
  "expected_clarify_keywords": []
}
```

不要要求 `rewritten_query` 和 expected 完全一致。更合理的是看：

```text
动作是否保留
业务对象是否保留
时间/区域/供应商/商品等关键限定是否保留
是否错误补入了上下文中不存在的信息
改写后进入工具选择是否选对工具
```

核心指标：

| 指标 | 含义 |
| --- | --- |
| `status_accuracy` | `ready` / `need_clarification` 判断是否正确 |
| `rewrite_success_rate` | 需要改写的 case 中，改写结果是否包含核心动作和核心对象 |
| `over_clarification_rate` | 本来可以改写，却要求用户澄清 |
| `unsafe_guess_rate` | 本来应该澄清，却擅自改写 |
| `downstream_tool_accuracy` | 改写后的 query 进入工具选择后，工具是否选对 |

其中最重要的是：

```text
unsafe_guess_rate
```

因为多问一句通常只是体验问题，乱猜并进入工具调用链路才是业务风险。

建议门槛：

```text
status_accuracy >= 90%
rewrite_success_rate >= 85%
unsafe_guess_rate <= 3%
downstream_tool_accuracy >= 90%
```

### 9.6 持续改进方式

持续改进不要靠感觉改 prompt，而是按 bad case 分类。

#### 该澄清却改写了

现象：

```text
上下文里有多个候选对象，但模型选择了其中一个。
```

处理：

```text
prompt 增加“多个候选必须澄清”的规则和反例。
case 加入 context clarification regression。
```

#### 该改写却澄清了

现象：

```text
上下文只有一个明确对象，模型仍然要求用户澄清。
```

处理：

```text
增加单一指代正例。
检查 summary 是否丢掉关键对象。
检查 recent_messages 窗口是否过小。
```

#### 改写丢了关键参数

现象：

```text
原始上下文里有时间、区域、商品、供应商，但 rewritten_query 丢掉了。
```

处理：

```text
prompt 明确要求保留时间、区域、商品、供应商、订单号、对账单等业务限定。
把该 case 加入 rewrite_success_rate 评测。
```

#### 改写后工具选错

现象：

```text
rewritten_query 看起来合理，但后续工具选择错了。
```

处理：

```text
先判断是 rewrite 问题，还是 RAG 工具选择问题。
如果 rewritten_query 缺少动作词，修 rewrite prompt。
如果 rewritten_query 完整但工具仍选错，修工具描述、rerank 或 tool selection prompt。
```

#### summary 信息不够

现象：

```text
recent_messages 已经没有关键对象，summary 也没有保留。
```

处理：

```text
调整 conversation_summary_compaction prompt。
要求 summary 保留当前任务对象、已查询结果、未完成事项和最近业务焦点。
但仍然不要把 summary 当作强参数事实库。
```

### 9.7 面试表达

可以这样讲：

> 我们曾经讨论过是否保留模糊需求澄清和上下文改写确认。我的判断是，如果要做，也不能一上来做复杂的长期记忆和硬实体 grounding，因为这类能力很难评测，误判成本也高。更稳的方式是只做一个轻量 ContextClarifier：输入当前 query、最近消息和 conversation summary，输出 ready 或 need_clarification。能唯一确定就改写成明确任务，不能唯一确定就问用户。
>
> 这套能力不承担权限、安全和事实校验，只是工具选择前的辅助理解。真正能否执行，仍然要经过工具选择、参数校验、权限校验和 HITL。评测上我会重点看 status_accuracy、rewrite_success_rate、unsafe_guess_rate 和 downstream_tool_accuracy，其中最重要的是 unsafe_guess_rate，因为多问一句只是体验问题，擅自猜测并调用工具才是业务风险。
>
> 当前工程没有把这部分代码恢复，只在文档中保留最小方案，原因是项目主线还是 ERP 工具调用、HITL、权限、trace 和 eval。后续如果真实线上 bad case 证明这类上下文指代问题频繁出现，再按这个最小方案小步引入，并用 regression case 保证不回退。
