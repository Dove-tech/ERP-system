# 提示词工程升级说明

## 1. 改造范围

本次没有修改原始工程，已在同级目录复制出新的后端工程：

```text
agent-copilot-hitl-prompt-engineering/
```

前端也复制了一份：

```text
copilot-frontend-hitl-prompt-engineering/
```

本次提示词工程改造只发生在后端副本中，前端接口协议保持不变。

## 2. 本次只做的三个重点升级

为了避免一次性引入过重的体系，本次只选择最关键的三个方向：

1. 提示词注册表：把关键提示词从代码字符串升级为 YAML 模板。
2. 模型 Profile：根据基模自动选择不同提示词版本，例如 qwen 使用 `v1-qwen`。
3. 结构化输出解析：对关键 LLM 输出优先按 JSON 解析，并保留旧格式兜底。

这三个点优先解决当前项目最明显的问题：

- 提示词散落在代码中，不方便维护。
- 切换 DeepSeek/Qwen 时需要改代码或改类。
- 大模型输出格式不稳定，容易影响工具选择和人类反馈判断。

## 3. 新增文件

### 3.1 提示词工程核心

```text
prompt/prompt_engineering.py
```

提供两个核心类：

```python
PromptRegistry
StructuredOutputParser
```

`PromptRegistry` 负责：

- 读取 `prompt_registry/` 目录下的 YAML 模板。
- 根据模型名称识别模型 family，例如 `qwen`、`deepseek`、`generic`。
- 根据 profile 选择提示词版本。
- 渲染 `{{ variable }}` 形式的变量。

`StructuredOutputParser` 负责：

- 从模型输出中提取 JSON。
- 解析人类反馈意图：`confirm`、`abort`、`unclear`。
- 解析工具选择结果中的 `action`。
- 如果 JSON 解析失败，回退到旧的关键词/文本格式解析。

### 3.2 模型 Profile

```text
prompt/prompt_registry/profiles.yaml
```

当前配置：

```yaml
model_profiles:
  generic:
    prompt_versions:
      tool_selection: v1
      tool_summary: v1
      human_feedback_intent: v1

  deepseek:
    prompt_versions:
      tool_selection: v1
      tool_summary: v1
      human_feedback_intent: v1

  qwen:
    prompt_versions:
      tool_selection: v1-qwen
      tool_summary: v1
      human_feedback_intent: v1-qwen
```

含义是：

- `deepseek-v3` 会走 `deepseek` profile。
- `qwen-max`、`qwen-plus` 等会走 `qwen` profile。
- 其他模型走 `generic`。

如果后续新增模型，例如 `glm` 或 `llama`，只需要增加 profile 和对应模板版本。

### 3.3 YAML 提示词模板

新增了三类模板：

```text
prompt/prompt_registry/tool_selection/v1.yaml
prompt/prompt_registry/tool_selection/v1-qwen.yaml
prompt/prompt_registry/tool_summary/v1.yaml
prompt/prompt_registry/human_feedback_intent/v1.yaml
prompt/prompt_registry/human_feedback_intent/v1-qwen.yaml
```

本次没有迁移所有提示词，只迁移了最影响稳定性的三类：

- `tool_selection`：工具选择。
- `tool_summary`：API 调用结果总结。
- `human_feedback_intent`：人类反馈意图识别。

## 4. 修改了哪些代码

### 4.1 `prompt/general_prompts.py`

改造点：

1. `PromptModelHub` 初始化时增加：

```python
self.model_name = model_name or "generic"
self.prompt_registry = PromptRegistry()
```

2. `gen_tool_selection_prompt()` 不再直接使用内置字符串模板，而是从 Registry 渲染：

```python
prompt = self.prompt_registry.render(
    "tool_selection",
    self.model_name,
    {
        "tool_descs": tool_descs,
        "tool_names": tool_names,
        "query": query,
    },
)
```

3. `post_process_tool_selection_result()` 增加 JSON 优先解析。

新模型可以输出：

```json
{"action": "tool1", "reason": "该工具用于查询产品库存"}
```

旧模型仍然可以输出：

```text
Action: tool1
```

两种格式都兼容。

4. `gen_tool_summary_prompt()` 改为从 `tool_summary` 模板生成最终总结提示词。

5. 新增人类反馈提示词方法：

```python
gen_human_feedback_intent_prompt()
post_process_human_feedback_intent_result()
```

这让人类反馈识别也进入统一的提示词工程体系。

### 4.2 `prompt/qwen_model_prompts.py`

改造点：

为 Qwen PromptHub 增加：

```python
self.model_name = model_name
self.prompt_registry = PromptRegistry()
```

这样 Qwen 会根据 `profiles.yaml` 自动选择 `v1-qwen` 模板。

### 4.3 `prompt/prompt_hub.py`

改造点：

非 Qwen 模型创建 `PromptModelHub` 时也传入模型名称：

```python
return PromptModelHub("", model_name)
```

这样 DeepSeek、generic 模型也能走 profile 选择逻辑。

### 4.4 `apis/api_planning_hub.py`

改造点：

在人类反馈意图识别中，保留原来的关键词规则优先逻辑：

```text
关键词命中 -> 直接返回 confirm/abort
关键词未命中 -> 使用 YAML 模板 + 结构化输出解析
结构化识别异常 -> 回退旧提示词逻辑
```

新增逻辑会生成结构化提示词，要求模型输出：

```json
{
  "intent": "confirm",
  "confidence": 0.95,
  "reason": "用户明确表示立即执行"
}
```

然后通过 `StructuredOutputParser.parse_intent()` 解析。

## 5. 改造后的模型切换方式

现在切换模型时，提示词版本会跟随模型自动变化。

例如：

```python
create_prompt_hub("deepseek-v3")
```

会使用：

```text
tool_selection:v1
tool_summary:v1
human_feedback_intent:v1
```

而：

```python
create_prompt_hub("qwen-max")
```

会使用：

```text
tool_selection:v1-qwen
tool_summary:v1
human_feedback_intent:v1-qwen
```

后续如果发现 Qwen 在参数提取上需要更强约束，可以新增：

```text
prompt/prompt_registry/param_extraction/v1-qwen.yaml
```

再在 `profiles.yaml` 中配置即可。

## 6. 当前兼容性设计

本次改造没有强制要求所有模型都必须输出 JSON。

工具选择支持两种格式：

```json
{"action": "tool1"}
```

以及旧格式：

```text
Action: tool1
```

人类反馈意图识别优先支持：

```json
{"intent": "confirm", "confidence": 0.9, "reason": "..."}
```

如果模型只输出：

```text
confirm
```

解析器也会兜底识别。

这种方式可以逐步迁移提示词，而不是一次性破坏原有流程。

## 7. 验证结果

已执行静态编译检查：

```bash
python -m py_compile prompt\prompt_engineering.py prompt\general_prompts.py prompt\qwen_model_prompts.py prompt\prompt_hub.py apis\api_planning_hub.py
```

结果通过。

已执行一次轻量模板渲染测试：

```bash
python -c "from prompt.prompt_hub import create_prompt_hub; h=create_prompt_hub('qwen-max'); print(h.prompt_registry.get_version('tool_selection','qwen-max')); print(h.gen_human_feedback_intent_prompt('立即执行','查询库存',{'id':1})[:80])"
```

输出显示 Qwen 自动选择：

```text
v1-qwen
```

并成功渲染人类反馈意图识别模板。

## 8. 后续建议

下一步可以继续扩展，但不建议一次性全改。

优先级建议：

1. 把 `param_extraction` 迁移到 Registry，并强制 JSON Schema 输出。
2. 把 `root_task_classification` 和 `subtask_context` 迁移到 Registry。
3. 在 `Task` 或日志中记录 prompt_id、version、model、解析结果，方便回溯。
4. 为高风险工具调用设计更严格的人类确认提示词版本。

评测集能力已经追加到 `prompt/evals/`，详细说明见：

```text
PROMPT_EVALS.md
```

## 9. 本次改造的核心价值

改造前：

```text
业务代码里手写提示词
不同模型靠不同 PromptHub 类分散维护
输出格式主要依赖字符串解析
```

改造后：

```text
提示词模板独立成 YAML
模型切换通过 profile 自动选择版本
关键输出 JSON 优先解析并兼容旧格式
```

这为后续接入更完整的提示词工程体系打好了基础，包括版本管理、评测、灰度、模型差异化适配和结构化输出校验。
