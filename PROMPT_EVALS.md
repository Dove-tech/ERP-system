# 提示词评测集功能说明

## 1. 背景

本次继续在副本工程中增加提示词评测集功能：

```text
agent-copilot-hitl-prompt-engineering/
```

没有修改原始工程。

用户提供的参考资料位于：

```text
C:\Users\Yi Jiang\Desktop\AI\大模型课程\2020期L3阶段资料\33- Copliot实战项目专题课（二）-26.3.20-云帆\测试用例(qwen-max-0919测试通过).pdf
```

该 PDF 在本地环境中无法可靠抽取正文：

- 没有 `pdftotext`。
- 没有 `pypdf`、`pdfplumber`、`PyMuPDF`。
- 没有 OCR 工具。
- 直接解压 PDF 文本流只能得到极少量 `ActualText`，主体内容疑似是截图或自定义字体编码。

因此本次没有强行自动解析 PDF，而是先搭建可运行的评测集框架，并预置与 qwen-max 验证方向一致的结构化 seed cases。后续可以把 PDF 中的用例人工转录到 JSON 数据集里。

## 2. 新增目录

```text
prompt/evals/
  __init__.py
  runner.py
  datasets/
    human_feedback_intent.json
    tool_selection.json
    tool_summary.json
```

## 3. 本次支持的三类评测

### 3.1 人类反馈意图识别

数据集：

```text
prompt/evals/datasets/human_feedback_intent.json
```

覆盖：

- `confirm`：用户确认执行。
- `abort`：用户取消、不执行。
- `unclear`：用户提问、改参数、表达不明确。

示例：

```json
{
  "id": "intent_confirm_001",
  "feedback": "立即执行",
  "tool_name": "查询产品信息",
  "tool_params": {"name": "苹果"},
  "expected": {"intent": "confirm"},
  "model_output": "{\"intent\":\"confirm\",\"confidence\":0.99,\"reason\":\"用户明确要求立即执行\"}"
}
```

这个数据集可以验证：

- prompt 是否正确渲染。
- JSON 输出解析是否稳定。
- 在线调用模型时，模型是否能分类到预期 intent。

### 3.2 工具选择

数据集：

```text
prompt/evals/datasets/tool_selection.json
```

覆盖：

- 查询产品应该选择产品查询工具。
- 查询订单应该选择订单查询工具。
- 不相关需求应该输出 `None`。

示例：

```json
{
  "id": "tool_select_001",
  "query": "查询名称为苹果的产品信息",
  "tools": [
    {
      "name_for_model": "tool1",
      "name_for_human": "根据产品名称查询产品信息",
      "description": "根据产品名称获取产品基本信息、库存和价格"
    }
  ],
  "expected": {"action": "tool1"},
  "model_output": "{\"action\":\"tool1\",\"reason\":\"用户需要根据产品名称查询产品\"}"
}
```

这个数据集可以验证：

- `tool_selection:v1-qwen` 模板是否正确生成。
- JSON 格式和旧格式 `Action: toolX` 是否都能解析。
- 在线模型是否能选中预期工具。

### 3.3 工具结果总结

数据集：

```text
prompt/evals/datasets/tool_summary.json
```

覆盖：

- 单 API 调用结果总结。
- 多 API 调用链结果总结。

示例：

```json
{
  "id": "summary_001",
  "query": "查询苹果产品的库存",
  "apis": [
    {
      "tool": "根据产品名称查询产品信息",
      "task_description": "查询苹果产品信息",
      "result": "{\"productId\":1,\"name\":\"苹果\",\"quantityInStock\":100,\"price\":10}"
    }
  ],
  "expected": {
    "prompt_contains": ["查询苹果产品的库存", "quantityInStock", "100"],
    "answer_keywords": ["苹果", "100"]
  }
}
```

离线模式验证 prompt 是否包含关键上下文；在线模式会调用模型并检查结果关键词。

## 4. Runner 使用方式

入口：

```text
prompt/evals/runner.py
```

推荐从项目根目录执行：

```bash
python -m prompt.evals.runner --task all --mode replay --model qwen-max
```

### 4.1 参数

```bash
--task
```

可选：

```text
all
human_feedback_intent
tool_selection
tool_summary
```

```bash
--mode
```

可选：

```text
render
replay
online
```

```bash
--model
```

指定模型名，用来选择 prompt profile，例如：

```bash
--model qwen-max
--model deepseek-v3
```

```bash
--output
```

把评测结果写到文件：

```bash
python -m prompt.evals.runner --task all --mode replay --model qwen-max --output prompt/evals/reports/replay-qwen.json
```

## 5. 三种评测模式

### 5.1 render 模式

只渲染提示词，不调用模型。

```bash
python -m prompt.evals.runner --task all --mode render --model qwen-max
```

用途：

- 检查 YAML 模板是否能加载。
- 检查 profile 是否能选到正确版本。
- 检查变量是否正常替换。
- 检查 prompt 是否包含必要上下文。

这个模式最适合 CI 和本地快速检查。

### 5.2 replay 模式

不调用模型，读取数据集中的 `model_output`，测试结构化解析器。

```bash
python -m prompt.evals.runner --task all --mode replay --model qwen-max
```

用途：

- 验证 JSON 输出解析。
- 验证旧格式兜底解析。
- 验证 expected 与解析结果是否一致。

例如工具选择同时支持：

```json
{"action": "tool1"}
```

以及旧格式：

```text
Action: tool1
```

### 5.3 online 模式

真实调用大模型。

```bash
python -m prompt.evals.runner --task human_feedback_intent --mode online --model qwen-max
```

需要配置：

```text
model_api_key 或 DASHSCOPE_API_KEY
model_base_url
model_temperature
model_top_p
```

注意：runner 不再依赖 `utils.config`，避免原工程在缺少 `sim_api_key` 时直接退出。它只从环境变量读取模型配置。

## 6. 当前验证结果

已执行：

```bash
python -m py_compile prompt\evals\runner.py
```

通过。

已执行：

```bash
python -m prompt.evals.runner --task all --mode render --model qwen-max
```

结果：

```text
human_feedback_intent: 6/6
tool_selection: 3/3
tool_summary: 2/2
```

已执行：

```bash
python -m prompt.evals.runner --task all --mode replay --model qwen-max
```

结果：

```text
human_feedback_intent: 6/6
tool_selection: 3/3
tool_summary: 2/2
```

`tool_summary` 在 replay 模式下没有模型输出，所以只做跳过式通过；它的真实答案检查需要使用 `online` 模式。

## 7. 如何把 PDF 测试用例集成进来

由于 PDF 当前无法自动 OCR，建议人工转录成 JSON。

### 7.1 如果是人类反馈意图识别用例

追加到：

```text
prompt/evals/datasets/human_feedback_intent.json
```

格式：

```json
{
  "id": "pdf_intent_001",
  "feedback": "可以，马上执行",
  "tool_name": "工具名称",
  "tool_params": {"参数名": "参数值"},
  "expected": {"intent": "confirm"},
  "model_output": "{\"intent\":\"confirm\",\"confidence\":0.95,\"reason\":\"...\"}"
}
```

### 7.2 如果是工具选择用例

追加到：

```text
prompt/evals/datasets/tool_selection.json
```

格式：

```json
{
  "id": "pdf_tool_select_001",
  "query": "用户问题",
  "tools": [
    {
      "name_for_model": "tool1",
      "name_for_human": "工具中文名",
      "description": "工具描述"
    }
  ],
  "expected": {"action": "tool1"},
  "model_output": "{\"action\":\"tool1\",\"reason\":\"...\"}"
}
```

### 7.3 如果是总结用例

追加到：

```text
prompt/evals/datasets/tool_summary.json
```

格式：

```json
{
  "id": "pdf_summary_001",
  "query": "用户请求",
  "apis": [
    {
      "tool": "工具名",
      "task_description": "子任务描述",
      "result": "{\"字段\":\"结果\"}"
    }
  ],
  "expected": {
    "prompt_contains": ["必须出现在 prompt 里的词"],
    "answer_keywords": ["在线模型答案应包含的词"]
  }
}
```

## 8. 与提示词工程的关系

现在项目已经具备一个基本闭环：

```text
Prompt Registry
  -> Model Profile 选择模板版本
  -> Prompt Eval 渲染检查
  -> Replay 解析检查
  -> Online 模型效果检查
```

这意味着后续修改提示词时，不再只靠人工试一两条，而是可以把历史用例沉淀为评测集。

推荐流程：

1. 从 PDF 或线上失败日志转录 case。
2. 加到 `prompt/evals/datasets/`。
3. 修改 prompt YAML。
4. 先跑 `render`。
5. 再跑 `replay`。
6. 最后有 API key 时跑 `online`。
7. 比较准确率变化后再决定是否采用。
