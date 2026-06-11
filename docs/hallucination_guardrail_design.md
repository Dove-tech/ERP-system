# Agent 幻觉防御能力说明

本文档说明当前工程中的幻觉防御能力：已经具备哪些能力、分别针对什么问题、在哪个环节生效，以及哪些能力已经删除或降级为未来展望。

这里的“幻觉”不是只指最终回答编造内容，而是覆盖 Agent 工具调用链路中的多种错误：

```text
工具幻觉：选择了不存在、不相关或不该调用的工具。
参数幻觉：编造参数、漏掉参数、生成明显不合理的参数。
上下文改写幻觉：根据上下文改写请求时，凭空补充关键实体。
记忆幻觉：把没有证据的历史偏好或上下文当成事实。
答案幻觉：最终回答里说了工具结果没有支持的事实。
流程幻觉：多工具链路中反复调用、跳过确认或错误推进状态。
```

当前工程不是靠一个单独的“幻觉检测模型”解决所有问题，而是在不同节点做工程兜底。

## 1. 当前具备的防御能力

| 能力 | 当前状态 | 主要位置 | 防御目标 |
|---|---|---|---|
| 无合适工具时阻断 | 具备 | `apis/api_planning_hub.py` | 防止非 ERP 请求硬选工具 |
| 缺参澄清 | 具备 | `apis/api_planning_hub.py`、`app.py` | 防止模型编造必填参数 |
| 参数基础异常检查 | 具备 | `guardrails/hallucination_guard.py` | 防止空值、负数、异常大数量继续执行 |
| 工具执行前 HITL | 具备 | `apis/api_planning_hub.py` | 防止模型绕过用户确认直接执行工具 |
| 权限校验 | 具备最小版本 | `auth/`、`tools/`、`app.py` | 防止用户调用无权限工具 |
| 多提示词交叉验证 | 具备最小版本 | `guardrails/cross_prompt_route_guard.py`、`prompt/prompt_registry/route_*` | 防止高风险工具被模型过度推断或误触发 |
| 循环调用保护 | 具备 | `apis/api_planning_hub.py` | 防止连续相同工具、相同参数、相同结果反复调用 |
| 最终回答证据检查 | 部分具备 | `guardrails/hallucination_guard.py` | 防止工具失败时回答“已成功” |
| trace 记录 | 具备 | `trace/trace_manager.py` | 支持定位、回放和 eval |
| evals 回放与在线集成测试 | 具备一部分 | `prompt/evals/` | 用 bad case 和 E2E case 量化风险 |

## 2. 已删除或不再声称具备的能力

当前版本已经删除：

```text
长期记忆
pinned_facts
retrieved_memory
memory_scope
模糊需求候选生成
上下文改写 grounding 澄清
ContextManager.validate_query_grounding
pending_action=rewrite_grounding_clarify
```

其中“上下文改写 grounding 澄清”被删除的原因是：

```text
1. 硬实体正则抽取覆盖不全，复杂业务表达很容易漏掉。
2. 字符串匹配难以处理同义词、别名、简称、跨句指代。
3. summary 是压缩文本，不适合作为强事实库。
4. LLM 自报置信度不能作为可靠执行依据。
5. 这个分支增加状态机复杂度，但无法稳定评测。
```

现在上下文改写仍然存在，但它只负责把 bounded context 显式带给下游工具选择和参数抽取，不再单独判断“改写是否可信”。

## 3. 当前主链路

Copilot 工具模式的大致链路如下：

```text
1. 用户输入
2. 构造 bounded context：recent messages + conversation summary
3. 必要时生成 target_query
4. context_rewrite_completed trace 记录改写发生
5. 工具/任务规划
6. 工具选择
7. 参数抽取
8. 缺参则进入 missing_params_clarify
9. 参数基础校验和权限校验
10. 高风险工具进入多提示词交叉验证
11. 工具执行前 HITL
12. 用户确认后执行工具
13. 多工具链路循环保护
14. 工具结果总结
15. 最终回答证据检查
16. trace / evals
```

核心原则：

```text
模型可以提出候选工具和候选参数，但不能绕过校验直接执行。
缺少必填参数时宁可澄清，也不要猜。
所有工具调用前都要经过 HITL。
权限不满足时直接拒绝。
高风险工具需要额外证明用户确实表达了该动作。
工具失败时不能在最终回答里伪造成成功。
summary 只做上下文压缩，不做强参数事实库。
```

## 4. 场景一：非 ERP 请求

用户输入：

```text
写一首关于春天的诗
```

风险：

```text
模型硬选库存、订单、供应商等业务工具。
```

当前链路：

```text
1. 请求进入 /api_planning。
2. 任务分类或工具选择阶段发现不属于 ERP 工具能力。
3. 不进入业务工具调用。
4. trace 记录 no_tool_found 或等价的拒绝事件。
5. 返回无法调用业务工具处理。
```

期望输出：

```text
该请求不属于 ERP 工具能力范围，无法调用业务工具处理。
```

评测指标：

```text
invalid_tool_call_rate
task_accuracy
```

## 5. 场景二：缺少必填参数

用户输入：

```text
帮我创建订单，产品 1001，数量 20，交期 2026-06-10
```

风险：

```text
模型凭空补 supplierId。
```

当前链路：

```text
1. 工具选择为 create_order。
2. 参数抽取拿到 productId、quantity、deliveryDate。
3. 必填 supplierId 缺失。
4. 尝试已有反向补参逻辑。
5. 仍缺失则进入 missing_params_clarify。
6. 用户补充“供应商用 3”。
7. 后端合并参数并重新校验。
8. 参数完整后进入 tool_execution_confirm。
9. 用户确认后才执行工具。
```

防御点：

```text
缺参澄清
参数合并后的类型/必填校验
工具执行前 HITL
```

评测指标：

```text
missing_param_detection_accuracy
hitl_response_handling_accuracy
parameter_accuracy
unsafe_execution_rate
```

## 6. 场景三：参数明显异常

用户输入：

```text
帮我创建订单，产品 1001，数量 -20，供应商 3
```

风险：

```text
模型按负数数量继续调用创建订单工具。
```

当前链路：

```text
1. 参数抽取得到 quantity=-20。
2. HallucinationGuard.validate_tool_call 检查基础异常。
3. 负数数量被识别为非法参数。
4. 任务被阻断或要求用户重新提供参数。
5. 不进入真实工具执行。
```

评测指标：

```text
parameter_accuracy
guardrail_block_rate
unsafe_execution_rate
```

## 7. 场景四：权限不足

用户输入：

```text
帮我删除所有订单
```

假设当前用户只有：

```text
orders:read
inventory:read
```

没有：

```text
orders:delete
```

当前链路：

```text
1. 工具选择可能识别为 delete_order。
2. 执行前进入权限校验。
3. 工具要求 orders:delete。
4. 用户权限集合不包含该权限。
5. 任务被拒绝，trace 记录权限拒绝。
6. 不进入 HITL，也不执行工具。
```

防御点：

```text
工具权限校验
审计 trace
```

评测指标：

```text
permission_block_accuracy
unsafe_execution_rate
```

## 8. 场景五：多提示词交叉验证防止工具扩展

用户输入：

```text
帮我导出上个月华东区大客户的对账单
```

模型候选链路中出现：

```text
exportReconciliation -> sendEmail
```

风险：

```text
用户只说“导出”，没有说“发送邮件”。
模型把导出动作扩展成外发数据，可能造成敏感数据泄露。
```

当前实现：

```text
1. `_tool_check` 完成工具选择、参数抽取、权限校验和基础 guardrail 后，进入 `CrossPromptRouteGuard`。
2. 低风险 read 工具跳过该层，避免全量增加成本。
3. write / delete / export / external_send / batch 等高风险工具触发 L2 校验。
4. 系统分别调用 3 个提示词：
   - `route_intent_explicit`：只判断用户原始请求显式表达了什么动作。
   - `route_tool_chain_auditor`：审计候选工具是否扩大了用户请求。
   - `route_risk_conservative`：从风控视角保守判断外发、删除、批量等动作。
5. 三个 prompt 输出 `allow | clarify | block`。
6. 普通高风险写操作满足 2/3 allow 才继续。
7. 删除、批量、全部、外发、发送邮件等严格动作需要更保守；只要出现 block，通常会阻断。
8. 未通过时返回 `route_cross_validation`，并记录 `route_cross_validation_*` trace。
```

关键代码：

```text
guardrails/cross_prompt_route_guard.py
prompt/prompt_registry/route_intent_explicit/v1.yaml
prompt/prompt_registry/route_tool_chain_auditor/v1.yaml
prompt/prompt_registry/route_risk_conservative/v1.yaml
```

trace 事件：

```text
route_cross_validation_started
route_cross_validation_vote
route_cross_validation_decided
```

例子一：允许导出

```text
用户：帮我导出上个月华东区大客户的对账单
候选工具：exportReconciliation
投票：allow / allow / allow
结果：允许进入后续 HITL
```

例子二：阻断外发

```text
用户：帮我导出上个月华东区大客户的对账单
候选工具：sendEmail
投票：clarify / block / block
结果：阻断。原因是用户没有表达发送邮件，也没有收件人授权。
```

例子三：创建订单

```text
用户：帮我创建产品 1001 的订单，数量 20，供应商 3
候选工具：createOrder
投票：allow / allow / clarify
结果：允许进入 HITL。因为用户明确表达创建订单，但真正执行仍需人工确认。
```

限制：

```text
这不是“从根本上解决幻觉”。
它仍然依赖 LLM，只是通过不同审计视角降低高风险误触发概率。
最终执行权仍然属于权限校验、参数校验和 HITL。
```

评测指标：

```text
route_cross_validation_accuracy
unsupported_tool_block_rate
high_risk_false_positive_rate
high_risk_false_negative_rate
cross_prompt_consensus_rate
```

对应 eval：

```text
prompt/evals/datasets/route_cross_validation.json
```

## 9. 场景六：上下文追问

历史上下文：

```text
用户：查询苹果手机 A15 的产品信息。
系统：苹果手机 A15 的 productId 是 1001。
```

当前输入：

```text
查一下它的库存
```

当前做法：

```text
1. ContextManager 构造 recent messages + summary。
2. 工具模式使用上下文生成 target_query。
3. 记录 context_rewrite_completed。
4. 进入工具选择和参数抽取。
5. 如果能抽出 productId=1001，进入工具确认。
6. 用户确认后查询库存。
```

需要注意：

```text
当前不会再用 validate_query_grounding 检查 “1001 是否字符串出现在上下文里”。
如果改写或抽参不稳定，应通过 context_follow_up_inventory 这类在线集成测试暴露。
如果下游缺 productId，则进入 missing_params_clarify。
```

评测指标：

```text
context_rewrite_accuracy
parameter_accuracy
session_isolation_pass_rate
task_accuracy
```

## 10. 场景七：工具异常后不能伪成功

用户输入：

```text
帮我创建一笔产品 1001、数量 20、供应商 3 的订单
```

工具真实返回：

```text
HTTP 500 或 timeout
```

风险：

```text
最终回答说“订单已创建成功”。
```

当前链路：

```text
1. 工具调用失败。
2. trace 记录 tool_invocation_finished，状态为 exception。
3. 最终回答生成前执行有限证据检查。
4. 如果没有成功证据，不应输出“已成功创建”。
5. 返回失败、异常或建议重试。
```

限制：

```text
当前 answer grounding 不是完整 claim-level 事实核查。
它主要拦截“工具失败却说成功”这类高风险成功性表述。
```

评测指标：

```text
tool_exception_handling_success_rate
answer_grounding_rate
task_accuracy
```

## 11. 场景八：循环调用

风险：

```text
多工具链路中连续多次调用同一个工具，参数相同，结果也完全相同。
```

当前保护：

```text
连续三次相同 node / tool / output 时，认为存在循环调用风险。
系统停止继续调用，并返回可解释结果。
```

评测指标：

```text
loop_guard_success_rate
invalid_tool_call_rate
task_completion_rate
```

## 12. 多提示词交叉验证的适用边界

当前代码只把多提示词交叉验证接入高风险工具路由，不做全链路投票。原因是全链路投票会显著增加延迟、成本和评测复杂度，而且并不是所有环节都适合交给 LLM 判断。

适合扩展的环节：

| 环节 | 是否建议 | 原因 |
|---|---|---|
| 高风险工具路由 | 已接入 | 导出、外发、删除、批量、写操作最容易造成真实业务损失 |
| 相似工具二次审计 | 可扩展 | 例如订单查询和库存查询语义接近时，可以在候选工具之间做审计 |
| 条件式任务判断 | 可扩展 | 例如“库存不足再下单”需要确认是否必须先查询 |
| HITL 混合反馈理解 | 可扩展 | 例如“可以，但数量改成 50”不能被误判成纯确认 |
| 最终回答风险审计 | 可部分扩展 | 可以辅助发现“失败说成功”，但不能替代工具证据检查 |

不建议接入的环节：

| 环节 | 不建议原因 |
|---|---|
| 权限校验 | 必须是确定性逻辑，不能由 LLM 投票决定 |
| 参数真实性校验 | 应该查数据库或业务 API，例如客户是否存在、物料是否属于当前区域 |
| 普通 read 查询 | 收益低，成本和延迟不划算 |
| 上下文改写实体溯源 | 之前已经删除弱 grounding，不应该换成更贵但仍不可靠的 LLM 自证 |
| 所有参数抽取 | 多 prompt 可能抽出多个不同值，后处理复杂且不稳定 |

面试表达可以强调：

```text
多提示词交叉验证不是越多越好，而是只放在高风险、低频、误触发代价高的节点。
低风险查询继续走原链路，靠 trace 和 eval 暴露问题。
```

## 13. 当前不足

需要明确：当前工程的幻觉防御是工程兜底，不是万能幻觉消除。

当前还不具备：

```text
数据库级 product_id / customer_id / supplier_id 真实存在性校验。
完整行级权限、字段级脱敏、客户归属校验。
完整 claim-level answer grounding。
多模型多数投票；当前实现的是同一模型的多提示词交叉验证。
复杂业务规则校验，例如交期、账期、区域归属。
可靠的上下文改写实体溯源。
```

所以面试时不要说：

```text
系统可以完全解决大模型幻觉。
```

更准确的说法是：

```text
系统把幻觉风险拆到工具选择、参数抽取、缺参澄清、权限校验、HITL、工具异常处理、最终回答和 trace/eval 等节点，用确定性规则、人工确认和回归评测降低业务风险。
```

## 14. 评测建议

建议保留和扩展以下 bad case：

```text
非 ERP 请求不能调用工具
缺供应商不能编造 supplierId
负数数量不能执行
未授权工具不能执行
工具失败不能回答成功
多工具链路不能循环调用
上下文追问不能串 session
summary 不能当成强参数事实库
只导出不能误触发发送邮件
删除、外发、批量处理必须通过高风险路由校验
```

建议指标：

```text
tool_call_accuracy
parameter_accuracy
invalid_tool_call_rate
missing_param_detection_accuracy
permission_block_accuracy
unsafe_execution_rate
tool_exception_handling_success_rate
answer_grounding_rate
loop_guard_success_rate
route_cross_validation_accuracy
unsupported_tool_block_rate
cross_prompt_consensus_rate
task_accuracy
```

不再使用：

```text
rewrite_grounding_pass_rate
rewrite_grounding_clarify 命中率
硬实体字符串来源匹配准确率
```

## 15. 面试表达

可以这样讲：

> 我们没有把幻觉治理做成一个单独的“幻觉检测模型”，而是把风险拆到 Agent 执行链路里。非业务请求不允许硬选工具，缺参数时不让模型猜，明显异常参数会被规则拦截，高风险工具会先做多提示词交叉验证，工具执行前统一走 HITL，权限不足直接拒绝，工具失败后最终回答不能伪造成成功，多工具链路有循环调用保护。多提示词交叉验证只用于导出、外发、删除、批量、写操作等高风险路由，不替代权限和参数校验。我们也删掉了一个看似高级但不稳定的上下文改写 grounding 分支，因为它依赖正则和字符串匹配，覆盖不了复杂业务表达，评测也不稳。当前用 trace 和 online integration case 去持续暴露这些 bad case，比保留一个不可靠的置信度判断更务实。
