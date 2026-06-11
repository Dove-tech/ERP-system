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
