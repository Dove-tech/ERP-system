# Function Call 改造方案

本文档设计当前 ERP Agent Copilot 如果后续改造成 function call / tool calling 形态，需要如何改造。

核心结论：

```text
function call 只替换或增强“模型如何表达想调用哪个工具、参数是什么”这一层。
它不能替代工具白名单、参数校验、权限校验、HITL、trace 和 LangGraph 状态编排。
```

也就是说，模型返回 function call 之后，系统不能直接执行工具，而是要把它当成一个 `ToolCallProposal`：

```text
模型提出工具调用建议
-> 系统校验
-> 系统确认
-> 系统执行
```

这和当前项目的安全边界是一致的。

## 1. 为什么要考虑 Function Call

当前项目的工具调用链路是：

```text
用户 query
-> RAG 召回候选工具
-> rerank / LLM 选择工具
-> 参数抽取
-> 参数校验
-> 权限校验
-> Guardrail
-> HITL
-> ERP API 执行
```

如果引入 function call，可以优化的是这两块：

```text
工具选择输出更结构化。
参数输出更稳定。
```

function call 的优势：

```text
1. 输出格式更稳定，不再依赖模型自由文本后处理。
2. tool_name 和 arguments 可以直接结构化解析。
3. 参数 schema 可以显式提供给模型。
4. 更适合做自动化 eval，因为输出是标准 JSON。
5. 与主流模型 API 的 tool calling 能力接轨。
```

但它解决不了：

```text
工具是否真的允许执行。
参数业务上是否正确。
用户是否有权限。
是否需要人工确认。
写操作是否幂等。
工具结果是否可信。
```

这些仍然必须由系统控制。

## 2. 改造后的总体架构

推荐架构：

```text
User Query
-> ContextManager 构造上下文
-> Tool Candidate Retrieval
   -> RAG 召回 topK 工具
   -> rerank
   -> permission pre-filter
-> Function Call Proposal
   -> 只把候选工具 schema 交给模型
   -> 模型返回 tool_name + arguments
-> ToolCallProposal Validator
   -> 工具白名单校验
   -> JSON schema 校验
   -> 必填字段校验
   -> 业务参数校验
   -> 权限校验
   -> Guardrail
-> LangGraph Gate
   -> 缺参：missing_params_clarify
   -> 写操作/高风险：tool_execution_confirm
   -> 阻断：permission_blocked / guardrail_blocked
-> Tool Runtime
   -> 用户确认后执行真实 ERP API
-> Result Summary
-> Trace / Eval
```

一句话：

```text
function call 是 tool proposal layer，不是 tool runtime layer。
```

## 3. 当前链路和改造后链路对比

### 3.1 当前链路

当前核心链路大致是：

```text
ApiSelectionHub.get_tool_coarse_and_fine()
-> ParamExtractionHub.param_extract()
-> ApiPlanningHub._tool_check()
-> PermissionGuard
-> HallucinationGuard
-> pending_action / pending_payload
-> ToolUseHub.tool_use()
```

LangGraph 中对应节点：

```text
select_tool
-> check_tool
-> persist_decision
```

### 3.2 Function Call 改造后链路

改造后可以变成：

```text
select_tool
-> retrieve_candidate_tools
-> function_call_propose
-> validate_tool_call_proposal
-> persist_decision
```

也可以保持当前节点名，只替换内部实现：

```text
select_tool
仍然负责召回和候选过滤。

check_tool
内部改成 function call 生成 tool_name + arguments，然后进入统一校验。

persist_decision
仍然负责创建 HITL gate 或阻断。
```

推荐第一阶段采用“内部替换”，避免改动过大。

## 4. 分阶段改造方案

### Phase 1：只引入 Function Call Proposal，不改变执行链

目标：

```text
模型用 function call 输出工具和参数。
系统仍然完全复用原来的校验、HITL、执行链路。
```

改造点：

```text
新增 FunctionCallToolSchemaBuilder。
新增 FunctionCallProposalHub。
新增 ToolCallProposal 数据结构。
在 ApiPlanningHub._tool_check 或 LangGraph check_tool 节点中接入。
```

流程：

```text
1. RAG 召回候选工具。
2. 把候选工具转换成模型支持的 function schema。
3. 调用支持 function call 的模型。
4. 解析返回的 tool_name 和 arguments。
5. 映射回内部 Tool 对象。
6. 进入原有参数校验、权限校验、HITL。
```

这一阶段不允许模型自动执行工具。

### Phase 2：Function Call + LangGraph 节点化

目标：

```text
把 function call proposal 变成一个显式图节点。
```

推荐图结构：

```text
load_task
-> classify_task
-> retrieve_candidate_tools
-> propose_tool_call
-> validate_tool_call
-> persist_decision
-> END
```

节点职责：

```text
retrieve_candidate_tools
负责 RAG 召回、rerank、权限预过滤。

propose_tool_call
负责调用 function call 模型，得到 tool_name + arguments。

validate_tool_call
负责 schema、参数、权限、guardrail 校验。

persist_decision
负责创建 HITL gate、缺参澄清或阻断。
```

这样可以在 trace 中清晰看到：

```text
候选工具有哪些
模型选择了哪个 function
模型生成了什么 arguments
系统为什么接受或拒绝
是否进入 HITL
```

### Phase 3：支持多工具 / 多步 Function Call

目标：

```text
支持模型提出多个 tool call proposal，但仍由系统逐步执行。
```

注意：

```text
多工具 proposal 不能一次性自动执行。
每一步都必须经过 validate。
写操作必须 HITL。
工具结果要回写 graph state / trace。
下一步是否继续由 LangGraph 控制。
```

推荐保守策略：

```text
第一版只允许 single tool call。
多工具任务仍然由 LangGraph / GenerateTaskHub 拆步。
等单工具稳定后，再允许模型提出 plan 或 parallel tool proposal。
```

## 5. Tool Schema 如何生成

当前工具来自 OpenAPI 解析后的 Tool Registry。

已有信息包括：

```text
tool_id
operationId
name_for_human
name_for_model
description
method
path
request_body / parameters
required_permissions
risk_level
data_domain
requires_hitl
```

需要新增一个 schema builder，把内部 Tool 转成模型 function schema。

### 5.1 Function Schema 都包含什么

不同模型厂商的 tool calling 协议细节略有差异，但主流结构都包含三部分：

```text
name
工具函数名，模型通过它表达想调用哪个工具。

description
工具能力说明，帮助模型判断什么时候选择它。

parameters
JSON Schema，描述 arguments 的字段、类型、必填项、枚举值和说明。
```

以 OpenAI-compatible tools 为例：

示例：

```json
{
  "type": "function",
  "function": {
    "name": "query_inventory",
    "description": "查询指定产品的库存信息",
    "parameters": {
      "type": "object",
      "properties": {
        "product_id": {
          "type": "string",
          "description": "产品编号，例如 A15"
        },
        "warehouse_id": {
          "type": "string",
          "description": "仓库编号"
        }
      },
      "required": ["product_id"]
    }
  }
}
```

其中：

```text
type=function
表示这是一个函数工具。

function.name
模型返回 tool_call 时会使用的函数名。

function.description
给模型看的工具描述。

function.parameters.type=object
表示参数是一个 JSON 对象。

function.parameters.properties
每个参数的 schema。

function.parameters.required
必填参数列表。
```

注意：function schema 不是 HTTP 请求定义本身。它只告诉模型：

```text
你可以选择哪个工具。
这个工具需要哪些 arguments。
每个 argument 是什么类型、含义和约束。
```

真正的 HTTP URL、method、path/query/body 如何组装，仍然由系统的 Tool Runtime 完成。

### 5.2 当前 Tool 到 Function Schema 的字段映射

当前项目的内部 `Tool` 结构是：

```python
class Tool(Document):
    tool_id
    operationId
    name_for_human
    name_for_model
    description
    api_url
    path
    method
    request_body
    required_permissions
    risk_level
    data_domain
    requires_hitl
```

参数结构是：

```python
class Parameter(EmbeddedDocument):
    name
    required
    type
    format
    description
    enum
    value
    in_
```

推荐映射：

```text
Tool.operationId 或 Tool.name_for_model
-> function.name

Tool.description + name_for_human + 风险提示
-> function.description

Tool.request_body
-> function.parameters.properties

Parameter.required=True
-> function.parameters.required

Parameter.type
-> JSON Schema type

Parameter.format
-> JSON Schema format

Parameter.enum
-> JSON Schema enum

Parameter.description
-> JSON Schema description
```

内部字段不建议全部暴露给模型：

```text
tool_id
api_url
path
required_permissions
```

这些应该由系统保存和校验，而不是让模型决定。

### 5.3 转换规则

推荐转换规则：

```text
1. function.name 必须稳定。
   优先使用 operationId；如果 operationId 不符合模型函数名规则，再用 name_for_model。

2. function.name 只能包含字母、数字、下划线等安全字符。
   例如把 get-product/{id} 归一化成 get_product_by_id。

3. description 要短且可区分。
   不要把整段 OpenAPI 描述原样塞进去。

4. parameters 只包含业务参数。
   不暴露 api_url、path、method、权限 token、tenant_id 等系统字段。

5. required 必须来自 Tool Registry。
   不能让模型自己猜哪些字段必填。

6. enum 要保留。
   比如 region 只能是 ["华东", "华南", "华北"]。

7. in_ 字段不直接暴露给模型，但系统要保存。
   in_=path/query/body 决定后续 HTTP 请求怎么组装。
```

伪代码：

```python
def build_function_schema(tool):
    properties = {}
    required = []

    for param in tool.request_body:
        schema = {
            "type": normalize_json_schema_type(param.type),
            "description": param.description or param.name,
        }
        if param.format:
            schema["format"] = param.format
        if param.enum:
            schema["enum"] = list(param.enum)

        properties[param.name] = schema
        if param.required:
            required.append(param.name)

    return {
        "type": "function",
        "function": {
            "name": normalize_function_name(tool.operationId or tool.name_for_model),
            "description": build_tool_description(tool),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }
```

### 5.4 示例：库存查询工具

内部 Tool：

```json
{
  "tool_id": 12,
  "operationId": "getInventoryByProduct",
  "name_for_human": "查询产品库存",
  "description": "根据产品编号和仓库查询库存",
  "api_url": "https://erp.example.com",
  "path": "/inventory/{productId}",
  "method": "GET",
  "request_body": [
    {
      "name": "productId",
      "required": true,
      "type": "string",
      "description": "产品编号",
      "in_": "path"
    },
    {
      "name": "warehouseId",
      "required": false,
      "type": "string",
      "description": "仓库编号",
      "in_": "query"
    }
  ],
  "risk_level": "read"
}
```

转换后的 function schema：

```json
{
  "type": "function",
  "function": {
    "name": "getInventoryByProduct",
    "description": "查询产品库存。根据产品编号和仓库查询库存。该工具为只读查询工具。",
    "parameters": {
      "type": "object",
      "properties": {
        "productId": {
          "type": "string",
          "description": "产品编号"
        },
        "warehouseId": {
          "type": "string",
          "description": "仓库编号"
        }
      },
      "required": ["productId"]
    }
  }
}
```

模型只会返回：

```json
{
  "name": "getInventoryByProduct",
  "arguments": {
    "productId": "A15",
    "warehouseId": "WH-East"
  }
}
```

它不会自己拼：

```text
GET https://erp.example.com/inventory/A15?warehouseId=WH-East
```

这个 HTTP 请求由系统 Tool Runtime 负责。

### 5.5 Function Name 和内部 Tool 的映射

必须维护映射表：

```text
function_name -> tool_id -> Tool
```

示例：

```json
{
  "getInventoryByProduct": {
    "tool_id": 12,
    "operationId": "getInventoryByProduct"
  }
}
```

校验规则：

```text
模型返回的 function_name 必须存在于本轮候选工具映射表。
如果不在候选集合中，直接拒绝，不能去全局工具库里帮模型找。
```

原因：

```text
防止模型编造工具。
防止模型绕过 RAG 候选和权限预过滤。
防止模型选择本轮不该看到的高风险工具。
```

需要注意：

```text
function name 必须稳定，建议用 operationId 或 name_for_model。
description 要短且可区分，避免相似工具混淆。
parameters 必须来自 Tool Registry，而不是手写散落在 prompt 中。
enum、format、required 要尽量保留。
内部 tool_id 不一定暴露给模型，但需要能通过 function name 映射回来。
```

推荐映射表：

```text
function_name -> tool_id -> operationId -> Tool
```

## 5.6 Function Call 之后如何真正执行工具

正常 function call 并不是模型自己执行工具。

标准链路是：

```text
1. 系统把 tools/function schema 发给模型。
2. 模型返回 tool_call：function_name + arguments。
3. 系统解析 tool_call。
4. 系统校验 function_name 和 arguments。
5. 系统决定是否执行。
6. 系统调用真实工具。
7. 系统把工具结果作为 tool result 回传给模型或直接总结。
```

所以真实工具调用仍然是系统侧完成的。

对于当前项目，就是继续走：

```text
ToolUseHub.tool_use(tool, params)
```

当前 `ToolUseHub.tool_use()` 的执行方式是组装 HTTP 请求：

```text
url = tool.api_url + tool.path
method = tool.method
headers = {"X-API-Key": sim_api_key}

如果 param.in_ == path：
  替换 URL 路径参数，例如 /inventory/{productId}

如果 param.in_ == query：
  放入 query params

剩余参数：
  放入 JSON body

最终调用：
  requests.request(method, url, params=query_params, json=body, headers=headers)
```

也就是说，function call 改造后，执行层仍然像现在这样组装 HTTP 请求。

差异只是：

```text
现在：
参数来自 ParamExtractionHub 的抽取结果。

改造后：
参数来自 function call arguments。

但二者进入 ToolUseHub 前，都必须经过同一套校验和 HITL。
```

推荐执行链：

```text
function_call.arguments
-> proposal_validator
-> _tool_check / PermissionGuard / HallucinationGuard
-> pending_action=tool_execution_confirm
-> 用户确认
-> ToolUseHub.tool_use(tool, params)
-> tool_invocation_started / finished trace
```

不要使用自动执行模式：

```text
模型返回 tool_call
-> 框架自动调用 HTTP API
```

ERP 场景下这很危险，因为它可能绕过：

```text
权限校验
HITL
参数业务校验
幂等控制
审计日志
```

因此，当前项目即使改成 function call，也应该坚持：

```text
模型只提出 tool_call proposal。
系统负责真实 HTTP 调用。
```

## 6. 是否还需要 RAG 工具选择

需要，尤其是工具数量变多时。

不要把几十个甚至上百个工具 schema 全量塞给模型。

推荐：

```text
用户 query
-> RAG 召回 topK 工具
-> rerank
-> 权限预过滤
-> 只把候选工具 schema 传给 function call 模型
```

这样 function call 的角色是：

```text
在候选集合中做结构化选择和参数生成。
```

不是：

```text
从全量系统工具里自由选择。
```

好处：

```text
降低 prompt token。
减少误选工具。
避免模型看到无权限工具。
保留当前项目 RAG 工具选择的工程价值。
```

### 6.1 面试包装口径：RAG/rerank 后用 function call 做最终结构化选择

如果面试中表达为：

```text
工具选择链路使用 function call。
先通过 RAG 召回和 rerank 精筛得到 topK 候选工具。
再把 topK 工具信息转换成 function call 需要的 schema 格式。
把这些 function schema 传给大模型。
大模型输出要调用的工具和 arguments。
```

这个说法整体是成立的，而且比“把所有工具都塞给模型”更合理。

但建议补充几个关键点，否则面试官容易继续追问。

#### 6.1.1 function call 不是替代 RAG，而是候选集内的结构化选择

更准确的表述是：

```text
RAG/rerank 负责从全量工具库中缩小候选空间。
function call 负责在候选工具集合中做最终结构化选择，并同时生成 arguments。
```

不要说：

```text
function call 完全负责工具选择。
```

更推荐说：

```text
function call 负责候选集内的最终选择。
```

原因是 function call 本身不解决：

```text
全量工具检索
相似工具排序
权限预过滤
工具描述治理
候选工具质量评估
```

它解决的是：

```text
模型如何用结构化格式表达“我想调用哪个工具，以及参数是什么”。
```

#### 6.1.2 topK 候选工具要转换成 function schema，而不是 HTTP schema

这里建议说 `schema`，不要说 `scheme`。

转换后的 function schema 不是完整 HTTP 请求定义。

它只包含模型选择工具和生成参数所需的信息：

```text
function.name
稳定且唯一的工具函数名。

function.description
工具能力说明，包括适用场景和不适用场景。

function.parameters
JSON Schema，定义 arguments 的字段、类型、枚举、必填项和字段描述。
```

内部仍然要维护映射表：

```text
function_name -> tool_id -> operationId -> HTTP method/path/body
```

模型只看到 function schema。

真正执行时，系统再根据 `tool_id / operationId` 找到原始 ERP API 定义，并由 Tool Runtime 组装 HTTP 请求。

#### 6.1.3 function_name 必须做候选白名单校验

即使只把 topK schema 传给模型，也不能完全相信模型返回的 function name。

系统必须维护本轮候选工具白名单：

```python
candidate_tool_map = {
    "query_inventory": tool_001,
    "query_order": tool_002,
    "query_reconciliation": tool_003,
}
```

模型返回后先校验：

```text
function_name 是否在 candidate_tool_map 中。
arguments 是否符合该 function 的 JSON Schema。
```

如果模型返回了不存在的工具名，或者返回了非候选工具：

```text
拒绝该 proposal
记录 function_call_rejected
必要时重试或返回 no_tool_found
```

这样可以避免模型绕过候选工具边界。

#### 6.1.4 要明确 no tool / no_tool_found 怎么处理

面试官可能会追问：

```text
如果 topK 里其实没有合适工具怎么办？
function call 会不会被迫选一个？
```

建议设计两个策略。

策略一：前置过滤。

```text
RAG/rerank 后，如果候选工具相关性低于阈值，直接返回 no_tool_found，不进入 function call。
```

策略二：允许模型不调用工具。

```text
tool_choice = auto
```

或者提供一个安全的 `no_tool` function：

```json
{
  "type": "function",
  "function": {
    "name": "no_tool",
    "description": "当用户请求不属于 ERP 工具能力范围，或候选工具都不匹配时选择该函数。",
    "parameters": {
      "type": "object",
      "properties": {
        "reason": {
          "type": "string",
          "description": "不调用工具的原因"
        }
      },
      "required": ["reason"]
    }
  }
}
```

ERP 工具窗口如果已经明确是业务工具调用模式，可以更保守：

```text
低相关性 -> no_tool_found
高相关性 -> function call required
```

不要让模型在明显不匹配的候选工具里硬选。

#### 6.1.5 function call 同时做工具选择和参数候选生成，但不等于最终参数

你的说法中“大模型输出对应的工具和参数”是可以的，但建议补一句：

```text
模型输出的是 ToolCallProposal，不是最终可执行参数。
```

function call 返回：

```json
{
  "name": "create_order",
  "arguments": {
    "product_id": "A15",
    "quantity": 100,
    "supplier_id": "SUP-A"
  }
}
```

系统后续仍然要做：

```text
JSON Schema 校验
required 参数校验
类型 / enum / format 校验
实体解析和 ID 映射
权限校验
业务规则校验
HITL 确认
```

所以更准确的口径是：

```text
function call 把工具选择和参数抽取合并成一个结构化 proposal。
系统再对这个 proposal 做校验、确认和执行。
```

#### 6.1.6 第一版建议只允许 single tool call

如果用户请求是多步骤任务：

```text
先查库存，如果不足再创建采购申请。
```

不要让模型一次 function call 返回一串自动执行计划。

第一版推荐：

```text
function call 只允许返回一个 tool_call。
多步骤任务由 LangGraph / planner 控制下一步。
每一步工具调用都要重新进入 validate + HITL gate。
```

这样更容易保证：

```text
状态可控
权限可控
HITL 可控
trace 可复盘
eval 可评测
```

#### 6.1.7 推荐完整链路

面试时可以按这个链路讲：

```text
用户 query
-> ContextManager 组装上下文
-> Tool Registry 获取全量工具元数据
-> 权限预过滤
-> Milvus 召回 topK * 2
-> rerank 精排到 topK
-> FunctionSchemaBuilder 将 topK 工具转换成 function schema
-> 调用支持 function call 的模型
-> 模型返回 function_name + arguments
-> ToolCallProposalValidator 校验候选白名单和 JSON Schema
-> 业务参数校验 / 实体解析 / 权限校验
-> HITL 展示工具名、参数、参数来源和风险
-> 用户确认后 Tool Runtime 执行 ERP HTTP API
-> 结果总结
-> trace / eval 记录
```

其中，关键边界是：

```text
RAG/rerank 决定“给模型看哪些候选工具”。
function call 决定“模型在候选工具中建议调用哪个工具、带什么参数”。
validator 决定“这个建议能不能进入执行链路”。
HITL 决定“用户是否确认执行”。
Tool Runtime 才真正调用 ERP API。
```

#### 6.1.8 推荐面试表达

可以这样说：

```text
我们不是把全量工具都塞给模型做 function call，而是采用 RAG/rerank + function call 的两阶段工具选择。

第一阶段，Tool Registry 里维护所有 ERP 工具的结构化元数据，先根据用户 query 做向量召回和 rerank，只保留 topK 候选工具，并在进入模型前做权限预过滤。

第二阶段，把这 topK 个候选工具转换成模型支持的 function schema，只把候选 schema 传给模型。模型通过 function call 返回 function_name 和 arguments，相当于生成一个 ToolCallProposal。

但这个 proposal 不会直接执行。系统会校验 function_name 是否在本轮候选白名单里，arguments 是否符合 JSON Schema，参数在业务上是否合法，用户是否有权限。如果缺参就澄清，如果是写操作或高风险动作就进入 HITL，用户确认后才由 Tool Runtime 调用 ERP API。

所以 function call 在这里不是替代 RAG，也不是替代权限和 HITL，而是把候选集内的最终工具选择和参数抽取变成结构化输出，降低自由文本解析和参数格式错误。
```

## 7. 参数校验如何保留

function call 的 schema 只能保证格式更稳定，不能保证参数业务正确。

必须保留校验链：

```text
1. JSON 是否能解析。
2. tool_name 是否在候选工具白名单。
3. arguments 是否符合 JSON schema。
4. required 参数是否齐全。
5. 类型是否正确。
6. enum 是否合法。
7. 数量、金额、日期等业务字段是否合法。
8. 产品、供应商、仓库等实体是否存在。
9. 参数是否在用户权限范围内。
10. 当前 query 与 arguments 是否明显冲突。
```

校验结果分支：

```text
缺参 -> missing_params_clarify
格式错误 -> function_call_repair 或澄清
权限失败 -> permission_blocked
高风险 -> tool_execution_confirm
通过 -> HITL 或执行
```

建议新增事件：

```text
function_call_proposed
function_call_schema_validated
function_call_schema_failed
function_call_repaired
function_call_rejected
```

## 8. HITL 如何保留

HITL 必须保留。

function call 返回后，不直接执行工具，而是创建确认 gate。

示例：

```text
模型返回：
tool_name = create_purchase_order
arguments = {
  "product_id": "A15",
  "supplier_id": "A供应商",
  "quantity": 100
}

系统展示：
模型建议创建采购申请：
- 产品：A15
- 供应商：A供应商
- 数量：100
是否确认执行？
```

用户确认后：

```text
再次校验参数和权限
-> 调用 ToolUseHub.tool_use()
```

用户修改：

```text
可以，但数量改成 50
-> 合并参数
-> 重新 schema 校验
-> 重新权限校验
-> 根据风险决定是否再次确认
```

用户取消：

```text
finish_trace(status=aborted)
```

HITL 相关字段仍然复用：

```text
pending_action = tool_execution_confirm
pending_payload = {
  "proposal_source": "function_call",
  "function_name": "...",
  "tool_id": ...,
  "operation_id": "...",
  "params": {...}
}
```

## 9. LangGraph 如何改造

当前图：

```text
load_task
-> classify_task
-> select_tool
-> check_tool
-> persist_decision
```

第一阶段建议保留大结构：

```text
select_tool
仍然做 RAG 候选召回和权限预过滤。

check_tool
内部调用 function call proposal，得到 arguments 后进入原有 _tool_check 校验链。

persist_decision
仍然创建 HITL gate 或阻断。
```

第二阶段可以拆成更标准节点：

```text
load_task
-> classify_task
-> retrieve_candidate_tools
-> propose_tool_call
-> validate_tool_call
-> persist_decision
```

新增 state 字段：

```python
candidate_tools: List[Dict[str, Any]]
function_name: str
function_arguments: Dict[str, Any]
proposal_source: str
proposal_validation: Dict[str, Any]
```

新增 trace：

```text
candidate_tools_retrieved
function_call_proposed
function_call_validated
function_call_rejected
```

## 10. Trace 和 Eval 如何改造

Function call 改造后，评测不能只看是否调用成功。

新增评测维度：

```text
function_name_accuracy
模型返回的 function_name 是否正确。

arguments_schema_valid_rate
arguments 是否符合 schema。

arguments_business_valid_rate
业务参数是否正确。

candidate_tool_hit_rate
正确工具是否出现在 RAG 候选集合。

tool_whitelist_block_rate
模型选择候选外工具时是否被拦截。

hitl_preservation_rate
写操作是否仍然进入 HITL。

unsafe_auto_execution_rate
未确认前是否错误执行工具，目标应为 0。
```

集成测试要覆盖：

```text
1. 正确 function call。
2. function name 幻觉。
3. arguments 缺参。
4. arguments 类型错误。
5. enum 错误。
6. 参数越权。
7. 写操作必须 HITL。
8. 用户确认后执行。
9. 用户修改参数后重新校验。
10. 用户取消后不执行。
11. 工具候选召回失败时不允许模型编造工具。
```

## 11. 需要甲方配合什么

Function call 改造不是只改 Agent 代码。

甲方需要配合或至少确认：

```text
1. 接口 schema 是否完整
   参数类型、required、enum、format、默认值是否准确。

2. 工具命名是否稳定
   operationId 是否可作为 function name。

3. 参数语义是否清楚
   同名字段在不同接口中含义是否一致。

4. 权限字段是否可用
   用户、租户、区域、部门、角色权限是否能被 Agent 查询。

5. 风险等级是否可定义
   哪些是 read，哪些是 write，哪些是 high risk。

6. 幂等和审计是否支持
   写操作是否能提供 operation_id / idempotency_key。

7. 错误码是否标准
   便于模型总结和系统重试/澄清。

8. 测试环境是否可用
   function call 改造必须有沙箱 ERP 环境验证。
```

如果甲方不配合，仍然可以做内部 function call proposal，但会有明显限制：

```text
schema 不准确 -> 参数错误率高。
权限不完整 -> 无法安全放行。
错误码不标准 -> 异常处理困难。
没有沙箱 -> 不适合验证写操作。
没有幂等 -> resume / 重试有重复执行风险。
```

## 12. 风险和限制

Function call 不是银弹。

主要风险：

```text
1. 模型仍可能选错工具。
2. 模型仍可能填错参数。
3. schema 太复杂时模型仍可能不遵循。
4. 不同模型 function call 协议不同。
5. 私有化模型可能不支持或支持不稳定。
6. 自动执行模式容易绕过 HITL。
7. 工具 schema 维护成本会上升。
```

控制措施：

```text
只给候选工具，不给全量工具。
不允许候选外工具名。
arguments 必须 schema 校验。
业务参数必须二次校验。
写操作必须 HITL。
权限失败必须阻断。
trace 记录 proposal 和 validation。
eval 增加 function call 专项数据集。
```

## 13. 推荐最小落地方案

第一版不要做太重。

推荐 MVP：

```text
1. 保留当前 RAG 工具召回。
2. 只把 topK 候选工具转换成 function schema。
3. function call 只允许返回一个 tool_call。
4. 返回后转成 ToolCallProposal。
5. 校验 tool_name 是否在候选集合中。
6. 校验 arguments schema。
7. 复用当前 _tool_check、PermissionGuard、HallucinationGuard。
8. 复用 pending_action / pending_payload HITL。
9. 复用 ToolUseHub 执行。
10. 增加 function_call_proposed / validated trace。
11. 增加 eval cases。
```

不建议第一版做：

```text
多 tool call 自动执行。
模型自动决定是否跳过 HITL。
全量工具 schema 直接塞给模型。
模型返回 tool_call 后框架自动执行。
复杂 planner + function call 多步计划。
```

## 14. 代码模块建议

可以新增：

```text
function_calling/
  __init__.py
  tool_schema_builder.py
  function_call_hub.py
  proposal_validator.py
  tool_call_proposal.py
```

职责：

```text
tool_schema_builder.py
把内部 Tool 转成模型 function schema。

function_call_hub.py
调用支持 tool calling 的 LLM，返回 ToolCallProposal。

proposal_validator.py
校验 function_name、arguments、schema、候选白名单。

tool_call_proposal.py
定义结构化 proposal。
```

推荐数据结构：

```json
{
  "proposal_id": "prop_001",
  "source": "function_call",
  "function_name": "create_purchase_order",
  "tool_id": 12,
  "operation_id": "createPurchaseOrder",
  "arguments": {
    "product_id": "A15",
    "supplier_id": "SUP-A",
    "quantity": 100
  },
  "raw_model_output": {},
  "validation": {
    "schema_valid": true,
    "candidate_allowed": true,
    "missing_params": []
  }
}
```

## 15. 面试表达口径

可以这样讲：

```text
如果后续改成 function call，我不会让模型直接执行工具，而是把 function call 当成结构化 tool proposal。模型只负责输出 function_name 和 arguments，系统负责判断这个 proposal 能不能执行。

具体链路是：先通过 RAG 和权限过滤得到候选工具，只把候选工具 schema 传给模型；模型返回 tool_call 后，系统校验工具名是否在候选集合中、arguments 是否符合 schema、业务参数是否合法、用户是否有权限。如果缺参就进入澄清，如果是写操作或高风险操作就进入 HITL，用户确认后才真正调用 ERP API。

所以 function call 改造不会取消当前的工具选择、参数校验、权限校验、HITL 和 trace。它只是把原来 LLM 自由文本式的工具选择和参数抽取，升级成更结构化的 tool proposal。
```

一句话总结：

```text
function call 解决的是“模型如何规范表达想调用什么工具”，不是“工具是否应该被执行”。执行权必须留在系统侧。
```

## 16. 参考资料

- OpenAI Function Calling / Tool Calling: https://platform.openai.com/docs/guides/function-calling
- OpenAI Tools: https://platform.openai.com/docs/guides/tools
- Anthropic Tool Use: https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview
- LangGraph Tool Calling: https://docs.langchain.com/oss/python/langgraph/overview
