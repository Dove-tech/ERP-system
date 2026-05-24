# 面试包装规划 V3：制造业 ERP Agent 工程化版

## 1. 项目时间背景与包装边界

项目背景设定为 **2025 年年底到 2026 年上半年** 的大型制造企业内部 ERP Agent Copilot 试点项目。

业务背景是：ERP 是制造企业管理销售订单、生产计划、物料库存、供应商协同、生产进度和任务分配的核心系统。但在真实企业里，ERP 并不等于完全自动化。很多业务步骤仍然依赖人工在系统里录入数据、推进流程、分配任务和检查状态。

本项目可以包装成：

> 一个面向制造业 ERP 场景的可控型业务 Copilot 原型。它不是替代整个 ERP，也不是完全自主的通用 Agent，而是围绕“自然语言到 ERP API 的可靠调用”做工程化试点，重点解决工具注册、工具选择、参数抽取、缺参补全、人工确认、执行链路追踪、提示词工程和基础评测。

面试中建议强调：

> 项目的核心挑战不是让大模型回答问题，而是让 Agent 在制造业 ERP 这种高约束业务系统里，可靠、安全、可追踪地推进业务流程。尤其是写操作、流程推进、生产计划调整、任务分配这类动作，不能只靠模型自由发挥，必须用 workflow、权限、参数校验、HITL、trace 和 eval 把能力边界管住。

这比“我做了一个能调 ERP API 的聊天机器人”更有深度，也更符合当前 Agent 工程从 Demo 走向落地的主线。

## 2. 业务背景润色

业务可以这样描述：

> 某大型制造企业在 ERP 系统中处理日常生产和履约流程时，约 60% 的步骤仍然需要人工参与。比如员工要手动录入客户订单，填写产品规格、数量和交付日期；生产过程中要手动更新进度，并在设备故障、原材料短缺等异常情况下调整生产计划；排产后还要手动把任务分配给不同生产线或人员。人工操作带来了流程耗时、错误率、经验差异和经济损失。

典型场景可以包装为：

- 客户订单录入：把客户订单中的产品规格、数量、交付日期录入 ERP。
- 生产计划调整：根据订单优先级、库存状态、设备状态调整生产计划。
- 物料与库存查询：查询某个物料或产品的库存、替代料和供应情况。
- 供应商与采购协同：查询供应商是否能按期供货，或根据区域、能力、评分选择供应商。
- 生产进度更新：将某个生产订单的状态更新为生产中、暂停、已完成等。
- 任务分配：根据生产计划把任务分配给生产线或员工。
- 模糊指令处理：用户说“按老样子排一下”“照上次那批来”“这个订单加急处理”时，系统需要结合上下文和记忆生成候选方案，而不是直接执行。

当前代码里的业务 API 主要是产品、订单、物流供应商、库存等域。面试中可以解释为：

> 当前实现使用产品、订单、供应商、库存这一组 ERP 核心对象做原型验证。在制造业语境下，产品可以映射为物料或成品主数据，订单可以映射为客户订单或生产相关单据，供应商可以映射为物料供应商或物流服务商。项目重点不在某个具体 ERP 表结构，而在自然语言驱动 ERP API 的可控执行链路。

这个解释能把现有实现和制造业甲方背景对齐，避免业务口径和代码数据不一致。

## 3. 项目真实掣肘

为了面试可信，不建议把项目讲成全自动 ERP 智能体。更真实的掣肘是：

- ERP 是存量系统，只能通过已有 OpenAPI 暴露的接口调用，不能直接改 ERP 库。
- 工具数量会随着 ERP 模块增加而增长，不能把所有接口硬编码进 prompt。
- 不同模型在 JSON 稳定性、中文业务理解、任务拆解风格上差异明显。
- 写操作风险高，例如创建订单、更新生产进度、调整计划、删除产品、取消订单。
- 制造业字段强约束多，例如物料编号、产品规格、数量、交期、供应商、生产线、设备状态。
- 业务人员提问常有省略、指代和模糊表达。
- 试点阶段真实用户量有限，不适合一开始建设复杂在线指标平台。
- 预算有限，不能所有节点都调用最强模型。

因此项目更适合包装为：

> 企业内部 ERP Copilot 试点，而不是通用 Agent 平台。第一阶段优先保证工具调用准确、写操作可控、上下文不串、prompt 可回归、badcase 可复盘。

## 4. V3 重点包装的 6 个能力

在 V2 基础上保留 6 个核心模块，但把业务语境改为制造业 ERP，并把 Agent 工程主线讲清楚：

1. PromptOps：提示词工程与模型适配。
2. Tool Registry + ERP Skills：ERP OpenAPI 工具资产管理，以及面向业务流程的 Skill 封装。
3. Controlled Agent Workflow：可控流程加局部 LLM 决策。
4. Scoped Memory & Context Manager：有边界的记忆和上下文构造。
5. Eval & Trace：分层评测、执行链路追踪和 badcase 回流。
6. Hallucination Control, Safety, Cost & Latency：幻觉治理、安全护栏、成本和速度优化。

暂时不建议重点包装成：

- 完整多 Agent 平台。
- 完整 LangGraph 重构。
- 完整异步任务队列。
- 完整自进化长期记忆平台。
- 大规模在线指标和 A/B 平台。
- 全自动 ERP 流程机器人。

这些是更高阶的平台化增强，而不是当前试点版必须完成的范围。

## 5. 当前项目对外包装的已完成能力

面试时不要把 V2 的核心能力讲成“以后再补”。更稳的口径是：当前项目已经完成了试点版工程闭环，只是每个模块都采用轻量实现，没有做成大型平台。

- PromptOps：已完成 YAML Prompt Registry、Model Profile、结构化输出解析和基础 prompt eval。
- Tool Registry：已完成 OpenAPI 上传、解析工具、存 MongoDB、写入 Milvus、向量召回、rerank 和 LLM 精选。
- ERP Skills：已把高频 ERP 业务流程包装成可复用能力，例如订单录入、库存查询、供应商选择、生产计划调整和人工确认执行。
- Controlled Workflow：已通过 `/api_planning` 管理任务生命周期，后端异步处理，任务状态落库。
- HITL：已在工具执行前进入等待确认状态，前端轮询任务状态，并在需要确认时让用户反馈。
- Scoped Memory & Context Manager：已按 session/task 组织上下文，组合 recent messages、任务状态、关键事实和摘要信息。
- Eval & Trace：已用 `nodes/edges` 展示执行链路，并用 eval dataset 覆盖工具选择、人类反馈意图和结果总结。
- Hallucination Control & Safety：已做工具候选约束、结构化输出解析、缺参/类型校验、无工具拒答、写操作确认、执行链路记录和基础 eval。
- Cost & Latency：已做规则短路、工具候选缩小、模型调用控制和低风险缓存策略。

面试中可以承认的不是“核心能力没做”，而是“当前是试点版轻量实现，后续可以平台化”：

- Trace 后续可以从轻量链路记录升级为完整 OpenTelemetry/Agent Trace 体系。
- Memory 后续可以从 scoped memory 升级为带压缩、冲突消解和过期机制的长期记忆系统。
- Workflow 后续可以迁移到 LangGraph，获得标准 checkpoint、interrupt 和 replay。
- Eval 后续可以从离线分层评测升级为线上指标、A/B 和自动 badcase 采样。

这样既能体现项目已经具备完整度，又不会把试点项目夸成大型商业化平台。

## 6. PromptOps：为什么是第一优先级

制造业 ERP 场景里，prompt 不能散落在业务代码里。原因有三个：

- 用户可能切换 DeepSeek、Qwen 或更小的本地模型。
- ERP 字段和流程强约束多，模型输出格式不稳定会直接影响工具调用。
- 工具选择、参数抽取、HITL 意图识别这类节点需要可回归。

当前方案：

- Prompt Registry：把关键提示词 YAML 化和版本化。
- Model Profile：根据模型 family 选择不同 prompt 版本。
- Structured Output Parser：关键节点优先 JSON 解析，兼容旧格式兜底。
- Prompt Eval：修改 prompt 后跑 render/replay/online 检查。

技术选型理由：

- 项目是企业内部试点，不需要一开始引入复杂 PromptOps 平台。
- YAML + profile 改造成本低，能保留原 PromptHub 调用方式。
- 不同模型差异可以用模板版本管理，而不是在代码里写大量 if/else。
- 结构化输出是 Agent 工程落地的基础，后续可以升级为更严格的 JSON Schema 或模型原生 Structured Outputs。

面试表达：

> PromptOps 是项目里最先工程化的部分，因为模型切换是明确需求。我的做法不是把 prompt 写死在业务逻辑里，而是把 prompt 当成可版本化、可评测、可适配模型的工程资产。这样后续换模型、调 prompt、回归 badcase 都有抓手。

## 7. Tool Registry + ERP Skills：工具资产和业务能力分层

制造企业的 ERP 接口往往很多，涉及订单、物料、库存、供应商、生产计划、设备和任务分配。直接把所有 API 描述塞进 prompt 不现实。

当前项目的底层工具链可以包装为：

```text
OpenAPI JSON
-> Tool Schema
-> MongoDB 结构化存储
-> Milvus 语义向量索引
-> Reranker 重排序
-> LLM 在候选工具内精选
-> Runtime Invocation
```

这部分仍然建议保留 Tool Registry 的说法，因为它解决的是 **底层 ERP API 如何被管理、检索和安全调用** 的问题。Skill 则是更上层的业务封装，解决的是 **如何把多个工具、提示词、流程、记忆、确认策略和评测用例组合成一个可复用业务能力** 的问题。

技术选型理由：

- MongoDB 存工具结构化元数据，方便按 tool_id、operationId、method、path 查询。
- Milvus 存工具语义向量，解决工具数量增长后的粗召回问题。
- reranker 解决向量召回 TopK 排序不稳的问题。
- LLM 只在候选工具内选择，降低 prompt 长度、成本和误选概率。
- OpenAPI 自动注册能适配 ERP 存量系统，减少手工维护工具描述。

### 7.1 为什么不只讲 Skill，而要保留 Tool

因为 Skill 不是 API 调用本身，Skill 需要调用 Tool。

可以把三层关系讲清楚：

```text
Tool：原子 API 能力
例如：查询库存、创建订单、更新生产进度、查询供应商

Tool Registry：工具资产管理层
负责工具注册、schema、检索、权限、风险级别、版本和 trace

ERP Skill：业务能力层
把 prompt、上下文、记忆、多个工具、HITL、guardrail、eval 组合成一个可复用流程
```

如果只说 Skill，不讲 Tool Registry，面试官可能会追问：Skill 里的工具从哪里来？怎么选？怎么管权限？怎么做评测？怎么避免 prompt 里塞满接口？所以更可信的包装是：

> 底层是 Tool Registry，上层是 ERP Skills。Tool Registry 负责工具资产治理，Skill 负责业务流程复用。

### 7.2 ERP Skills 的包装方式

2026 年前后，Skill 已经成为 Agent 工程里的热门表达。它的价值是把可复用的任务能力封装起来，而不是每次都从零让模型规划。

本项目可以包装出几类 ERP Skills：

- 客户订单录入 Skill：抽取客户订单字段，校验规格、数量、交期，调用订单创建工具，执行前 HITL。
- 生产计划调整 Skill：结合订单优先级、库存、供应商和生产线状态，生成计划调整方案，确认后调用更新工具。
- 物料库存查询 Skill：识别物料或成品，调用库存查询和替代料查询工具，输出库存风险说明。
- 供应商选择 Skill：根据交付区域、供应能力、评分、状态和历史偏好推荐供应商。
- 生产进度更新 Skill：识别生产订单和目标状态，校验状态流转是否合法，确认后更新进度。
- 模糊需求澄清 Skill：处理“按老样子”“照上次那批来”“这个订单加急”这类需求，生成候选方案而不是直接执行。

一个 Skill 的内部结构可以这样讲：

```text
skill_id: production_plan_adjustment
description: 处理生产计划调整
inputs: 用户自然语言 + session context + pinned facts
prompts: 任务识别、槽位抽取、澄清、确认、总结
tools: 订单查询、库存查询、供应商查询、生产计划更新
memory_policy: 只读取确认过的偏好，不写入高风险推断
guardrails: 数量、交期、产线状态、权限、HITL
eval_cases: 正常调整、缺料、设备故障、模糊需求、越权操作
trace_events: skill_start, tool_selected, params_extracted, human_confirmed, tool_invoked, skill_done
```

这样包装更贴近 2026 年的 Agent 工程表达，也比只说“我做了工具调用”更有深度。

### 7.3 MCP 和 Skill 的关系

MCP 可以作为后续标准化接入层，但它不替代 Tool Registry 和 Skill。

推荐表达：

```text
Tool Registry：内部工具资产和治理层
ERP Skill：业务能力组合层
MCP Server Adapter：把内部工具或部分 Skill 标准化暴露给外部 Agent 客户端
```

当前试点阶段更合理的口径是：

> 项目先做内部 Tool Registry 和 ERP Skills，因为核心问题是 ERP 工具治理和业务流程可控。MCP 是后续标准化接口层，如果要让更多 Agent 客户端或 IDE 统一调用 ERP 能力，可以在 Tool Registry 外面封装 MCP Server Adapter。

不要说“用了 MCP”来硬蹭概念；说“预留 MCP adapter”更稳。

面试表达：

> ERP API 数量一多，问题就从“能不能调用工具”变成“如何管理工具资产，以及如何把工具组合成稳定业务能力”。我的设计是底层做 Tool Registry，把 OpenAPI 自动注册成可检索、可评测、可审计的工具；上层做 ERP Skills，把订单录入、生产计划调整、库存查询、供应商选择这类业务流程封装成可复用能力。MCP 则是后续对外标准化暴露这些能力的协议层。

## 8. Controlled Agent Workflow：可控优先

制造业 ERP 场景不适合包装成完全自主 ReAct Agent。更合理的说法是：

> 项目采用 workflow + 局部 LLM 决策的混合模式。流程骨架是确定的，但在任务分类、工具选择、参数抽取、缺参补全、结果总结等节点使用大模型。

推荐讲的执行链路：

```text
用户请求
-> 构造上下文
-> 任务分类
-> 工具召回与选择
-> 参数抽取
-> 缺参补全或澄清
-> 安全校验
-> 人工确认或 dry-run
-> 工具调用
-> 任务状态更新
-> trace 记录
-> 记忆更新
-> 最终总结
```

为什么不用完全自主 Agent：

- ERP 写操作风险高，不能让模型自行决定是否执行。
- 制造业流程强约束多，必须做参数和权限校验。
- 工具调用链需要可展示、可复盘、可审计。
- 试点阶段目标是把高频流程做稳，而不是追求全自动。

为什么仍然需要 LLM：

- 业务人员输入不规范，规则无法覆盖所有自然语言表达。
- 多工具任务需要动态拆解，例如先查物料，再查库存，再生成生产计划调整建议。
- 缺参补全需要结合上下文，例如从订单里补产品规格和交期。
- 最终结果需要转换成业务人员能理解的说明。

面试表达：

> 我没有选择完全自主 Agent，而是用可控 workflow 包住模型决策点。模型负责理解、规划和总结，但真正执行前必须经过工具边界、参数校验、权限校验和人工确认。

## 9. Scoped Memory：克制地做制造业业务记忆

记忆系统要体现深度，但不能包装成全能长期记忆。制造业 ERP 场景更适合 scoped memory。

推荐三层记忆：

### 9.1 短期记忆

服务当前 session，保存最近几轮对话和当前任务状态：

- 用户输入。
- 系统回复。
- task_id。
- 当前等待确认的工具。
- 已抽取参数。
- 用户确认或取消记录。

建议存储：

- 试点阶段：MongoDB。
- 高并发阶段：Redis 做热数据，MongoDB 落盘。

### 9.2 摘要记忆

长对话时做滚动摘要，保留：

- 当前业务目标。
- 用户确认过的约束。
- 已调用工具。
- 工具结果摘要。
- 未完成任务。
- 风险点和等待用户确认的事项。

### 9.3 长期业务偏好记忆

只存低风险、明确确认过的偏好：

- 常用产品或物料。
- 常用供应商。
- 常用交付城市或工厂。
- 常用生产线。
- 常用订单数量。
- 用户明确说过的业务偏好。

不存：

- 未确认的模型推断。
- 敏感商业数据。
- 临时任务参数。
- 高风险操作结果。
- 没有来源的模糊偏好。

为什么不纯向量记忆：

- ERP 关键字段需要结构化和可校验。
- 向量召回可能召回相似但错误的历史信息。
- 记忆污染比召回不到更危险。
- 订单、物料、供应商、交期这类字段更适合结构化保存。

面试表达：

> 我没有追求通用长期记忆，而是做 scoped memory。短期记忆保证当前 session 连贯，摘要记忆解决长上下文，长期记忆只保存用户确认过的低风险业务偏好。制造业 ERP 场景里，记忆系统最大的风险不是记不住，而是记错和串用。

## 10. Session 隔离：先做简单但有效

核心目标：

```text
不同用户、不同 session、不同任务之间的上下文、记忆和任务状态不能串。
```

最小设计：

- user_id。
- session_id。
- task_id。
- memory_scope。
- tenant_id 预留。

隔离策略：

- 短期记忆按 user_id + session_id 隔离。
- 任务状态按 task_id 隔离，并关联 user_id/session_id。
- 长期偏好按 user_id + memory_scope 隔离。
- 向量召回必须带 metadata filter。
- 后续做 SaaS 化时再启用 tenant_id。

面试表达：

> 记忆系统第一优先级不是召回效果，而是隔离。ERP 场景里如果把 A 用户的订单上下文召回给 B 用户，问题比没有记忆更严重。所以我会先保证 user/session/task 维度不串，再谈长期记忆召回质量。

## 11. 长上下文处理：状态构造，而不是无限塞历史

长上下文不是简单扩大窗口，也不是把历史全塞进 prompt。推荐讲成 Context Manager：

```text
final_context =
  system_instruction
  + current_user_query
  + recent_messages
  + task_state
  + pinned_facts
  + summary_memory
  + retrieved_memory
  + tool_trace_summary
```

在制造业 ERP 场景里需要 pin 住的关键事实：

- 客户订单号。
- 产品或物料编码。
- 产品规格。
- 数量。
- 交付日期。
- 工厂或生产线。
- 供应商。
- 生产进度。
- 用户确认过的计划调整。

为什么需要 pinned facts：

- 摘要可能丢细节。
- 向量召回可能引入噪声。
- ERP 字段一旦错，会直接导致错误工具调用。
- 用户确认过的关键字段应该结构化保存，并强制进入后续上下文。

面试表达：

> 我把长上下文处理看成状态构造问题，而不是截断问题。最新消息、任务状态、关键事实、摘要和相关记忆有不同优先级。尤其是物料编码、数量、交期、生产线这些 ERP 字段，不能只靠模型摘要，必须结构化 pin 住。

## 12. 模糊需求处理：结合记忆，但不自动执行

制造业 ERP 里常见模糊表达：

```text
按老样子排一下
照上次那批来
这个订单加急处理
还是用之前那个供应商
按上周计划调整
```

处理流程：

1. 判断缺少哪些槽位，例如产品、规格、数量、交期、生产线、供应商。
2. 查询当前 session、任务状态和长期偏好记忆。
3. 生成候选解释和候选参数。
4. 给出来源说明，例如来自上次确认订单或用户偏好。
5. 如果置信度低，反问关键缺失字段。
6. 如果涉及写操作或流程推进，必须 HITL 确认。
7. 用户确认后才调用 ERP 写接口。

示例回复：

```text
我理解你说的“按老样子”可能是：产品为 A 型组件，数量 200，交付日期按上次同类订单设置为 7 天后，优先使用 2 号生产线和上次确认过的供应商。请确认是否按这个方案生成生产计划调整单？
```

面试表达：

> 模糊需求不能让 Agent 猜完就执行。记忆只能作为候选解释来源，不能替代用户确认。尤其是 ERP 写操作，必须把模型推断变成可审查的候选方案，再由用户确认。

## 13. Eval & Trace：从 prompt 级走向 agent 链路级

当前项目可以包装为已经从 prompt eval 扩展到轻量 agent eval。也就是说，不只验证提示词能否渲染和解析，还验证 Agent 链路里的关键决策点。

已具备的评测层次：

- human feedback intent eval。
- tool selection eval。
- tool summary eval。
- render/replay/online 三种模式。
- task classification eval：单工具还是多工具任务。
- param extraction eval：ERP 字段是否抽取正确。
- safety eval：危险参数、越权操作、提示注入是否拦截。
- ambiguity eval：模糊需求是否生成候选解释而不是直接执行。
- tool chain eval：多工具链路是否合理。
- HITL eval：确认、取消、改参数是否识别正确。
- E2E eval：完整任务是否到达预期状态。

Trace 可以包装为轻量独立 schema，`nodes/edges` 是其前端可视化表现：

- trace_id。
- task_id。
- user_id。
- session_id。
- query。
- prompt_id/version/model。
- tool_candidates。
- selected_tool。
- extracted_params。
- missing_params。
- safety_check_result。
- human_feedback。
- api_request_summary。
- api_response_summary。
- final_answer。
- latency_ms。
- token_usage。
- cost_estimate。
- error_type。

`nodes/edges` 可以继续作为前端可视化层，但不应是唯一的 trace 数据结构。

面试表达：

> Agent 评测不能只看最终答案。错误可能发生在任务分类、工具选择、参数抽取、缺参补全、安全校验、人工确认或工具调用任一节点。所以我把评测拆成分层指标，并把每次执行沉淀为可复盘 trace，badcase 可以回流成 eval dataset。

## 14. Safety Guardrail：制造业 ERP 必须做

ERP Agent 最核心的安全原则：

```text
模型可以建议和规划，但不能绕过业务校验直接执行。
```

建议做工具风险分级：

- read：查询订单、查询库存、查询供应商。
- write：创建订单、更新进度、调整计划、分配任务。
- delete/cancel：取消订单、删除物料、删除供应商。
- high_risk_process：生产计划调整、资源重新分配、异常停线处理。

业务参数校验：

- 数量不能为负。
- 价格不能为负。
- 交付日期不能早于当前可生产周期。
- 生产线不能处于停机状态。
- 库存不足不能直接创建生产或发货动作。
- 供应商状态必须可用。
- 异常大订单需要二次确认。
- 用户角色必须有对应工具权限。

HITL 策略：

- 查询类低风险工具可以直接执行。
- 写操作必须确认。
- 删除、取消、生产计划调整需要二次确认。
- 模糊需求生成候选方案后必须确认。

面试表达：

> 制造业 ERP 的错误成本很高。我的设计里模型不会直接拥有执行权，而是只能生成建议、候选工具和候选参数。真正执行前要经过参数校验、权限校验、风险分级和 HITL。

## 15. Hallucination Control：Agent 幻觉治理

这个项目原本对幻觉的处理不是完全没有，而是偏“隐式防御”：通过工具候选约束、参数校验和人工确认减少模型乱调用工具的概率。但如果面试要体现深度，需要把它包装成一套面向 ERP Agent 的 hallucination control pipeline。

### 15.1 当前项目已有的幻觉处理

当前项目对幻觉的处理主要分布在几个环节：

1. 工具边界约束

系统不是让模型凭空编造接口，而是先从 Tool Registry 里召回候选工具，再让模型在候选工具中选择。如果没有合适工具，提示词要求输出 `None`，后端在没有工具时直接停止任务并提示用户换个问法。

2. 工具检索 grounding

ERP API 先通过 OpenAPI 解析成工具，存 MongoDB 和 Milvus。用户请求先经过向量召回、rerank，再进入 LLM 精选。这样能降低模型凭空选择不存在工具的概率。

3. 结构化输出解析

工具选择、人类反馈意图等关键节点优先使用 JSON 或固定格式解析。例如工具选择只接受候选 action，人类反馈只接受 `confirm/abort/unclear`。解析失败时不会直接执行高风险动作。

4. 参数缺失和类型校验

参数抽取后会检查必填字段、空值、整数/浮点类型转换。如果必填参数缺失，会进入补参流程；补不出来时停止任务，而不是让模型猜一个参数继续执行。

5. 异常参数和提示注入判断

工具调用前会判断参数是否异常，例如负价格、负数量、空参数、参数不在请求中等，并把这类情况视为风险信号。

6. HITL 人工确认

真正调用工具前，系统会把工具名和参数展示给用户确认。用户明确确认才执行，取消或反馈不明确时不会直接调用。

7. 执行链路可视化和 eval

当前 `nodes/edges` 已经能展示工具调用链，prompt eval 也覆盖了工具选择、人类反馈意图和结果总结。这使得 badcase 可以回看是工具选错、参数错、用户确认错还是总结错。

因此对外包装时，不说“只靠 prompt 防幻觉”，而是把这些分散机制统一归纳为一套独立的幻觉治理层。

### 15.2 ERP Agent 常见幻觉类型

制造业 ERP 场景里的幻觉不只是“回答编事实”，更常见的是 Agent 行动链路中的幻觉：

- Tool hallucination：模型选择或编造一个不存在的 ERP API。
- Parameter hallucination：模型编造物料编码、供应商 ID、数量、交期、生产线。
- Fact hallucination：模型把工具返回里没有的信息写进最终回答。
- Memory hallucination：模型把未确认的历史偏好当成事实。
- Workflow hallucination：模型跳过必要步骤，例如未查库存就建议排产。
- Permission hallucination：模型假设用户有权限执行某个写操作。

面试时可以强调：

> Agent 幻觉治理不能只盯最终回答，真正危险的是工具选择、参数抽取、记忆召回和流程推进中的幻觉。ERP 场景里一个错误参数可能导致错误订单、错误排产或错误任务分配。

### 15.3 升级后的幻觉治理链路

可以把项目包装成六层防线：

```text
User Query
-> Skill Intent Boundary
-> Tool Grounding
-> Structured Planning
-> Param Validation
-> Evidence-grounded Answer
-> HITL / Guardrail Escalation
-> Trace & Eval Feedback
```

#### 第一层：Skill Intent Boundary

先判断用户请求属于哪个 ERP Skill，例如订单录入、库存查询、生产计划调整、供应商选择。如果请求不属于已支持 Skill，就拒绝执行或转为澄清，而不是让模型自由规划。

作用：

- 防止模型把不支持的业务强行映射到某个工具。
- 限制 Agent 的行动空间。
- 给后续工具选择和参数抽取提供业务边界。

#### 第二层：Tool Grounding

工具必须来自 Tool Registry。模型只能在候选工具集合中选择，不能编造工具名、URL 或 operationId。

升级包装：

- 向量召回 + rerank 作为粗筛。
- LLM 只在候选工具内精选。
- 工具选择结果必须是候选 action。
- 候选为空或置信度低时输出 `no_tool_found`。
- 工具风险级别和权限随工具元数据进入上下文。

#### 第三层：Structured Planning

任务分类、工具选择、参数抽取、安全检查、人类反馈意图都采用结构化输出。

推荐结构：

```json
{
  "decision": "select_tool",
  "selected_tool": "tool3",
  "confidence": 0.86,
  "evidence": ["候选工具描述中包含库存查询"],
  "missing_slots": [],
  "risk_level": "read"
}
```

这样可以明确区分：

- 模型选择了什么。
- 置信度是多少。
- 依据来自哪里。
- 缺失哪些槽位。
- 是否需要升级到人工确认。

#### 第四层：Param Validation

ERP 参数不能只靠模型抽取，必须做确定性校验。

建议校验：

- 必填字段完整。
- 类型正确。
- 枚举值合法。
- 数值范围合法。
- 日期和交期合理。
- 供应商状态可用。
- 生产线状态可用。
- 库存和产能满足要求。
- 高风险参数变更触发二次确认。

如果模型抽出的参数没有来源或校验失败，应进入澄清，而不是自动补一个看似合理的值。

#### 第五层：Evidence-grounded Answer

最终回答必须基于工具返回、任务状态、确认记录和结构化记忆，不能自由补充 ERP 中没有的信息。

建议回答规则：

- 查询结果只引用 API response 中出现的字段。
- 总结中标注“未查询到”“接口未返回”“需要人工确认”。
- 对截断结果说明限制。
- 对模型推断使用“可能/候选方案”，不能说成事实。
- 写操作完成后只汇报工具返回的执行状态，不编造业务结果。

#### 第六层：HITL / Guardrail Escalation

当出现以下情况，系统必须升级到人工确认或拒绝执行：

- 工具选择置信度低。
- 参数缺失或来源不明。
- 涉及写操作、删除、取消、生产计划调整。
- 记忆召回和当前输入冲突。
- 用户请求模糊，例如“按老样子”“照上次那批”。
- 工具返回和模型计划不一致。
- 权限不足或风险级别过高。

### 15.4 Trace 和 Eval 中如何评估幻觉

幻觉治理需要能被评测，而不是只靠 prompt 约束。

Trace 中增加：

- selected_skill。
- selected_tool 是否来自候选集。
- tool_selection_confidence。
- param_source：来自用户输入、工具返回、记忆、人工确认。
- unsupported_claims：最终回答中不被证据支持的字段。
- hallucination_type：tool、param、fact、memory、workflow、permission。
- guardrail_action：allow、clarify、confirm、reject。

Eval 数据集增加：

- unsupported_tool_case：用户请求不在工具能力内，应该拒绝或澄清。
- wrong_param_case：模型抽取了用户没说过的物料 ID，应该拦截。
- missing_evidence_case：最终回答包含 API 未返回的信息，应该判错。
- ambiguous_memory_case：“按老样子”只能生成候选方案，不能直接执行。
- workflow_skip_case：生产计划调整前未查库存或产线状态，应该判错。
- permission_case：无权限用户请求写操作，应该拒绝。

面试表达：

> 我把幻觉治理拆成 tool hallucination、parameter hallucination、fact hallucination、memory hallucination 和 workflow hallucination。对应的治理方法不是单靠 prompt，而是工具候选约束、结构化输出、确定性参数校验、证据化回答、HITL 升级和 trace/eval 回流。

## 16. Cost & Latency：先做轻量策略

试点阶段不建议一开始引入复杂异步队列。成本和速度可以先从轻量策略做起：

### 16.1 规则短路

明确确认或取消不用调大模型：

```text
立即执行 -> confirm
不执行 -> abort
取消 -> abort
```

### 16.2 模型路由

- 简单意图识别：规则或小模型。
- 工具选择：候选较少时小模型，复杂时大模型。
- 参数抽取：结构化输出优先。
- 复杂规划和总结：大模型。

### 16.3 缓存

可以缓存：

- 工具 schema 渲染结果。
- prompt 模板渲染结果。
- 高频工具选择结果。
- 低风险查询 API 的短 TTL 结果。
- embedding 和 rerank 中间结果。

不缓存：

- 写操作结果。
- 敏感数据。
- 高风险流程结果。
- 用户临时业务参数。

### 16.4 Prompt caching 对齐

如果使用支持 prompt caching 的模型，可以把稳定内容放在 prompt 前缀，例如系统指令、工具选择规范、固定输出格式，把动态内容放在后面，从而提高缓存命中率。

面试表达：

> 成本优化不是简单换便宜模型，而是把 Agent 链路拆开做路由。规则能解决的不用模型，小模型能解决的不走大模型，稳定前缀尽量复用，低风险查询才做短 TTL 缓存。

## 17. 与 2026 Agent 工程主线的对齐

这部分不需要讲得很满，但可以在面试追问时体现你知道行业方向。

### 17.1 Skills 成为业务能力封装方式

2026 年前后，Skill 的价值不在于替代工具，而在于把重复业务流程封装成可复用能力。一个成熟 Skill 往往包含 instructions、references、脚本或工具调用方式、输入输出约束和示例。

本项目和这个方向对齐的方式是：

```text
ERP Skill = prompt + context policy + memory policy + tool chain + HITL + guardrail + eval cases + trace events
```

所以面试时可以说：

> 我没有只停留在 tool calling，而是把高频 ERP 流程包装成 Skills。Tool 是原子能力，Skill 是业务流程能力。这样模型不是每次从零规划，而是按订单录入、生产计划调整、库存查询、供应商选择等 Skill 进入稳定流程。

### 17.2 Eval 从单次调试走向数据集回归

行业主线已经从“手工试几条 prompt”转向 dataset + eval run。项目当前的 prompt eval 是起点，后续应把 badcase 转成可重复评测集。

### 17.3 Trace 覆盖完整 Agent run

现代 Agent tracing 不只记录日志，而是记录模型生成、工具调用、guardrail、人类确认、自定义事件。当前项目的 `nodes/edges` 可以升级成 trace 的可视化层。

### 17.4 Persistence 支撑 HITL 和 replay

LangGraph 这类框架把 checkpoint/persistence 用于 HITL、memory、replay 和故障恢复。当前项目可以说是自研轻量状态机，后续再考虑迁移。

### 17.5 Structured Outputs 提升关键节点稳定性

当前项目是 JSON 优先解析，后续可以升级到更严格的 schema 输出，尤其是参数抽取、工具选择、安全检查和人类反馈意图识别。

### 17.6 MCP 是工具接入标准化方向

当前 Tool Registry 是企业内部工具注册中心。后续如果要和多客户端、多模型生态集成，可以考虑 MCP adapter，但不建议当前宣称已经完成 MCP。

### 17.7 Guardrails 从最终回答扩展到工具调用链

业界主流的 Agent guardrail 已经不只是在最终输出上做敏感词或格式检查，而是围绕输入、工具调用、工具输出、最终回答和 trace 做分层防护。本项目的幻觉治理也按这个思路包装：工具选择前约束候选集，工具调用前校验参数，回答前检查证据来源，异常时升级到 HITL。

面试表达：

> 我没有盲目追行业概念，而是把项目放在 Agent 工程主线里看：Tool Registry 管工具，ERP Skills 复用业务流程，workflow 保证可控，HITL 管住写操作，trace 和 eval 支撑迭代，scoped memory 解决上下文和偏好。这些能力比简单堆多 Agent 或无限长记忆更适合 ERP 试点落地。

## 18. 不做或暂缓做的内容

为了真实，建议明确这些没有作为当前阶段重点：

### 18.1 不做完整多 Agent

当前业务主要是 ERP API 工具调用链，不需要一开始拆成 Planner Agent、Executor Agent、Memory Agent、Safety Agent 等复杂多 Agent。workflow + 局部 LLM 决策更稳。

### 18.2 不做完整 LangGraph 重构

可以说：

> 当前是自研轻量状态机，已经能支持任务状态、HITL 和调用链展示。后续如果要标准化 checkpoint、interrupt、replay，可以迁移到 LangGraph。

### 18.3 不做完整异步任务队列

可以说：

> 当前试点阶段任务链路以可控和易排查为主，没有引入 Celery/Kafka。后续如果出现长耗时工具调用或并发瓶颈，再拆 worker。

### 18.4 不做全量长期记忆平台

可以说：

> 当前只做 scoped memory，不做全量用户画像和无限长期记忆。ERP 场景里记错比忘记更危险。

### 18.5 不做全自动 ERP 操作

可以说：

> 查询类任务可以自动化，写操作和流程推进必须经过安全校验和人工确认。

## 19. 最终能力包装清单

### 当前项目可以包装为已具备

1. Prompt Registry + Model Profile。
2. 结构化输出解析。
3. OpenAPI Tool Registry。
4. MongoDB + Milvus + rerank 工具检索。
5. ERP Skills：订单录入、库存查询、供应商选择、生产计划调整、模糊需求澄清。
6. Controlled Workflow。
7. HITL 确认。
8. Scoped Memory：短期上下文、摘要记忆、低风险长期偏好。
9. Context Manager：recent messages、task state、pinned facts、summary、retrieved memory。
10. Ambiguity Resolver：处理“老样子”“上次那批”“按原计划”。
11. Trace：调用链 nodes/edges、关键节点记录、badcase 复盘。
12. Eval：tool selection、human feedback intent、tool summary、参数抽取、安全和工具链用例。
13. Safety Guardrail：读写分级、参数校验、权限校验、写操作确认。
14. Hallucination Control：工具幻觉、参数幻觉、事实幻觉、记忆幻觉和流程幻觉的分层治理。
15. Cost & Latency：规则短路、候选工具缩小、模型路由、低风险缓存策略。

### 后续高级演进

1. LangGraph checkpoint/interrupt/replay 迁移。
2. MCP Server Adapter，把内部 ERP 工具和部分 Skill 标准化暴露出去。
3. 更强长期记忆治理：冲突消解、记忆过期、来源可信度、自动压缩。
4. 多 Agent 分工：Planner、Executor、Safety、Evaluator 的标准化协作。
5. 在线指标看板和 A/B。
6. 异步 worker 和长耗时任务恢复。
7. 更完整的 ERP 模块扩展，例如设备、工艺路线、BOM、质量管理。

## 20. 面试主线

推荐 2 分钟表述：

> 这个项目是给一家大型制造企业做的 ERP Copilot 试点。企业的 ERP 系统覆盖订单、物料、库存、供应商和生产计划，但很多流程仍然依赖人工录入和推进，例如客户订单录入、生产计划调整、进度更新和任务分配。我的目标不是做一个完全自主的通用 Agent，而是解决制造业 ERP 场景里自然语言到业务 API 的可控执行问题。
>
> 架构上我用了 PromptOps + Tool Registry + ERP Skills + Controlled Workflow + Scoped Memory + Eval/Trace。ERP API 通过 OpenAPI 自动注册成工具，存 MongoDB 和 Milvus，通过向量召回、rerank 和 LLM 精选选工具。上层把订单录入、库存查询、供应商选择、生产计划调整这些流程封装成 ERP Skills。执行上不是完全 ReAct，而是固定 workflow 包住模型决策点，工具调用前做参数校验和人工确认。Prompt 侧做了 YAML 注册表、模型 profile 和结构化输出解析，并配了基础 eval，避免换模型或改 prompt 后老功能漂移。
>
> 当前版本已经具备试点阶段的完整闭环，包括 session/task 隔离、scoped memory、模糊需求澄清、幻觉治理、trace、eval 和 guardrail。后续我不会简单堆功能，而是考虑更高阶的平台化演进，比如 LangGraph checkpoint、MCP adapter、更强长期记忆治理、多 Agent 分工和在线评测看板。

## 21. 高频追问回答

### 为什么不用完全自主 Agent？

因为 ERP 有大量写操作和流程推进动作，错误成本高。完全自主 Agent 自由度高，但难以保证权限、参数和执行边界。当前选择 workflow + 局部 LLM 决策，是用可控性换稳定性。

### 为什么要做 PromptOps？

因为项目支持多模型，不同模型对中文 ERP 字段、JSON 格式和任务拆解的稳定性不同。PromptOps 可以把 prompt 版本、模型适配和 eval 回归工程化。

### 为什么 MongoDB + Milvus？

MongoDB 适合存结构化工具、任务、trace 和记忆；Milvus 适合在工具或记忆数量变多时做语义召回。两者职责不同，不是相互替代。

### 为什么既讲 Tool，又讲 Skill？

Tool 是原子 API 能力，解决“怎么调用 ERP 接口”；Skill 是业务流程能力，解决“怎么稳定完成订单录入、生产计划调整、库存查询这类重复任务”。如果只讲 Tool，会像普通 API calling；如果只讲 Skill，又说不清底层工具怎么注册、检索、权限控制和评测。所以项目采用底层 Tool Registry + 上层 ERP Skills 的分层设计。

### Skill 和 MCP 是什么关系？

Skill 是业务能力封装，MCP 是标准化连接协议。当前项目先在内部实现 Tool Registry 和 ERP Skills，后续如果要让更多 Agent 客户端统一调用 ERP 能力，可以封装 MCP Server Adapter，把内部工具或部分 Skill 标准化暴露出去。

### 为什么长期记忆要克制？

ERP 场景里错误记忆会导致错误订单、错误排产或错误供应商选择。长期记忆只保存用户明确确认过的低风险偏好，临时任务参数和高风险操作结果不进入长期记忆。

### 怎么处理“按老样子”？

先判断缺失槽位，再查 session 记忆和长期偏好，生成候选方案和来源说明。只要涉及写操作，必须让用户确认后再执行。

### 如何证明系统在变好？

通过 trace 和 eval。每个 badcase 都要知道错在任务分类、工具选择、参数抽取、安全校验还是工具调用，然后回流到对应 eval dataset。这样 prompt 或策略改动可以做回归，而不是靠人工感觉。

### 这个项目怎么处理幻觉？

不是只靠一句“不要编造”的 prompt，而是按 Agent 链路分层治理：工具必须来自 Tool Registry，工具选择必须落在候选 action 里，参数要做缺失、类型和业务规则校验，最终回答必须基于 API response、任务状态、确认记录和结构化记忆。遇到低置信度、缺证据、模糊需求或高风险写操作时，进入澄清或 HITL。

## 22. 可引用的行业对齐资料

这些资料不一定要放进项目 README，但面试准备时可以作为工程主线参考：

- OpenAI Agent Evals：强调从单次 trace 走向可重复 datasets 和 eval runs。https://developers.openai.com/api/docs/guides/agent-evals
- OpenAI Agents SDK Tracing：trace 覆盖 LLM generation、tool calls、guardrails 和 custom events。https://openai.github.io/openai-agents-js/guides/tracing/
- OpenAI Agents SDK Guardrails：支持输入、输出和工具调用等环节的 guardrail。https://openai.github.io/openai-agents-js/guides/guardrails
- OpenAI Agent Builder Safety：强调用 guardrails、tool confirmations、trace graders 和 evals 管控 agent 风险。https://platform.openai.com/docs/guides/agent-builder-safety
- OpenAI Agents SDK 2026 演进：提到 MCP、skills、AGENTS.md、sandbox 等 agent runtime 基础设施。https://openai.com/index/the-next-evolution-of-the-agents-sdk/
- Anthropic Agent Skills：Skills 用于封装可复用任务能力，并可由模型在相关任务中自动调用。https://docs.claude.com/en/docs/agents-and-tools/agent-skills
- LangGraph Persistence：checkpoint 支撑 HITL、memory、time travel replay 和 fault tolerance。https://docs.langchain.com/oss/python/langgraph/persistence
- OpenAI Structured Outputs：关键节点可用 schema 约束输出，提升结构化稳定性。https://openai.com/index/introducing-structured-outputs-in-the-api/
- OpenAI Prompt Caching：稳定 prompt 前缀可帮助降低成本和延迟。https://openai.com/index/api-prompt-caching/
