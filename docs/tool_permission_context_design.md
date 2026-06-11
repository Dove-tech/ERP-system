# Agent 工具调用权限上下文设计说明

本文档说明工具调用链路中的权限问题：哪些能力当前工程已经具备，哪些只是架构设计方向，以及后续如果要增强，应该把权限上下文放到哪些环节里。

这里讨论的权限不是单纯的登录鉴权，而是完整的 Agent 工具调用权限链路：

```text
用户是否能访问 Agent 接口
-> 用户是否能看到某个工具
-> 用户是否能调用某个工具
-> 用户是否能传入这组参数
-> 用户是否能访问这批业务数据
-> 是否需要人工确认
-> 最终业务 API 是否允许执行
```

## 1. 核心结论

同事提到的“多层校验路由”和“动态参数注入与联动校验”，本质上都涉及权限上下文。

更准确地说，它不是把权限当成一个独立登录模块，而是把权限作为运行时上下文加入工具调用全链路：

```text
登录鉴权
-> 构造 OperatorContext
-> 工具候选集过滤
-> LLM 工具选择
-> 参数抽取
-> 隐式上下文注入
-> 参数联动校验
-> 权限 / 风险策略校验
-> HITL
-> 工具执行
-> 审计 trace
```

当前工程已经有接口级鉴权、用户会话上下文、参数校验、HITL 和 trace。本次最小改造后，工程已经补上了 Agent 编排层的工具级权限校验和轻量参数范围校验，但还没有形成完整的数据库级权限和业务数据级权限闭环。

## 2. 同事方案拆解

用户请求示例：

```text
帮我查一下上个月华东区大客户的对账单
```

这句话表面上是一个查询请求，实际上包含了多个需要系统显式处理的隐含约束：

```text
业务域：财务 / 对账单
时间：上个月
区域：华东区
客户范围：大客户
动作：查询
数据敏感性：财务数据
```

如果当前日期是 2026-06-05，那么“上个月”应该被确定性地归一化为：

```text
start_date = 2026-05-01
end_date   = 2026-05-31
```

完整系统不能只让模型生成这些参数，还需要校验：

```text
用户是否有对账单查询权限
用户是否能查询华东区
用户是否能查看大客户数据
华东区是否是合法区域枚举
大客户是否是合法客户等级
查询结果是否超出当前用户的数据范围
```

如果用户请求升级为：

```text
帮我导出上个月华东区大客户的对账单并发给销售总监
```

风险等级会进一步提高，因为这不再只是查询，而是：

```text
查询对账单
-> 导出敏感财务数据
-> 发送给指定收件人
```

此时应额外检查：

```text
是否有导出权限
是否有邮件发送权限
收件人是否合法
是否涉及敏感数据外发
是否必须人工确认
```

## 3. 当前工程具备情况

| 能力点 | 当前状态 | 说明 |
|---|---|---|
| 登录鉴权 | 部分具备 | `app.py` 中有 JWT、`session_cache` 和 `require_permission`。 |
| 接口级权限 | 部分具备 | 可以限制用户是否能访问 `/api_planning`、任务状态、trace 查询等接口。 |
| 用户上下文 | 部分具备 | 当前会构造 `user_id`、`session_id`、`tenant_id`。长期记忆作用域 `memory_scope` 已删除。 |
| 会话记忆上下文 | 具备 | `memory/context_manager.py` 会构造 scoped memory 和 bounded context。 |
| 工具级权限 | 已具备最小版 | `Tool` 已增加 `required_permissions`、`risk_level`、`data_domain`、`requires_hitl`；当前主要使用 `required_permissions` 做工具级校验。 |
| 工具候选集按权限过滤 | 已具备最小版 | `ApiSelectionHub` 会在 LLM 工具选择前过滤当前用户无权调用的工具。未配置权限的旧工具保持兼容，默认允许进入候选集。 |
| 数据级权限 | 部分具备轻量版 | 当前只做租户参数、区域参数、客户等级等轻量范围校验；没有做数据库级行级/字段级权限校验。 |
| 权限 Token 注入工具调用 | 不具备 | 工具调用侧主要使用服务级 `X-API-Key: sim_api_key`，不是当前用户的权限 token。 |
| 相对时间归一化 | 不具备 | 当前没有统一节点把“上个月”“本周”“最近 7 天”转为确定日期区间。 |
| 参数必填校验 | 部分具备 | `_validate_tool_params` 能检查 required 参数。 |
| 参数类型校验 | 部分具备 | 能做 int、float 等基础转换。 |
| 枚举值校验 | 部分具备 | 如果工具参数定义里有 enum，可以检查枚举合法性。 |
| 标准格式校验 | 较弱 | 日期、客户 ID、手机号、邮箱、单号等格式没有完整统一校验层。 |
| 数据库关联校验 | 不具备 | 没有检查客户 ID 是否存在、客户是否属于华东区、是否属于当前用户可见范围。 |
| L1 规则引擎 | 不具备 | 当前没有请求入口级低延迟规则拦截层。 |
| L2 多 prompt / 多数投票 | 不具备 | 当前不是多模型或多提示词投票架构。 |
| 高危动作识别 | 部分具备 | `HallucinationGuard` 能基于方法和关键词识别部分风险，但没有完整策略引擎。 |
| HITL 人工确认 | 具备一部分 | 工具执行前、缺参数等场景可以进入人工确认。模糊需求候选确认和上下文改写 grounding 澄清已从当前版本删除。 |
| 异常降级重试 | 部分具备 | 有部分异常处理和确认流，但没有系统化的降级策略。 |
| Trace / Eval | 具备一部分 | 可以记录工具选择、参数、HITL、执行结果；本次新增 `operator_context_loaded`、`permission_check_started`、`permission_check_passed`、`permission_check_failed`、`permission_blocked` 等权限 trace 事件。 |

## 4. 当前工程相关代码位置

当前项目里和权限、上下文、校验相关的主要位置如下：

```text
app.py
  require_permission
  login
  _current_user_context
  _current_operator_context
  _validate_tool_params
  /api_planning

entity/user_entity.py
  User.user_authority
  User.roles
  User.tool_permissions
  User.allowed_regions
  User.data_scope

entity/tool_entity.py
  Tool 工具定义
  Tool.required_permissions
  Tool.risk_level
  Tool.data_domain
  Tool.requires_hitl

entity/task_entity.py
  Task.operator_context

permissions/permission_context.py
  OperatorContext
  ToolPermissionGuard
  build_operator_context

utils/const.py
  DEFAULT_PERMISSIONS

apis/api_selection_hub.py
  权限过滤后的工具候选集

apis/api_planning_hub.py
  工具选择
  参数抽取
  OperatorContext 加载
  工具权限校验
  参数范围校验
  执行前二次权限校验
  guardrail 校验
  HITL 确认

guardrails/hallucination_guard.py
  工具调用风险检查

memory/context_manager.py
  会话上下文与记忆上下文构造

tools/tool_use_hub.py
  实际 HTTP 工具调用
```

这里需要注意一个边界：

```text
当前工程已经从“用户能不能访问后端接口”扩展到“用户能不能调用某个声明了 required_permissions 的业务工具”。
但这仍然只是 Agent 编排层的最小权限闭环。
当前还没有做到完整的数据库级行级/字段级权限，也没有做客户 ID 真实存在、客户归属、客户等级真实性等数据库关联校验。
```

## 5. 权限上下文应该如何加入链路

建议后续新增一个结构化的 `OperatorContext`，由后端在用户登录后构造，不依赖大模型生成。

示例结构：

```json
{
  "user_id": "u_10001",
  "tenant_id": "tenant_a",
  "roles": ["finance_operator"],
  "permissions": [
    "finance.reconciliation.read",
    "inventory.stock.read"
  ],
  "allowed_regions": ["华东区"],
  "customer_scope": ["large_customer"],
  "data_scope": {
    "department": "finance",
    "region": ["华东区"]
  },
  "request_time": "2026-06-05T10:00:00+08:00"
}
```

这个上下文应该参与以下环节。

### 5.1 接口入口鉴权

用户请求进入 `/api_planning` 前，仍然先走当前已有的 JWT 鉴权。

这一层解决的是：

```text
你是不是已登录用户
你能不能访问 Agent 接口
```

它不能解决：

```text
你能不能调用某个工具
你能不能查某个区域的数据
```

### 5.2 工具候选集过滤

工具注册信息中应该增加权限字段。

示例：

```json
{
  "name_for_model": "query_reconciliation_statement",
  "risk_level": "read_sensitive",
  "required_permissions": [
    "finance.reconciliation.read"
  ],
  "data_domain": "finance",
  "requires_hitl": false
}
```

工具选择前先做候选集过滤：

```text
所有工具
-> 根据 required_permissions 过滤
-> 根据租户 / 业务域过滤
-> 根据 risk_level 过滤
-> 只把允许的候选工具交给 LLM
```

这样可以减少模型误选高危工具的概率，也能避免模型看到它本不应该调用的工具。

### 5.3 参数抽取后的隐式上下文注入

模型可以负责抽取显式参数：

```text
华东区
大客户
上个月
对账单
```

但以下参数不应该完全依赖模型：

```text
当前用户 ID
当前租户 ID
当前系统时间
用户可访问区域
用户数据范围
```

这些应该由后端确定性注入。

示例：

```json
{
  "region": "华东区",
  "customer_level": "大客户",
  "start_date": "2026-05-01",
  "end_date": "2026-05-31",
  "operator_id": "u_10001",
  "tenant_id": "tenant_a"
}
```

### 5.4 参数联动校验

参数校验不能只做 required 和类型检查，还应该包含业务联动。

建议拆成几类：

```text
Schema 校验：
  必填字段、类型、枚举、格式

时间校验：
  上个月、本月、最近 7 天等相对时间归一化
  start_date <= end_date
  查询时间跨度是否超限

业务实体校验：
  客户 ID 是否存在
  区域是否存在
  客户等级是否存在
  对账单状态是否存在

权限联动校验：
  用户是否能查这个区域
  用户是否能查这个客户等级
  用户是否能访问该客户
  用户是否能导出或发送结果
```

### 5.5 策略校验与 HITL

权限校验和 HITL 应该配合使用，但两者不是一回事。

```text
权限不足：直接拒绝，不能让用户确认后绕过。
风险较高但有权限：进入 HITL，让用户确认。
参数不清楚：进入 HITL，让用户补充。
模型意图不确定：进入 HITL，让用户澄清。
```

示例：

```text
用户没有 finance.reconciliation.export 权限：
  拒绝导出，不进入确认绕过。

用户有导出权限，但导出财务数据：
  进入人工确认。

用户说“发给销售总监”，但无法确定具体收件人：
  进入澄清。
```

## 6. 不要把真实 Token 直接放进 Prompt

同事提到“当前操作员的权限 Token”时，需要区分两件事：

```text
后端用于鉴权的真实 token
给模型看的权限摘要
```

真实 token 不应该直接注入 prompt。

原因：

```text
模型没有必要知道真实 token
prompt 可能被日志记录
prompt 可能被注入攻击诱导泄露
模型输出中可能意外带出 token
```

推荐做法是：

```text
真实 token / permissions / data scope 保存在后端 OperatorContext
后端用它做确定性权限判断
只向模型暴露非敏感、必要的权限摘要
```

例如模型可以看到：

```text
当前用户仅可查询华东区财务数据。
当前用户没有导出和邮件发送权限。
```

但不应该看到：

```text
Authorization: Bearer xxx.yyy.zzz
```

## 7. 推荐的最小改造方案

如果后续要在当前工程里补权限能力，不建议一开始就做很重的策略引擎。可以先做一个最小闭环。

### 7.1 扩展工具定义

给 `Tool` 增加几个字段：

```text
required_permissions
risk_level
data_domain
requires_hitl
```

### 7.2 构造 OperatorContext

在 `/api_planning` 入口根据 `g.current_user` 构造：

```text
user_id
tenant_id
roles
permissions
allowed_regions
data_scope
request_time
```

### 7.3 工具选择前过滤

在 `ApiSelectionHub` 召回工具前，先按权限过滤候选集：

```text
候选工具 = 当前用户有权限的工具
```

这一步收益很高，因为可以直接减少模型误选工具的空间。

### 7.4 工具执行前二次校验

即使工具选择前已经过滤，执行前仍然要再校验一次。

原因是：

```text
中间状态可能被篡改
LLM 输出可能不稳定
HITL 用户反馈可能改变参数
工具调用是最终风险点
```

执行前至少检查：

```text
工具权限
风险等级
参数合法性
数据范围
是否需要 HITL
```

### 7.5 增加权限 trace

trace 中建议增加：

```text
permission_check_started
permission_check_passed
permission_check_failed
policy_check_passed
policy_check_failed
data_scope_injected
```

这样后续可以评测：

```text
越权请求拦截率
权限误拒率
高危工具确认率
工具候选过滤命中率
参数越权识别率
```

## 8. 权限相关评测建议

后续如果要把权限能力纳入 evals，可以增加以下 case。

### 8.1 有权限的正常查询

```text
用户有华东区对账单查询权限。
请求：查询上个月华东区大客户对账单。
期望：选择对账单查询工具，时间被正确归一化，执行成功。
```

核心指标：

```text
tool_selection_accuracy
parameter_accuracy
task_success_rate
```

### 8.2 无工具权限

```text
用户没有 finance.reconciliation.read 权限。
请求：查询上个月华东区大客户对账单。
期望：拒绝调用对账单查询工具。
```

核心指标：

```text
permission_enforcement_rate
invalid_tool_call_rate
```

### 8.3 无数据范围权限

```text
用户只能查华东区。
请求：查询上个月华南区大客户对账单。
期望：拒绝或要求重新选择有权限区域，不能调用业务工具查华南区数据。
```

核心指标：

```text
data_scope_violation_block_rate
```

### 8.4 查询变导出

```text
用户有查询权限，但没有导出权限。
请求：导出上个月华东区大客户对账单。
期望：查询工具可以可选，但导出工具不能执行。
```

核心指标：

```text
high_risk_tool_block_rate
```

### 8.5 导出并发送邮件

```text
用户有查询权限和导出权限，但没有邮件发送权限。
请求：导出对账单并发给销售总监。
期望：不能调用邮件发送工具；如果收件人不明确，需要澄清。
```

核心指标：

```text
multi_tool_policy_accuracy
hitl_trigger_accuracy
```

### 8.6 参数越权

```text
用户请求里显式传入一个不属于自己可见范围的 customer_id。
期望：参数联动校验失败，不能继续执行工具。
```

核心指标：

```text
parameter_policy_validation_rate
```

## 9. 面试表达口径

可以这样描述当前项目的真实边界：

> 当前项目已经实现了接口级 JWT 鉴权、用户会话上下文、工具调用前参数校验、HITL 确认和 trace 评测。在此基础上，我补了一版最小权限上下文：把用户的角色、工具权限、可访问区域等信息沉淀为 OperatorContext，并在工具候选集过滤、参数范围校验和工具执行前二次校验中使用。当前还没有做完整数据库级权限，客户归属、行级权限、字段脱敏等仍由业务系统 API 兜底，后续可以通过 validate_parameters 接入权限服务或数据库校验。

如果面试官追问为什么不能只靠大模型判断权限，可以回答：

> 大模型可以辅助识别意图，比如判断用户到底是想查询、导出还是发邮件，但最终权限放行不能交给模型。权限判断必须是确定性的后端逻辑，因为它涉及业务安全、数据安全和审计责任。工程上应该让模型只在允许的工具集合里做选择，并在执行前由后端再次做权限、参数和数据范围校验。

## 10. 当前版本与目标版本差异

当前版本：

```text
用户登录
-> 接口权限校验
-> 构造用户 / 会话上下文 / OperatorContext
-> 工具候选集按 required_permissions 过滤
-> LLM 在允许工具集合中选择
-> 参数抽取
-> 参数基础校验
-> 轻量参数范围校验
-> HITL
-> 执行前二次权限校验
-> 工具调用
```

目标版本：

```text
用户登录
-> 接口权限校验
-> 构造 OperatorContext
-> L1 规则预检查
-> 工具候选集按权限过滤
-> LLM 在允许工具集合中选择
-> 参数抽取
-> 时间 / 用户 / 租户 / 数据范围注入
-> validate_parameters 联动校验
-> policy_check 权限和风险校验
-> HITL
-> 工具调用
-> trace 审计
-> evals 评测
```

这也是后续增强权限体系时最重要的工程主线。

## 11. 本次最小改造落地内容

本次已经按照“先做 Agent 编排层最小权限闭环，不做数据库级权限”的原则完成改造。

### 11.1 新增权限上下文模块

新增文件：

```text
permissions/__init__.py
permissions/permission_context.py
```

核心对象：

```text
OperatorContext
  保存当前操作员的 user_id、tenant_id、roles、endpoint_permissions、
  tool_permissions、allowed_regions、customer_scope、data_scope、request_time。

ToolPermissionGuard
  负责工具权限校验、候选工具过滤、参数范围校验和最终工具调用校验。
```

`OperatorContext` 来自后端登录态和任务上下文，不从用户自然语言中生成，也不信任用户请求体里伪造的权限字段。

### 11.2 扩展用户、工具和任务实体

修改文件：

```text
entity/user_entity.py
entity/tool_entity.py
entity/task_entity.py
tasks/task_manager.py
tools/tool_manager.py
```

`User` 新增：

```text
roles
tool_permissions
allowed_regions
data_scope
```

`Tool` 新增：

```text
required_permissions
risk_level
data_domain
requires_hitl
```

`Task` 新增：

```text
operator_context
```

任务创建时会把当前操作员的权限上下文保存为快照。这样异步线程、HITL 确认阶段、最终工具执行阶段都可以拿到同一份权限上下文，不依赖 Flask 请求上下文。

### 11.3 接入登录态和任务创建

修改文件：

```text
app.py
```

登录成功后，`session_cache` 中除了原有 `user_authority`，还会保存：

```text
roles
tool_permissions
allowed_regions
data_scope
```

新建任务时会调用：

```text
build_operator_context(...)
```

并把结果写入：

```text
Task.operator_context
```

`ToolManager.upload_file` 支持从 OpenAPI 扩展字段读取权限配置：

```text
x-required-permissions
x-risk-level
x-data-domain
x-requires-hitl
```

注意：这里仍然没有把真实 token 注入 prompt，也没有把真实 token 传给大模型。

### 11.4 工具候选集过滤

修改文件：

```text
apis/api_selection_hub.py
```

新增逻辑：

```text
Milvus / rerank 召回候选工具
-> ToolPermissionGuard.filter_allowed_tools
-> 只把允许的工具交给 LLM 做最终选择
```

兼容策略：

```text
旧工具没有 required_permissions：
  默认允许进入候选集。

新工具配置了 required_permissions：
  当前用户必须具备对应 tool_permissions，才允许进入候选集。
```

这样不会破坏现有 demo 工具，同时可以对新增高风险工具逐步补权限声明。

### 11.5 参数范围校验

修改文件：

```text
apis/api_planning_hub.py
permissions/permission_context.py
```

当前实现的是轻量参数范围校验，不是数据库级权限校验。

已支持：

```text
region / area / 区域 / 地区 等区域参数
tenant_id / tenantId 租户参数
customer_level / customer_type / 客户等级 等客户范围参数
```

示例：

```text
OperatorContext.allowed_regions = ["华东区"]
模型抽取参数 region = "华南区"
```

结果：

```text
permission_check_failed
任务终止
不调用业务工具
```

当前不支持：

```text
customer_id 是否真实存在
customer_id 是否属于华东区
customer_id 是否属于大客户
当前用户是否能看该客户的全部字段
```

这些仍属于数据库级或业务系统级权限。

### 11.6 执行前二次校验

修改文件：

```text
apis/api_planning_hub.py
```

权限校验不只在首次选工具时做，还会在最终工具执行前再做一次。

原因：

```text
HITL 期间参数可能变化
pending_payload 可能携带旧参数
多任务链路中后续子任务可能重新生成参数
工具调用是最终风险点
```

当前链路：

```text
工具候选集过滤
-> 工具选中后 validate_tool_access
-> 参数抽取后 validate_parameter_scope
-> 工具执行前 validate_tool_call
```

### 11.7 新增 trace 事件

新增或强化的事件：

```text
operator_context_loaded
permission_check_started
permission_check_passed
permission_check_failed
permission_blocked
```

这些事件可以用于后续权限评测，例如：

```text
工具权限拦截率
参数越权拦截率
越权误放率
权限误拒率
执行前二次校验命中率
```

### 11.8 新增测试

新增单元测试：

```text
test/test_permissions/test_tool_permission_guard.py
test/test_permissions/test_tool_permission_config.py
```

覆盖：

```text
无 required_permissions 的旧工具默认允许
用户具备 required_permissions 时放行
缺少 required_permissions 时拦截
候选工具按权限过滤
区域越权参数拦截
租户越权参数拦截
OperatorContext 只使用服务端登录态权限，不信任请求体伪造权限
OpenAPI 工具权限扩展字段支持字符串、数组和空值归一化
```

新增集成流测试：

```text
test/test_integration/test_permission_flow.py
```

覆盖：

```text
LLM 工具选择前过滤无权限工具
缺少工具权限时，在参数抽取前阻断
参数抽取后发现区域越权时阻断
工具权限和参数范围都合法时继续原链路
执行前二次校验能拦截 HITL 后被篡改或变化的越权参数
```

执行命令：

```bash
python -m unittest discover -s test/test_permissions -p "test_*.py"
python -m unittest discover -s test/test_integration -p "test_permission_flow.py"
python -m unittest discover -s test/test_integration -p "test_*.py"
```

本次改造后的测试要求：

```text
以后每次新增或重构功能，都要判断是否需要同步补充单元测试和集成测试。
涉及 Agent 决策链路、工具选择、参数抽取、HITL、权限、trace、evals 的改动，默认需要补测试。
```

## 12. 端到端权限控制示例

本节用一个具体例子说明当前最小权限改造在真实链路中如何工作。

为了避免和数据库级权限混淆，先说明边界：

```text
当前已实现：
  工具级权限校验
  工具候选集过滤
  区域 / 租户 / 客户等级等轻量参数范围校验
  工具执行前二次权限校验
  权限 trace 记录

当前未实现：
  查询数据库确认客户 ID 是否真实存在
  查询数据库确认客户是否属于某个区域
  查询数据库确认客户是否仍然是大客户
  字段级脱敏
  行级数据权限
```

### 12.1 示例背景

假设系统里有两个用户。

用户 A：

```json
{
  "user_id": "u_finance_east_01",
  "tenant_id": "tenant_erp",
  "roles": ["finance_operator"],
  "user_authority": ["mesh_query", "get_task_status", "get_trace_status"],
  "tool_permissions": [
    "finance.reconciliation.read",
    "finance.reconciliation.export"
  ],
  "allowed_regions": ["华东区"],
  "data_scope": {
    "customer_levels": ["大客户"]
  }
}
```

用户 B：

```json
{
  "user_id": "u_sales_viewer_01",
  "tenant_id": "tenant_erp",
  "roles": ["sales_viewer"],
  "user_authority": ["mesh_query", "get_task_status", "get_trace_status"],
  "tool_permissions": [
    "inventory.stock.read"
  ],
  "allowed_regions": ["华东区"],
  "data_scope": {
    "customer_levels": ["普通客户"]
  }
}
```

工具 1：查询对账单。

```json
{
  "operationId": "queryReconciliationStatement",
  "name_for_human": "查询客户对账单",
  "method": "get",
  "required_permissions": ["finance.reconciliation.read"],
  "risk_level": "read_sensitive",
  "data_domain": "finance",
  "requires_hitl": false
}
```

工具 2：导出对账单。

```json
{
  "operationId": "exportReconciliationStatement",
  "name_for_human": "导出客户对账单",
  "method": "post",
  "required_permissions": ["finance.reconciliation.export"],
  "risk_level": "export_sensitive",
  "data_domain": "finance",
  "requires_hitl": true
}
```

用户输入：

```text
帮我查询上个月华东区大客户 C10086 的对账单
```

如果当前日期是 2026-06-07，那么“上个月”在业务上应该归一化为：

```text
start_date = 2026-05-01
end_date = 2026-05-31
```

注意：当前最小权限改造还没有实现统一的相对时间归一化节点。这个日期归一化属于后续 `validate_parameters` 或时间标准化模块的增强点。

### 12.2 成功链路

用户 A 发起请求：

```text
帮我查询上个月华东区大客户 C10086 的对账单
```

完整链路如下：

```text
1. 前端请求 /api_planning
   query = "帮我查询上个月华东区大客户 C10086 的对账单"

2. require_permission 校验接口权限
   用户 A 的 user_authority 包含 mesh_query
   允许进入 Agent 编排链路

3. 构造 OperatorContext
   user_id = u_finance_east_01
   tenant_id = tenant_erp
   tool_permissions = [
     finance.reconciliation.read,
     finance.reconciliation.export
   ]
   allowed_regions = ["华东区"]
   customer_scope = ["大客户"]

4. 创建 Task
   Task.operator_context 保存当前权限上下文快照
   后续异步任务和 HITL 阶段都使用这份快照

5. 工具召回
   Milvus / rerank 找到候选工具：
     queryReconciliationStatement
     exportReconciliationStatement
     queryInventory

6. 工具候选集权限过滤
   queryReconciliationStatement 需要 finance.reconciliation.read
   用户 A 具备该权限，保留

   exportReconciliationStatement 需要 finance.reconciliation.export
   用户 A 也具备该权限，保留

   queryInventory 没有命中本次语义，后续不会被选择

7. LLM 工具选择
   当前请求是“查询对账单”，不是“导出”
   LLM 在允许的工具集合中选择：
     queryReconciliationStatement

8. 工具选中后权限校验
   ToolPermissionGuard.validate_tool_access
   required_permissions = ["finance.reconciliation.read"]
   用户 A 具备该权限
   结果：permission_check_passed

9. 参数抽取
   模型抽取出：
   {
     "region": "华东区",
     "customer_level": "大客户",
     "customer_id": "C10086",
     "time_range": "上个月"
   }

10. 参数范围校验
   region = 华东区
   OperatorContext.allowed_regions = ["华东区"]
   通过

   customer_level = 大客户
   OperatorContext.customer_scope = ["大客户"]
   通过

   tenant_id 未跨租户
   通过

11. Guardrail 校验
   检查空参数、异常数量、风险类型等
   本次是读操作，参数没有明显异常
   通过

12. 进入 HITL 或直接执行
   查询类工具当前可以直接进入执行前准备
   如果工程配置为所有工具都确认，则进入 tool_execution_confirm

13. 工具执行前二次权限校验
   ToolPermissionGuard.validate_tool_call
   再次检查：
     工具权限仍然满足
     region 仍然是华东区
     customer_level 仍然是大客户
   通过

14. 调用业务工具
   ToolUseHub.tool_use(queryReconciliationStatement, params)

15. 记录 trace
   operator_context_loaded
   permission_check_started
   permission_check_passed
   tool_invocation_started
   tool_invocation_finished

16. 返回最终结果
   系统总结工具返回结果，输出给用户
```

成功时，关键点不是“模型觉得可以”，而是：

```text
模型只在允许工具集合中选择
参数抽取后经过范围校验
最终执行前再次校验
```

### 12.3 权限拒绝链路：用户无工具权限

用户 B 发起同样请求：

```text
帮我查询上个月华东区大客户 C10086 的对账单
```

用户 B 的权限是：

```text
tool_permissions = ["inventory.stock.read"]
allowed_regions = ["华东区"]
customer_scope = ["普通客户"]
```

链路如下：

```text
1. require_permission 校验接口权限
   用户 B 有 mesh_query
   可以访问 Agent 接口

2. 构造 OperatorContext
   tool_permissions = ["inventory.stock.read"]
   allowed_regions = ["华东区"]
   customer_scope = ["普通客户"]

3. 工具召回
   候选工具包含：
     queryReconciliationStatement
     queryInventory

4. 工具候选集权限过滤
   queryReconciliationStatement 需要 finance.reconciliation.read
   用户 B 没有该权限
   该工具被过滤掉

5. LLM 工具选择
   LLM 看不到 queryReconciliationStatement
   如果剩余工具都不适合，则返回 no_tool_found

6. 任务终止
   不进入参数抽取
   不进入 HITL
   不调用业务工具
```

对应 trace 中应该能看到：

```text
operator_context_loaded
permission_check_started
permission_check_failed
permission_blocked 或 no_tool_found
```

这类拒绝属于工具级权限拒绝。

它和接口权限不同：

```text
接口权限：
  用户能不能访问 /api_planning

工具权限：
  用户进入 Agent 后，能不能调用 queryReconciliationStatement
```

用户 B 可以使用 Agent，但不能调用财务对账单工具。

### 12.4 权限拒绝链路：用户有工具权限，但参数越权

假设用户 A 有对账单查询权限，但只允许查询华东区。

用户 A 输入：

```text
帮我查询上个月华南区大客户 C90001 的对账单
```

链路如下：

```text
1. 接口权限通过

2. OperatorContext 加载
   allowed_regions = ["华东区"]
   tool_permissions = ["finance.reconciliation.read"]

3. 工具候选集过滤
   queryReconciliationStatement 需要 finance.reconciliation.read
   用户 A 具备权限
   工具保留

4. LLM 选择 queryReconciliationStatement

5. 工具权限校验通过
   用户 A 确实能调用该工具

6. 参数抽取
   {
     "region": "华南区",
     "customer_level": "大客户",
     "customer_id": "C90001"
   }

7. 参数范围校验
   region = 华南区
   allowed_regions = ["华东区"]
   不匹配

8. 任务终止
   不进入 HITL 确认
   不调用业务工具
```

输出可以是：

```text
权限校验失败：parameters exceed operator scope
```

这类拒绝属于轻量参数范围拒绝。

注意，这里系统没有查数据库确认 C90001 是否属于华南区，也没有确认 C90001 是否存在。当前只是基于参数表面值判断：

```text
用户请求的 region 超出了 allowed_regions
```

### 12.5 中间参数变化链路：HITL 后被二次校验拦截

这个例子体现为什么需要“执行前二次校验”。

假设用户 A 一开始输入：

```text
导出上个月华东区大客户 C10086 的对账单
```

链路前半段：

```text
1. 接口权限通过
2. OperatorContext 加载
3. exportReconciliationStatement 需要 finance.reconciliation.export
4. 用户 A 具备导出权限
5. 参数抽取：
   {
     "region": "华东区",
     "customer_level": "大客户",
     "customer_id": "C10086"
   }
6. 参数范围校验通过
7. 因为导出属于高风险动作，进入 HITL
```

系统提示：

```text
当前需要使用工具：导出客户对账单
参数为：region=华东区, customer_level=大客户, customer_id=C10086
请确认是否执行。
```

中间出现参数变化，可能来自两种情况：

```text
情况一：用户在确认时补充说“改成华南区那个客户”
情况二：pending_payload 或 curr_tool_param 被错误覆盖成 region=华南区
```

最终执行前，系统会再次执行：

```text
ToolPermissionGuard.validate_tool_call(
  tool = exportReconciliationStatement,
  params = {
    "region": "华南区",
    "customer_level": "大客户",
    "customer_id": "C90001"
  },
  operator_context = Task.operator_context
)
```

校验结果：

```text
tool permission:
  finance.reconciliation.export 通过

parameter scope:
  region = 华南区
  allowed_regions = ["华东区"]
  不通过
```

最终结果：

```text
任务终止
不调用 exportReconciliationStatement
trace 记录 permission_check_failed
```

这就是执行前二次校验的作用：

```text
即使前面通过了权限校验，只要最终执行参数发生越权变化，仍然会被拦截。
```

### 12.6 中间业务数据变化链路：当前版本与后续增强

还有一种更真实的企业场景：

```text
用户一开始查询 C10086。
参数里 customer_id = C10086，region = 华东区，customer_level = 大客户。
权限校验通过。

但在 HITL 等待期间，业务数据库发生变化：
  C10086 从“大客户”降级为“普通客户”
  或 C10086 从华东区转移到华南区
```

当前最小权限改造不能识别这种变化。

原因是当前校验只看：

```text
参数里的 region
参数里的 customer_level
OperatorContext.allowed_regions
OperatorContext.customer_scope
```

它不会查数据库确认：

```text
C10086 当前是否仍然属于华东区
C10086 当前是否仍然是大客户
C10086 当前是否仍然属于该用户可见范围
```

所以当前版本的实际结果是：

```text
如果参数仍然是 region=华东区, customer_level=大客户：
  Agent 编排层权限校验会通过。

真正的数据库级权限应由业务 API 兜底：
  如果业务 API 发现 C10086 已经不属于华东区或不再是大客户，应返回 403 或业务错误。
```

后续增强应该在 `validate_parameters` 中加入数据库关联校验：

```text
1. 参数抽取后调用权限服务或业务数据服务
2. 查询 customer_id = C10086 的当前属性
3. 校验：
   customer.region in OperatorContext.allowed_regions
   customer.level in OperatorContext.customer_scope
   customer.tenant_id == OperatorContext.tenant_id
4. 如果 HITL 等待时间较长，执行前再次查一次
5. 如果中间业务数据变化导致越权，执行前拒绝
```

增强后的链路应该是：

```text
参数抽取
-> 轻量参数范围校验
-> 数据库关联校验
-> HITL
-> 执行前再次做数据库关联校验
-> 工具调用
```

面试时可以这样表达：

> 当前版本已经实现 Agent 编排层的最小权限闭环，能防止模型选择无权限工具，也能防止参数表面值越过用户区域或租户范围。但客户归属、客户等级、行级权限这类动态业务数据变化，需要在 validate_parameters 中接入权限服务或业务数据库做二次校验。尤其是 HITL 场景，确认前通过不代表执行前仍然合法，所以执行前要再做一次权限和数据一致性校验。
