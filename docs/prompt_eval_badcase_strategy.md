# Prompt 评测与 Bad Case 演进说明

本文档补充说明 Agent 项目中 prompt 评测的工程化使用方式。这里的重点不是“写了几个 prompt”，而是说明 prompt 如何通过评测持续演进，以及 bad case 如何沉淀为回归集，影响后续工程设计。

## 1. Prompt 评测不是一次性验收

Prompt 评测不应该只看当前 prompt 能不能跑通几个样例，而应该体现持续迭代过程：

```text
测试或线上发现 bad case
-> 记录 trace 和错误表现
-> 做根因归类
-> 修改 prompt 或工程逻辑
-> 跑 prompt eval
-> 跑 workflow integration eval
-> 检查新 case 是否修复、旧 case 是否回退
-> 通过后升级 prompt 版本
```

也就是说，prompt 是一个可版本化、可评测、可回滚的工程资产，而不是一次性写完的自然语言说明。

当前工程已经具备基础闭环：

```text
prompt/prompt_registry/
prompt/evals/runner.py
prompt/evals/datasets/
prompt/evals/bad_cases/prompt_bad_cases.json
```

其中 `prompt_registry` 管 prompt 版本，`runner.py` 跑组件级评测，`datasets` 存可执行回归 case，`bad_cases` 存 bad case 归因和处理记录。

## 2. 每次改 Prompt 要比较什么

每次调整 prompt 后，不只看总准确率，还要看分项指标：

- 工具选择准确率是否提升。
- 参数抽取准确率是否提升。
- JSON 可解析率是否下降。
- out-of-scope 请求是否仍然能输出 `None`。
- 写操作是否仍然进入 HITL 确认。
- 原有正确 case 是否出现回归。
- prompt token 数、模型延迟、调用成本是否明显增加。

推荐执行：

```powershell
python -m prompt.evals.runner --task all --mode render --model qwen-max
python -m prompt.evals.runner --task all --mode replay --model qwen-max
python -m prompt.evals.integration_runner
```

如果有稳定的 API key，再抽样跑在线模型：

```powershell
python -m prompt.evals.runner --task tool_selection --mode online --model qwen-max
python -m prompt.evals.runner --task human_feedback_intent --mode online --model qwen-max
```

三种模式的意义不同：

- `render`：检查 prompt 模板和变量渲染。
- `replay`：检查结构化解析器和历史模型输出。
- `online`：检查当前模型在新 prompt 下的真实表现。

## 3. Dev Set、Regression Set 与 Holdout Set

Prompt 迭代时建议把数据集分成三类：

- `dev set`：用于日常调 prompt，可以反复查看和分析。
- `regression set`：沉淀历史 bad case，每次改 prompt 都必须跑。
- `holdout set`：不参与 prompt 调整，只用于最终验证，避免为了几个 bad case 过拟合。

当前工程里的 `prompt/evals/datasets/*.json` 主要承担 replay 和 regression 作用。后续如果评测规模扩大，可以把 case 增加 `split` 字段：

```json
{
  "id": "tool_select_bad_conditional_inventory_first",
  "split": "regression",
  "query": "查询苹果当前库存，后续如果库存不足再下单"
}
```

## 4. Bad Case 不等于马上改 Prompt

出现 bad case 后，不能第一反应就是往 prompt 里加一句规则。需要先归因：

```text
工具没召回 -> RAG / embedding / topK / reranker 问题
工具召回了但选错 -> tool selection prompt 或候选工具描述问题
工具选对但参数错 -> parameter extraction prompt / schema / parser 问题
参数缺失却硬猜 -> 参数校验和 missing_params 分流问题
写操作没确认 -> guardrail / HITL 问题
最终回答编造结果 -> tool_summary prompt / answer grounding 问题
多轮上下文串错 -> context rewrite / memory isolation 问题
```

只有确认是模型理解边界问题时，才优先改 prompt。确定性约束应该下沉到代码规则、schema 校验或 guardrail。

## 5. 一个 Prompt 演进小故事

2025 年 10 月，产品经理在 ERP 工作流测试里提供了一个看起来很普通的 case：

```text
帮我查一下苹果还有多少，如果不够就下 50 件。
```

早期版本的系统把它直接识别成创建订单，调用链类似：

```text
create_order
```

这个结果表面上没有崩溃，但业务上是错误的。用户的真实意图是先查库存，只有库存不足时才考虑下单，而且下单前还要让用户确认。标准流程应该是：

```text
query_product -> query_inventory -> ask_user_confirmation -> create_order
```

我们把这个问题记录成 bad case：

```text
failure_type = tool_timing_error
root_cause = 条件式任务被压缩成直接写操作
```

第一次修复时，我在任务分类 prompt 里加了一条比较粗的规则：“出现库存时优先判断为多任务”。这个改动修好了上面的 case，但很快又引入了回归：

```text
查一下苹果库存
```

这个原本是单工具查询，结果也被拆成了多工具链路，甚至后续试图进入下单确认。这说明第一次 prompt 规则写得太宽，把“库存”关键词错误泛化成了多步骤任务。

最终处理方式不是继续堆更多提示词，而是明确决策边界：

```text
单纯库存查询是单工具任务；
只有同时存在条件表达和后续动作，例如“不够就下单”“不足就补货”“满足条件后调整计划”，才拆成多工具任务。
```

同时在数据集中加入一正一反两个 case：

```text
正例：帮我查一下苹果还有多少，如果不够就下 50 件 -> multi
反例：查一下苹果库存 -> single
```

这些 case 已经补充到：

```text
prompt/evals/datasets/task_classification.json
prompt/evals/datasets/tool_chain.json
prompt/evals/datasets/tool_selection.json
prompt/evals/bad_cases/prompt_bad_cases.json
```

这件事带来的工程影响是：后续每次修改任务分类或工具选择 prompt，都不能只看新 bad case 是否通过，还必须确认普通库存查询没有回退。

## 6. Bad Case 如何影响工程

Bad case 会影响四类工程资产：

1. 数据集：把失败输入、错误 trace、期望 trace 固化成 regression case。
2. Prompt：如果是语义边界问题，调整 prompt 规则、正例和反例。
3. Guardrail：如果是写操作安全、参数越界、缺参硬猜，交给后端确定性规则。
4. Trace：如果定位困难，就补充 trace event，让问题能分清是分类错、召回错、参数错还是总结错。

本工程中的例子：

- `bc_20251018_conditional_order_skipped_inventory` 促使任务分类和工具链路评测增加条件式任务 case。
- `bc_20251019_inventory_query_over_split_regression` 促使评测增加普通库存查询反例。
- `bc_20251102_missing_supplier_hallucinated` 促使参数抽取评测增加“缺供应商不能猜”的 case。
- `bc_20251114_confirm_with_param_change` 促使 HITL 反馈解析从关键词短路改成场景化解析。
- `bc_20251203_poem_wrong_tool` 促使工具选择 prompt 明确支持 `None`，并在集成测试里统计无效工具调用率。

## 7. 面试表达

可以这样描述：

> 我们不是写完 prompt 后人工试几条就结束，而是维护了 prompt eval 和 bad case regression。每次测试或线上发现错误 trace，会先做根因归类：如果是 RAG 召回问题就调 embedding、topK 或 reranker；如果是工具选择边界问题就改 tool selection prompt；如果是参数或写操作安全问题就下沉到 schema 校验和 guardrail。每次 prompt 修改都会跑 render、replay 和 workflow eval，要求新 bad case 修复，同时旧 case 不能回退。这样 prompt 是通过评测持续演进出来的，而不是靠经验堆出来的。
