# 面试包装规划 V1：完整能力蓝图版

> 当前版本说明：长期记忆、`pinned_facts`、`retrieved_memory` 和模糊需求候选生成已经从工程实现中删除。本文中相关内容只作为早期包装稿和未来展望参考，不再代表当前代码能力。

## 项目定位

项目可以包装为一个面向零售供应链业务的企业级 Agent Copilot 平台。用户可以用自然语言完成产品查询、供应商查询、订单创建、库存管理、促销活动等操作。系统基于 API 工具库、向量检索、大模型规划和 HITL 人工确认，实现从用户意图理解、工具选择、参数抽取、缺参补全、工具调用到最终总结的完整 Agent 工作流。

面试中不建议只说“我做了一个能调 API 的 Copilot”，而是应强调：

> 我做的是一个面向零售供应链业务的可控型 Agent Copilot 平台。核心挑战不是让模型调用 API，而是如何在多工具、多轮对话、上下文过长、用户需求模糊、工具调用有风险的情况下，保证 Agent 可控、可评测、可追踪、可迭代。

## 1. PromptOps：提示词工程体系

项目中已经存在大量提示词，而且用户可能切换不同基模，例如 DeepSeek、Qwen、小模型等。不同模型对相同提示词的遵循能力、JSON 稳定性、中文理解、任务拆解风格都不同，因此提示词不能继续硬编码在业务逻辑里。

可包装为：

- Prompt Registry：提示词模板版本化。
- Model Profile：不同模型自动选择不同 prompt 版本。
- Structured Output：工具选择、参数抽取、人类反馈等关键节点使用 JSON 输出。
- Prompt Eval：修改提示词后跑评测，防止老功能漂移。
- Prompt Trace：记录 prompt_id、version、model、输入、输出、解析结果。

面试亮点：

> 我不是把 prompt 写死在代码里，而是把 prompt 当成可版本化、可评测、可灰度的工程资产管理。

## 2. 记忆系统：短期记忆、摘要记忆

当前版本建议包装为两层会话记忆：

- 短期记忆：当前 session 最近 N 轮对话，直接进入上下文窗口。
- 摘要记忆：上下文变长后，对历史对话做滚动摘要，保留目标、约束、已确认信息、已调用工具、未完成事项。

为什么这么做：

- 短期记忆保证当前对话连贯。
- 摘要记忆解决上下文长度限制。
- 当前不做跨 session 长期记忆，不把历史偏好自动恢复成工具参数。
- 长期记忆可以作为未来展望，但需要补齐来源可信度、权限边界和评测闭环。

面试亮点：

> 我们没有把所有历史对话都塞进 prompt，而是按使用场景分层管理：短期上下文直接使用，长对话滚动摘要。跨会话长期记忆暂时不进入当前版本，避免模型把不可靠历史偏好当成业务事实。

## 3. Session 隔离和多租户隔离

需要引入：

- user_id
- session_id
- task_id
- tenant_id

隔离策略：

- 短期记忆按 session_id 隔离。
- 任务状态按 task_id 隔离。
- 工具权限按 user_id / role 隔离。
- 工具向量检索只用于工具候选召回，用户会话上下文不进入长期向量记忆。

面试亮点：

> 向量检索不能只看语义相似度，还必须做 metadata filter，否则很容易出现 session 污染、用户数据串扰和多租户安全问题。

## 4. 模糊需求处理：当前删除，未来展望

用户说：

```text
按照老样子帮我订一下
```

当前版本不会从长期记忆解释“老样子”“按上次方案”等表达，也不会生成模糊需求候选订单。

当前处理方式：

1. 按正常参数抽取检查需求是否完整。
2. 缺少产品、数量、供应商、日期等关键参数时，进入缺参澄清。
3. 用户明确补齐参数后，才继续工具选择、参数校验和 HITL。
4. 不把未确认的历史偏好、摘要内容或模型猜测当成工具参数。

未来如果重新引入模糊需求候选，需要补齐长期记忆存储、候选来源标注、候选 grounding、用户确认和专项 eval。

面试亮点：

> 当前版本没有上线模糊需求候选生成。我的处理策略是先把缺参澄清、上下文改写、全工具 HITL 和评测闭环做稳，长期记忆解释“老样子”这种能力放到后续规划，避免在没有评测闭环时制造记忆幻觉。

## 5. 长上下文处理

上下文管理不是简单截断，而是状态构造：

- recent_messages：最近几轮完整对话。
- summary_memory：历史摘要。
- tool_trace：已调用工具和结果摘要。

面试亮点：

> 我把上下文管理看成状态构造问题，而不是简单截断。当前版本只使用当前 query、最近消息和会话摘要，不把长期记忆或 pinned facts 作为参数事实库。

## 6. 评测体系

建议构建分层评测：

- Prompt Eval：提示词渲染、结构化解析、模型输出格式。
- Tool Selection Eval：工具是否选对。
- Param Extraction Eval：参数是否抽对。
- Planning Eval：单/多任务和工具链是否正确。
- Safety Eval：危险参数、提示注入、高风险操作是否拦截。
- HITL Eval：确认、取消、修改参数是否识别正确。
- E2E Eval：完整任务最终是否成功。
- Online Eval：用户采纳率、重试率、人工介入率、平均耗时、成本。

面试亮点：

> Agent 评测不能只看最终答案，因为错误可能发生在工具选择、参数抽取、规划链路、执行安全等中间环节。我把评测拆成分层指标，并把 badcase 回流到 eval dataset。

## 7. 成本与速度

可以设计：

- 规则优先：明确确认/取消不用调大模型。
- 小模型优先：简单意图分类、参数合法性先走小模型。
- 大模型兜底：复杂任务规划、总结再走大模型。
- 缓存：高频 query、工具选择结果、检索结果、总结结果。
- 并行工具调用：无依赖的多个查询可并行。

面试亮点：

> 我不是所有步骤都用最强模型，而是根据任务复杂度和风险做模型路由。高频简单任务走规则或小模型，复杂低频任务才走大模型。

## 8. 安全与可控性

建议包装：

- 工具风险分级：read/write/delete/payment。
- 高风险工具强制 HITL。
- 参数合法性校验：负价格、负库存、异常数量。
- 权限校验：用户是否有调用工具的权限。
- 执行前 dry-run：展示即将调用的工具和参数。
- 执行后审计日志。

面试亮点：

> Agent 的能力越强，越需要可控性。我的设计里工具调用不是模型说了算，而是经过权限、参数、安全、人工确认多层校验。

## 9. 可观测性、Trace 和 Badcase 回流

每个 task_id 记录完整 trace：

- 用户输入
- prompt version
- model name
- 工具候选集
- 工具选择结果
- 参数抽取结果
- HITL 用户反馈
- API 请求和响应摘要
- 最终总结
- token 成本
- 耗时

面试亮点：

> 我们可以对每一次 Agent 执行做 replay。Badcase 不只是看日志，而是能复现当时的 prompt、工具候选、参数和模型输出。

## 10. LangGraph / Workflow 化

当前版本可以理解为自研轻量状态机。后续可迁移到 LangGraph：

- classify_task
- rewrite_query
- select_tool
- extract_params
- safety_check
- human_confirm
- invoke_tool
- summarize
- evaluate

面试表达：

> 当前版本是自研轻量状态机，下一步可以迁移到 LangGraph，把中断恢复、checkpoint、conditional edge、trace 管理标准化。

## 11. MCP / Tool Registry

当前项目已经有 OpenAPI 解析和工具上传，可以包装成企业内部 Tool Registry：

- OpenAPI -> Tool Schema -> Vector Index -> Runtime Invocation
- 工具版本管理
- 工具权限
- 工具健康检查
- MCP adapter

面试亮点：

> 我们没有把工具硬编码在 prompt 里，而是把 OpenAPI 自动解析成工具资产，进入工具注册中心，再通过向量召回 + rerank + LLM 精选完成工具选择。

## 12. 最终八大模块

1. Tool Registry：OpenAPI 工具注册、权限、向量索引、rerank
2. Agent Workflow：任务分类、规划、工具调用、HITL、状态流转
3. PromptOps：提示词版本、模型 profile、结构化输出、灰度
4. Memory System：短期记忆、摘要记忆、session 隔离
5. Context Manager：长上下文压缩、query rewrite、grounding 校验
6. Safety Guardrail：权限、参数校验、提示注入、高风险确认
7. Eval & Badcase：prompt eval、agent eval、链路评测、badcase 回流
8. Cost & Observability：缓存、模型路由、trace、成本统计

## 13. 推荐实施顺序

第一阶段：

1. 记忆系统 + session 隔离
2. Context Manager + 缺参澄清 + 上下文改写 + 全工具 HITL
3. Agent 级评测集
4. Trace 记录和 badcase 回流

第二阶段：

1. 成本与速度：缓存、模型路由
2. 安全 Guardrail 完善
3. 工具协议/MCP adapter 文档化

第三阶段：

1. LangGraph 版本 workflow
2. 多 Agent 分工
3. 在线指标看板
4. 长期记忆与模糊需求候选生成
