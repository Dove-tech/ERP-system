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

## 附录：Eval Case 类型说明

本工程的 eval case 不是完整线上压测，而是面向 Agent 关键链路的回归测试。每一类 case 都围绕一个可观察能力设计：给定输入、模型输出或 replay 输出、期望结果，然后由 runner 解析并判断是否通过。

### A. `slot_filling_intent`

数据集：

```text
prompt/evals/datasets/slot_filling_intent.json
```

测试目标：

- 验证缺失参数补全场景下，系统能否正确理解用户回复。
- 验证用户回复中的业务字段能否被抽取成真实参数名，例如 `supplierId`，而不是中文描述“供应商”。
- 验证用户同时表达“继续”和“修改参数”时，不会被误判成单纯确认。
- 验证取消和表达不清时不会继续进入工具调用确认。

代表 case：

- `slot_fill_supplier`：用户回复“供应商用 3”。测试系统是否识别为 `provide_info`，并抽取 `supplierId=3`。
- `slot_update_existing_and_fill_missing`：用户回复“可以继续，但数量改成 50，供应商是 2”。测试混合反馈：既有“可以继续”，又有参数修正。正确结果应是 `provide_info`，并抽取 `quantity=50`、`supplierId=2`。
- `slot_abort`：用户回复“不执行了，取消”。测试缺参等待状态下的取消意图。
- `slot_unclear`：用户回复“你看着办吧”。测试没有给出有效参数时，是否保持 `unclear`，继续等待补充。

通过标准：

- `parsed.intent == expected.intent`
- `filled_params` 中 expected 要求的字段和值一致。

对应指标：

- 参数正确率。
- 参数缺失补全成功率。
- HITL 反馈识别准确率。
- 无效继续执行拦截率。

### B. `ambiguity_feedback_intent`

数据集：

```text
prompt/evals/datasets/ambiguity_feedback_intent.json
```

测试目标：

- 验证模糊需求候选方案出来后，用户反馈能否被正确分类。
- 验证用户确认候选、修正候选、取消任务、表达不清四类反馈是否能分开处理。
- 验证“可以，但这次数量改成 50”这类混合反馈不会被简单当成确认。
- 验证修正后的需求能否生成 `revised_query`，供后续 Agent 重新规划。

代表 case：

- `ambiguity_confirm_candidate`：用户回复“确认，继续”。测试是否识别为 `confirm_candidate`。
- `ambiguity_provide_info`：用户回复“可以，但这次数量改成 50，供应商换成 2”。测试是否识别为 `provide_info`，并生成包含新数量和新供应商的 `revised_query`。
- `ambiguity_abort`：用户回复“算了，取消”。测试候选方案确认阶段的取消。
- `ambiguity_unclear`：用户回复“先看看吧”。测试系统是否避免强行推进任务。

通过标准：

- `parsed.intent == expected.intent`
- 如果 expected 包含 `revised_query_contains`，则 `revised_query` 必须包含这些关键片段。
- 如果 expected 包含 `filled_facts`，则解析出的关键事实必须一致。

对应指标：

- 模糊需求澄清成功率。
- 用户修正反馈识别准确率。
- 调用时机合理性。
- 错误执行拦截率。

### C. `human_feedback_intent`

数据集：

```text
prompt/evals/datasets/human_feedback_intent.json
```

测试目标：

- 验证传统工具执行确认阶段的用户意图识别。
- 只处理工具参数已经完整、等待用户确认是否执行的场景。
- 区分 `confirm`、`abort`、`unclear`。
- 避免用户提问或要求修改参数时被误判为确认执行。

代表 case：

- `intent_confirm_001`：用户回复“立即执行”。测试确认调用。
- `intent_confirm_002`：用户回复“可以，继续调用这个接口”。测试语义确认。
- `intent_abort_001`：用户回复“不执行，先停一下”。测试取消执行。
- `intent_abort_002`：用户回复“取消本次任务”。测试任务终止。
- `intent_unclear_001`：用户问“这个参数 productId 是什么意思？”。测试提问不应触发工具执行。
- `intent_unclear_002`：用户说“把数量改成 10 再看看”。测试修改参数不应被当成确认执行。

通过标准：

- 解析出的 `intent` 与 expected 完全一致。

对应指标：

- HITL 确认准确率。
- 高风险工具误执行拦截率。
- 调用时机合理性。

### D. `tool_selection`

数据集：

```text
prompt/evals/datasets/tool_selection.json
```

测试目标：

- 验证用户子任务能否匹配到正确工具。
- 验证没有合适工具时能否返回 `None`，而不是强行选择一个无关工具。
- 验证工具选择解析器能兼容 JSON 输出和旧格式 `Action: toolX`。

代表 case：

- `tool_select_001`：查询苹果产品信息，应选择产品查询工具 `tool1`。
- `tool_select_002`：查询订单 ID 为 1001 的订单信息，应选择订单查询工具 `tool2`。
- `tool_select_003`：写春天的诗，候选 ERP 工具都不适用，应返回 `None`。

通过标准：

- 如果 expected 是具体工具，则 `selected_tool.name_for_model == expected.action`。
- 如果 expected 是 `None`，则不能返回任何工具。

对应指标：

- 工具调用准确率。
- 无效工具调用占比。
- 工具幻觉拦截能力。

### E. `param_extraction`

数据集：

```text
prompt/evals/datasets/param_extraction.json
```

测试目标：

- 验证参数抽取结果是否符合预期。
- 验证已抽取参数和仍缺字段能否被正确区分。
- 目前该 runner 使用数据集中的 `model_params` 和 `model_missing` 做 replay，不真实调用参数抽取模型。

代表 case：

- `pe_order_entry_complete`：订单录入信息完整，预期抽取 `productId`、`quantity`、`deliveryDate`、`region`，且无缺失字段。
- `pe_missing_delivery_date`：用户给了物料和数量，但缺少交期，预期 `deliveryDate` 出现在 missing 列表。

通过标准：

- `model_params == expected.params`
- `sorted(model_missing) == sorted(expected.missing)`

对应指标：

- 参数正确率。
- 必填参数缺失识别率。
- 参数补全触发准确率。

### F. `hallucination_guard`

数据集：

```text
prompt/evals/datasets/hallucination_guard.json
```

测试目标：

- 验证工具调用前的规则护栏是否能按风险等级处理。
- 验证读操作、写操作、异常参数的处理策略是否符合预期。
- 验证负数数量等明显异常参数能否进入澄清，而不是直接调用工具。

代表 case：

- `hg_read_inventory_allow`：查询库存是读操作，参数正常，应 `allow`。
- `hg_write_order_confirm`：创建订单是写操作，参数正常，但需要用户确认，应 `confirm`。
- `hg_negative_quantity_clarify`：创建订单数量为负数，应触发参数违规并 `clarify`。

通过标准：

- `guardrail_action` 与 expected 一致。
- `risk_level` 与 expected 一致。
- 是否存在 violations 与 `has_violations` 一致。

对应指标：

- 工具异常处理成功率。
- 参数幻觉拦截率。
- 高风险操作确认覆盖率。

### G. `ambiguity_resolution`

数据集：

```text
prompt/evals/datasets/ambiguity_resolution.json
```

测试目标：

- 验证系统能否识别“老样子”“上次”“照旧”等模糊请求。
- 验证有可靠上下文或记忆时能否生成候选解释。
- 验证没有可靠上下文时是否要求用户补充信息，而不是直接猜测执行。
- 验证清晰请求不会被误判为模糊请求。

代表 case：

- `ar_old_way_with_memory`：有 pinned facts 或偏好记忆，预期生成候选方案。
- `ar_old_way_without_memory`：没有可靠记忆，预期仍识别为模糊，但不生成可执行 resolved query。
- `ar_clear_request`：清晰请求，预期 `is_ambiguous=false`。

通过标准：

- `is_ambiguous` 与 expected 一致。
- `confidence` 不低于 expected 的最低要求。
- 是否有 `resolved_query` 与 expected 一致。

对应指标：

- 模糊需求识别准确率。
- 记忆使用可靠性。
- 调用时机合理性。

### H. `task_classification`

数据集：

```text
prompt/evals/datasets/task_classification.json
```

测试目标：

- 验证用户请求应被归类为单工具任务还是多工具链任务。
- 单工具任务通常可以直接进入工具选择和参数抽取。
- 多工具任务需要先生成根任务或子任务，再逐步规划工具链。

代表 case：

- `tc_single_inventory_lookup`：库存查询，通常是单工具任务。
- `tc_multi_order_plan`：需要查询订单、库存、供应商并调整计划，属于多工具任务。

通过标准：

- 模型输出规范化后等于 expected 的 `single` 或 `multi`。

对应指标：

- 调用时机合理性。
- 工具链规划准确率。
- 多步任务识别准确率。

### I. `tool_chain`

数据集：

```text
prompt/evals/datasets/tool_chain.json
```

测试目标：

- 验证多工具任务的步骤顺序是否合理。
- 验证 Agent 是否能按“先查事实，再执行写操作”的顺序规划。
- 这是对调用时机合理性的粗粒度离线评测。

代表 case：

- `chain_shortage_plan_adjustment`：缺货计划调整任务，预期链路为 `query_order -> query_inventory -> query_supplier -> update_plan`。
- `chain_progress_update`：生产进度更新任务，预期先查询订单，再更新状态。

通过标准：

- `model_steps == expected.steps`

对应指标：

- 调用时机合理性。
- 工具链顺序准确率。
- 写操作前置查询覆盖率。

### J. `tool_summary`

数据集：

```text
prompt/evals/datasets/tool_summary.json
```

测试目标：

- 验证最终总结 prompt 是否包含用户请求和工具返回的关键事实。
- 在线模式下，验证模型最终回答是否使用了工具结果，而不是凭空总结。
- 该类 case 更接近“工具执行结果利用率”的评测。

代表 case：

- `summary_001`：单 API 查询苹果库存，期望 prompt 和在线回答覆盖“苹果”“100”等关键事实。
- `summary_002`：多 API 查询订单和产品信息，期望最终总结能综合多个工具结果。

通过标准：

- `render` 模式：prompt 必须包含 expected 的 `prompt_contains`。
- `replay` 模式：当前没有固定模型答案，runner 会跳过答案检查并返回通过。
- `online` 模式：真实调用模型后，回答必须包含 expected 的 `answer_keywords`。

对应指标：

- 工具执行结果利用率。
- 最终回答事实覆盖率。
- 最终回答幻觉风险。

### K. Debug 脚本中的覆盖策略

调试脚本：

```text
debug_eval_case.py
```

该脚本选取了多类代表 case，方便在 PyCharm 中单步调试：

- `slot_filling_intent` 覆盖补参、改参、取消、表达不清。
- `ambiguity_feedback_intent` 覆盖确认候选、修正候选、取消、表达不清。
- `human_feedback_intent` 覆盖工具执行确认阶段的确认、取消、unclear。
- `tool_selection` 覆盖正常选工具和无效工具需求。
- `param_extraction` 覆盖参数完整和参数缺失。
- `hallucination_guard` 覆盖读操作放行和异常写参数澄清。
- `tool_chain` 覆盖多工具顺序规划。

推荐调试入口：

```python
passed, detail = runner(case, mode, hub, llm)
```

在这行打断点后 Step Into，就能进入对应类别的真实 runner 函数，观察 prompt 渲染、输出解析和 expected 比对的全过程。
