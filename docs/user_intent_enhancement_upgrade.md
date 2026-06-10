# 用户意图增强升级说明

本文档描述当前 `agent-copilot-hitl-v3-engineering` 在 HITL 用户反馈理解上的保留能力。

当前版本不做全局“是否调用工具”的意图识别，也已经删除“模糊需求澄清”能力。系统只在已有任务进入等待状态后，根据 `pending_action` 判断用户本次反馈应该如何处理。

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
```

这样设计的原因是：ERP 工具调用涉及权限、参数校验、HITL 和审计。当前版本优先保证等待状态机可解释、可测试，而不是把所有自然语言理解能力都塞进一个复杂意图识别模块。

## 2. Pending Action 分流

`Task` 中使用 `pending_action` 和 `pending_payload` 表示当前正在等待用户处理的具体业务动作。

当前支持的 pending action：

```text
missing_params_clarify      等待用户补充工具必填参数
rewrite_grounding_clarify   等待用户澄清上下文改写结果
tool_execution_confirm      等待用户确认是否执行工具调用
```

`process_human_feedback()` 会先按 `pending_action` 分流：

```text
missing_params_clarify -> 参数补全反馈处理器
rewrite_grounding_clarify -> 上下文改写澄清处理器
tool_execution_confirm -> 工具执行确认处理器
```

已经删除：

```text
ambiguity_confirm
```

也就是说，当前不再支持“系统根据长期记忆生成模糊需求候选方案，然后等待用户确认候选”的链路。

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

## 4. 上下文改写澄清

`rewrite_grounding_clarify` 用于处理这种情况：

```text
用户当前请求依赖上下文
-> 系统尝试改写成完整请求
-> 改写后的关键实体在当前 query、recent messages 或 summary 中找不到来源
-> 进入澄清，不直接调用工具
```

例子：

```text
历史中同时出现了 A 供应商和 B 供应商。
用户：把它导出来。
系统改写：导出 B 供应商的对账单。
grounding 发现“B 供应商”来源不够稳。
系统：请确认是否导出 B 供应商的对账单，或补充正确供应商。
```

用户反馈处理规则：

```text
短确认：按 candidate_query 继续
取消：终止任务
其他输入：拼接为用户澄清并重新规划
```

这个分支不是长期记忆，也不是模糊需求候选生成。它只校验“上下文改写是否有来源”。

## 5. 工具执行确认

`tool_execution_confirm` 是最终工具执行前的确认阶段。

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

不会直接把参数改成 50 后执行。这样更保守，避免最终确认阶段混入参数变更导致误执行。

## 6. 已删除能力

本次裁剪删除了以下能力：

```text
memory/ambiguity_resolver.py
prompt/prompt_registry/ambiguity_feedback_intent/v1.yaml
prompt/evals/datasets/ambiguity_resolution.json
prompt/evals/datasets/ambiguity_feedback_intent.json
pending_action=ambiguity_confirm
```

删除原因：

```text
依赖长期记忆和复杂候选 grounding
评测成本高
与当前“只做上下文压缩，不做长期记忆”的方案不一致
容易在面试中被追问长期记忆来源、事实可靠性和候选解释正确率
```

当前遇到“老样子”“按上次方案”这类表达时，不再自动生成候选方案。系统应通过缺参、上下文改写澄清或工具参数校验进入更保守的澄清路径。

## 7. Eval 覆盖

保留的 eval 数据集：

```text
prompt/evals/datasets/human_feedback_intent.json
prompt/evals/datasets/slot_filling_intent.json
```

覆盖场景：

```text
工具执行确认：confirm / abort / unclear
缺参补充：provide_info / abort / unclear
混合反馈：可以继续，但数量改成 50，供应商是 2
无效反馈：你看着办吧
```

验证命令：

```powershell
python -m py_compile app.py apis\api_planning_hub.py prompt\prompt_engineering.py prompt\evals\runner.py
python -m prompt.evals.runner --task human_feedback_intent --mode replay --model qwen-max
python -m prompt.evals.runner --task slot_filling_intent --mode replay --model qwen-max
```

## 8. 未来展望

长期记忆和模糊需求候选可以作为后续方向，但不属于当前版本。

未来如果重新引入，需要同时补齐：

```text
长期记忆的存储模型
记忆写入来源和审批策略
记忆过期与冲突处理
候选方案 grounding
模糊需求候选确认 HITL
对应 eval 和 online integration case
```

面试表达时应说清楚：

```text
当前版本没有上线长期记忆和模糊需求候选生成，只保留短期 session history、conversation summary 和 pending 状态下的反馈解析。长期记忆属于后续规划，必须等事实来源、权限边界和评测体系成熟后再引入。
```
