# 用户意图增强升级说明

本文档描述 `agent-copilot-hitl-v3-engineering` 在 HITL 用户反馈理解上的增强改造。目标是让系统在等待用户输入时，不再只把反馈理解成“执行/不执行”，而是根据当前等待场景分别识别确认、补充信息、取消和表达不清。

## 1. 改造背景

原流程的 HITL 判断主要服务于工具调用前确认：工具已经选好、参数已经完整，系统等待用户确认是否执行。

实际业务中还有两类需要用户继续补充的信息：

- 模糊需求澄清：用户说“上次”“老样子”“照旧”等，系统生成候选解释后，需要用户确认或修正。
- 参数缺失补全：工具已经选中，但参数抽取和 API 反向补参后仍缺少必填字段，需要用户直接补充缺失参数。

如果这些场景继续复用“是否执行”的判断，用户回复“供应商用 3”“不对，数量改 50”“可以，但供应商换成 2”时容易被误判成 unclear 或 confirm，导致补充信息丢失。

## 2. 统一 Pending Action 分流

`Task` 中使用 `pending_action` 和 `pending_payload` 表示当前正在等待用户处理的具体业务动作。

当前支持的 pending action：

```text
missing_params_clarify      等待用户补充工具必填参数
ambiguity_confirm           等待用户确认或修正模糊需求候选方案
rewrite_grounding_clarify   等待用户澄清上下文改写结果
tool_execution_confirm      等待用户确认是否执行工具调用
```

`process_human_feedback()` 会先按 `pending_action` 分流：

```text
missing_params_clarify -> 参数补全反馈处理器
rewrite_grounding_clarify -> 上下文改写澄清处理器
ambiguity_confirm -> 模糊需求反馈处理器
tool_execution_confirm -> 原工具执行确认处理器
```

这样用户反馈会在正确场景下解释，不再统一走“执行/不执行”。

## 3. 参数不足时请求用户补充

实现位置：

```text
apis/api_planning_hub.py
app.py
prompt/prompt_registry/slot_filling_intent/v1.yaml
```

工具参数处理流程调整为：

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

用户可见文案改为：

```text
还需要补充以下信息才能继续：物流供应商Id。请直接提供这些信息，或取消本次任务。
```

用户补充后，系统用 `slot_filling_intent` prompt 解析本轮回复，只输出：

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

LLM 只负责把用户回复转为结构化字段。参数合并、类型转换、枚举校验和必填校验仍由后端规则完成。若仍缺字段，继续等待用户补充；若参数完整，进入工具调用前确认。

## 4. 模糊需求反馈识别

实现位置：

```text
app.py
prompt/prompt_registry/ambiguity_feedback_intent/v1.yaml
```

模糊需求候选方案等待用户反馈时，新增独立 prompt，不复用工具执行确认 prompt。

输入只包含：

- 原始用户请求
- 当前候选方案
- 用户本次回复

输出结构：

```json
{
  "intent": "confirm_candidate | provide_info | abort | unclear",
  "revised_query": "按上次方案再下一单，但数量改成 50，供应商换成 2",
  "filled_facts": {
    "quantity": 50,
    "supplierId": 2
  },
  "confidence": 0.92,
  "reason": "用户确认方向但修正了关键字段"
}
```

处理规则：

- `confirm_candidate`：用户确认候选方案，继续按候选请求规划。
- `provide_info`：用户补充或修正候选方案，使用 `revised_query` 继续规划。
- `abort`：用户取消任务。
- `unclear`：继续要求用户确认或补充。

如果用户说“可以，但供应商换成 2”，系统会优先识别为 `provide_info`，不会把“可以”简单当作确认并丢失后半句。

## 5. Prompt 简化原则

本次改造按需求去掉了补充信息识别 prompt 中的复杂记忆上下文。

`slot_filling_intent` 不传入：

```text
pinned_facts
summary
retrieved_memory
```

`ambiguity_feedback_intent` 也不额外拉取记忆，只使用 `pending_payload` 中已有的候选方案和用户本次回复。

这样可以降低 prompt 长度、降低解释链路复杂度，并避免在补参场景中把旧记忆误当成用户当前确认。

## 6. 验证覆盖

新增 eval 数据集：

```text
prompt/evals/datasets/slot_filling_intent.json
prompt/evals/datasets/ambiguity_feedback_intent.json
```

覆盖场景：

- 用户直接补充缺失参数。
- 用户同时修改已有参数并补齐缺失参数。
- 用户取消参数补全任务。
- 用户补参表达不清。
- 用户确认模糊需求候选方案。
- 用户对候选方案补充或修正。
- 用户取消模糊需求任务。
- 用户模糊反馈表达不清。

验证命令：

```powershell
python -m py_compile app.py apis\api_planning_hub.py prompt\prompt_engineering.py prompt\evals\runner.py
python -m prompt.evals.runner --task slot_filling_intent --mode replay --model qwen-max
python -m prompt.evals.runner --task ambiguity_feedback_intent --mode replay --model qwen-max
```

## 7. 面试表达

可以这样解释这次升级：

> 原来的 HITL 只解决“工具是否执行”的确认问题，但真实 ERP Copilot 中，等待用户输入有多种状态。V3 增加了 pending_action 分流：工具确认、模糊需求确认、上下文改写澄清、参数缺失补全分别使用不同的意图识别策略。参数补全和模糊澄清只基于当前 pending 场景解析用户回复，不拉复杂记忆上下文；LLM 只做结构化抽取，最终是否完整和是否合法由后端规则校验。这样用户说“供应商用 3”或“可以，但数量改成 50”时，系统可以继续推进任务，而不是误判为确认或失败。
