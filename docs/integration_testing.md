# Agent 工作流集成测试说明

本文档说明本工程新增的集成测试内容，以及如何基于产品经理整理的“常见操作序列和自然语言对应表”获取工具调用准确率、任务完成度、任务准确率等指标。

## 1. 集成测试测什么

已有的 `prompt/evals/runner.py` 更偏向子能力评测，例如工具选择、参数抽取、HITL 反馈识别、幻觉护栏等。集成测试关注的是一条完整业务链路是否走对：

```text
用户自然语言
-> 任务分析
-> 工具选择
-> 参数抽取与补全
-> 工具调用顺序
-> 写操作确认
-> 工具异常处理
-> 最终回答是否使用工具结果
```

因此，集成测试不是只看某一次 LLM 输出是否和 expected 一致，而是看整条 trace 是否符合产品经理定义的标准工作流。

传统软件开发里的单元测试和集成测试仍然适用：

- 单元测试：测试单个函数、单个解析器、单个 guardrail 规则是否正确。
- 集成测试：测试多个模块串起来以后，是否能完成一个真实业务流程。
- Agent Eval：在集成测试基础上，把“自然语言理解、工具调用、参数、时机、最终回答”也纳入指标。

## 2. 新增文件

```text
prompt/evals/integration_runner.py
prompt/evals/integration_datasets/workflows.json
docs/integration_testing.md
```

其中：

- `workflows.json` 是产品经理操作序列表的结构化版本。
- `integration_runner.py` 负责读取 expected workflow 和 actual trace，并计算指标。
- 本文档说明如何维护数据集、执行测试和理解指标。

## 3. 数据集怎么写

每个 case 对应一条常见业务流程。核心字段如下：

```json
{
  "id": "wf_create_order_full_params",
  "workflow": "订单录入",
  "natural_language": "为产品 1001 创建 20 件订单，交期 2026-06-10，供应商 3",
  "expected": {
    "task_status": "completed",
    "operation_sequence": [
      {"tool": "query_product", "params": {"productId": 1001}},
      {"tool": "query_supplier", "params": {"supplierId": 3}},
      {
        "tool": "create_order",
        "requires_confirmation": true,
        "params": {
          "productId": 1001,
          "quantity": 20,
          "deliveryDate": "2026-06-10",
          "supplierId": 3
        }
      }
    ],
    "required_result_facts": ["orderId", "status"],
    "final_answer_contains": ["O-20260602-001", "已创建"]
  },
  "actual_trace": {
    "task_status": "completed",
    "tool_calls": [],
    "used_result_facts": ["orderId", "status"],
    "final_answer": "订单 O-20260602-001 已创建，状态为 created。"
  }
}
```

字段含义：

- `natural_language`：用户真实或模拟的自然语言输入。
- `expected.operation_sequence`：产品经理定义的标准操作序列。
- `expected.params`：每一步工具调用必须携带的关键参数。
- `requires_confirmation`：写操作或高风险操作是否必须先经过用户确认。
- `expected.task_status`：预期最终状态，例如 `completed`、`waiting_user`、`rejected`、`failed_handled`。
- `required_result_facts`：最终回答必须使用的工具结果字段。
- `actual_trace`：系统真实执行后的 trace，也可以先用 replay 方式手工填写。

实际项目中，可以先从以下来源沉淀 `actual_trace`：

- `/api_trace_status` 返回的后端 trace。
- 测试环境接口日志。
- 产品经理或测试同学记录的操作回放。
- 自动化 E2E 脚本跑完后导出的调用链。

## 4. 如何执行集成测试

在 V3 工程根目录执行：

```powershell
python -m prompt.evals.integration_runner
```

也可以按传统软件测试的方式执行 `unittest`：

```powershell
python -m unittest discover -s test/test_integration -p "test_*.py"
```

输出报告保存到文件：

```powershell
python -m prompt.evals.integration_runner --output prompt/evals/reports/integration-workflows.json
```

如果希望在 CI 或本地回归中设置阈值：

```powershell
python -m prompt.evals.integration_runner `
  --min-task-accuracy 0.95 `
  --min-completion 0.98 `
  --max-invalid-tool-call-rate 0
```

当前 runner 是 replay/trace 评测，不会调用大模型。它评估的是“这条实际 trace 是否符合预期工作流”。如果后续要做在线集成测试，可以先跑真实接口生成 trace，再把 trace 写回 `actual_trace` 或改造 runner 自动请求后端接口。

## 5. 指标如何获取

runner 会对每个 case 计算明细，再汇总为全局指标。

### 5.1 工具调用准确率

指标名：

```text
tool_call_accuracy
```

计算方式：

```text
按位置调用正确的工具数 / 预期工具调用数
```

例如预期链路是：

```text
query_order -> query_inventory -> query_supplier -> update_plan
```

实际链路也是这个顺序，则工具调用准确率为 100%。如果第二步误调用了 `query_product`，则第二步不计正确。

该指标主要衡量工具选择是否正确，以及多工具链路是否选中了正确工具。

### 5.2 参数正确率

指标名：

```text
parameter_accuracy
```

计算方式：

```text
实际参数值正确的字段数 / 预期参数字段数
```

例如 `create_order` 预期参数是：

```json
{
  "productId": 1001,
  "quantity": 20,
  "deliveryDate": "2026-06-10",
  "supplierId": 3
}
```

如果实际只把 `quantity` 抽成了 200，其他 3 个字段正确，则该步骤参数正确率为 3/4。

该指标主要衡量参数抽取、参数补全、上下文改写后的参数继承是否可靠。

### 5.3 任务完成度

指标名：

```text
task_completion_rate
```

单 case 计算方式：

```text
(按顺序匹配到的预期操作数 + 最终状态是否匹配) / (预期操作数 + 1)
```

它不是简单看最终是否 `completed`，因为有些流程的正确结果本来就是 `waiting_user`，例如缺少供应商时应该停下来要求补充，而不是强行创建订单。

例如缺少供应商的 case：

```text
expected status = waiting_user
expected operation = ask_user_clarification
```

如果系统正确进入澄清状态，就算任务没有业务完成，也说明流程完成度是 100%。

### 5.4 任务准确率

指标名：

```text
task_accuracy
```

计算方式：

```text
完全通过的 case 数 / case 总数
```

一个 case 完全通过需要同时满足：

- 工具序列完全一致。
- 关键参数完全正确。
- 调用顺序合理。
- 写操作已经经过确认。
- 没有无效工具调用。
- 最终状态符合 expected。
- 最终回答包含 expected 要求的关键信息。
- 需要使用的工具结果字段被最终回答使用。
- 如果发生工具异常，异常被正确处理。

这是最严格的端到端指标，适合面试或项目汇报时表达“完整工作流通过率”。

### 5.5 调用时机合理性

指标名：

```text
timing_reasonableness_rate
```

计算方式：

```text
调用顺序合理且确认动作满足要求的 case 数 / case 总数
```

它主要检查两类问题：

- 顺序问题：例如应该先查订单和库存，再调整生产计划。
- 确认问题：例如 `create_order`、`update_plan` 这类写操作必须在用户确认后执行。

### 5.6 无效工具调用占比

指标名：

```text
invalid_tool_call_rate
```

计算方式：

```text
无效工具调用数 / 实际工具调用数
```

无效调用包括：

- 工具不在 `tool_catalog` 中。
- 工具虽然存在，但不属于该 case 的预期流程。
- 对明显不属于 ERP 的请求仍然调用业务工具。

例如“写一首关于春天的诗”预期不应该调用任何 ERP 工具，如果系统实际调用了 `query_product`，就会计入无效工具调用。

### 5.7 工具执行结果利用率

指标名：

```text
tool_result_utilization_rate
```

计算方式：

```text
最终回答使用到的必需工具结果字段数 / expected.required_result_facts 字段数
```

例如订单创建工具返回：

```json
{
  "orderId": "O-20260602-001",
  "status": "created"
}
```

如果 expected 要求最终回答使用 `orderId` 和 `status`，而最终回答只说“订单创建成功”，没有给出订单号或状态，则利用率会下降。

该指标用于避免模型“看起来回答了”，但没有真正使用工具返回结果。

### 5.8 工具异常处理成功率

指标名：

```text
tool_exception_handling_success_rate
```

计算方式：

```text
已正确处理的异常 case 数 / 发生或预期发生异常的 case 数
```

正确处理包括：

- 没有把异常伪装成成功。
- 最终状态是 `failed_handled` 或其他符合 expected 的状态。
- 最终回答告诉用户异常原因或下一步建议。
- trace 中标记 `exception_handled=true`。

## 6. 如何从产品经理表格转成 case

产品经理表格通常长这样：

```text
自然语言：订单 O-9001 如果库存不足，就查询供应商并调整生产计划
标准操作：查询订单 -> 查询库存 -> 查询供应商 -> 调整生产计划
关键参数：orderId=O-9001, productId=1001, planStatus=adjusted
预期结果：生产计划调整成功，最终回答包含订单号和计划状态
```

转成 `workflows.json` 时按这个顺序处理：

1. 把自然语言写入 `natural_language`。
2. 把标准操作写入 `expected.operation_sequence`。
3. 把每一步必须正确的参数写入 `params`。
4. 如果是写操作，标记 `requires_confirmation=true`。
5. 写出 expected 的最终状态。
6. 写出最终回答必须覆盖的字段或关键词。
7. 跑真实系统或根据测试记录补充 `actual_trace`。
8. 执行 `python -m prompt.evals.integration_runner` 查看指标。

## 7. 与现有 evals 的关系

推荐分层使用：

```text
单元测试
  -> 验证函数和规则

prompt/evals/runner.py
  -> 验证提示词、结构化输出解析、单点 Agent 能力

prompt/evals/integration_runner.py
  -> 验证完整业务工作流和端到端指标

test/test_integration/test_workflow_metrics.py
  -> 用传统 unittest 形式断言集成测试指标阈值
```

如果集成测试失败，再回到子能力 eval 定位问题：

- 工具错了：看 `tool_selection`。
- 参数错了：看 `param_extraction` 或 `slot_filling_intent`。
- 错过澄清：看 `ambiguity_resolution` 或 `ambiguity_feedback_intent`。
- 写操作直接执行：看 `hallucination_guard` 和 HITL intent。
- 最终回答没有用工具结果：看 `tool_summary` 和 answer grounding trace。
