# 显式长期偏好记忆设计方案

本文档设计一个适合当前 ERP Agent 项目的轻量长期记忆方案。

核心结论：

```text
可以增加长期记忆，但当前阶段不建议做“模型自动沉淀长期记忆”。
更稳妥的方案是做“用户显式配置的长期偏好”。
```

也就是说，长期记忆不由 LLM 自动猜测、自动写入，而是由用户在前端明确填写、修改、关闭或删除。

例如：

```text
默认供应商优先选择 A 供应商。
库存安全阈值不能低于 200。
导出报表时默认使用 Excel。
发送邮件前必须人工确认。
```

这类信息来源明确、风险可控、可编辑、可审计，比让模型从历史对话里自动总结用户偏好更适合 ERP 场景。

## 1. 为什么这个方案可行

你的原始想法是：

```text
前端提供一个地方，让用户自己填写偏好。
后端按 user_id 存数据库。
每次和大模型对话时加载这部分长期记忆。
用户可以随时修改。
为了控制上下文长度，把内容限制在 100 字。
```

这个方向整体可行，原因是：

```text
1. 记忆来源明确，是用户自己填写，不是模型猜的。
2. 修改入口明确，用户可以自主管理。
3. 按 user_id 隔离，避免不同用户记忆串用。
4. 内容较短，对 prompt token 压力小。
5. 比复杂的长期记忆系统更容易实现和评测。
```

这个方案可以继续简化。当前阶段不建议上来就做场景识别、scope 过滤和 prompt_type 分流，因为这会带来额外判断逻辑和评测成本。

真正需要保留的约束是：

```text
1. 只按 user_id 不够，ERP 场景通常还需要 tenant_id / org_id 隔离。
2. 长期偏好必须短、稳定、可编辑、可关闭。
3. 可以每次统一注入，但不能让它覆盖当前 query。
4. 业务偏好不能直接绕过权限校验、参数校验和 HITL。
5. 系统需要记录偏好版本，便于 prompt cache 命中和问题排查。
```

所以当前推荐的最小方案是：

```text
前端显式配置
+ 数据库存储一段短文本偏好
+ 按 tenant_id + user_id 隔离
+ 每次 LLM 调用统一注入到 prompt 前部
+ 不做场景识别
+ 不做模型自动写入长期记忆
+ 只作为偏好和约束，不作为强执行事实
+ 工具调用前仍然经过参数校验、权限校验和 HITL
```

这一版的重点不是“记忆能力做得复杂”，而是把长期记忆做成稳定、可控、可解释的 prompt 前缀。

## 2. 记忆类型划分

这一节是理解层面的分类，不要求当前最小版本一定在代码里做复杂分类。

当前最小版本可以只保存一段 `preference_text`，但在 prompt 中明确告诉模型：这段内容可能同时包含展示偏好、业务默认值和业务约束，使用时不能覆盖当前 query，也不能跳过权限和 HITL。

如果后续要增强，再把自由文本拆成结构化类型。

推荐至少分成三类：

### 2.1 展示偏好

低风险偏好，主要影响最终回答或页面展示。

例如：

```text
回答尽量用表格。
金额保留两位小数。
导出报表默认 Excel 格式。
```

这类偏好可以比较直接地进入 prompt，因为它不直接改变业务数据，也不会触发高风险工具。

### 2.2 业务默认值

中风险偏好，用于减少用户输入，但不能直接作为写操作依据。

例如：

```text
默认供应商优先选择 A 供应商。
默认仓库为华东仓。
默认查询最近 30 天。
```

这类偏好只能用于：

```text
生成候选参数
提示模型优先询问或建议
在 HITL 中展示默认值
```

不能用于：

```text
绕过用户确认
直接创建订单
直接修改库存
直接发送邮件
覆盖当前 query 中明确给出的参数
```

### 2.3 业务约束 / 安全规则

较高价值的长期偏好，通常用于校验和拦截。

例如：

```text
库存安全阈值不能低于 200。
任何导出操作都要确认。
单笔采购金额超过 5 万必须人工确认。
```

这类信息更像用户级 guardrail，不只是 prompt 背景。

它应该进入：

```text
参数校验
权限校验
HITL 判断
最终回答解释
```

例如用户说：

```text
帮我把 A15 的安全库存改成 100。
```

如果用户长期偏好里写着：

```text
库存安全阈值不能低于 200。
```

系统应该拦截或进入确认：

```text
你设置过库存安全阈值不能低于 200。当前请求要改成 100，低于你的长期约束，是否确认继续？
```

如果这是硬约束，则直接拒绝；如果是软偏好，则进入 HITL。

## 3. 推荐数据结构

当前最小版可以只保存一段短文本，但要补齐隔离、版本和启用状态。

推荐最小 schema：

```json
{
  "tenant_id": "tenant_001",
  "user_id": "user_001",
  "preference_text": "默认供应商选择 A 供应商；库存安全阈值不能低于 200；导出报表优先 Excel。",
  "enabled": true,
  "source": "user_manual",
  "version": 3,
  "version_hash": "sha256:xxxx",
  "max_chars": 300,
  "created_at": "2026-06-17T10:00:00+08:00",
  "updated_at": "2026-06-17T10:30:00+08:00"
}
```

字段说明：

```text
tenant_id + user_id
长期偏好必须按租户和用户隔离。

preference_text
用户在前端手动维护的一段短文本，建议限制 100 到 300 字。

enabled
用户可以一键关闭长期偏好注入。

source
当前只允许 user_manual，表示用户手动写入，不允许 LLM 自动写入。

version / version_hash
用于判断偏好是否变化，也方便排查 prompt cache 是否失效。

max_chars
控制长期偏好最大长度，避免 prompt 被长期记忆撑大。
```

如果后续要做更精细的策略，再扩展结构化 schema。

增强版 schema 示例：

```json
{
  "preference_id": "pref_001",
  "tenant_id": "tenant_001",
  "user_id": "user_001",
  "scope": "procurement",
  "type": "business_default",
  "key": "default_supplier",
  "value": "A供应商",
  "display_text": "默认供应商优先选择 A 供应商",
  "enabled": true,
  "priority": 50,
  "source": "user_manual",
  "allowed_usage": ["prompt_context", "draft_param", "hitl_display"],
  "forbidden_usage": ["direct_write_execution", "skip_hitl"],
  "expires_at": null,
  "created_at": "2026-06-17T10:00:00+08:00",
  "updated_at": "2026-06-17T10:00:00+08:00"
}
```

字段说明：

```text
tenant_id
租户或企业隔离，避免不同公司数据串用。

user_id
用户隔离，确保只加载当前用户自己的偏好。

scope
偏好作用域，例如 inventory、procurement、finance、report、global。

type
偏好类型，例如 display_preference、business_default、business_constraint。

key / value
结构化字段，便于程序判断。

display_text
给 LLM 看的短文本，便于注入 prompt。

enabled
用户可以在前端关闭某条偏好。

priority
多条偏好冲突时用于排序。

allowed_usage / forbidden_usage
明确这条偏好能用于什么，不能用于什么。

expires_at
可选过期时间，避免临时偏好永久生效。

source
当前只允许 user_manual，表示用户手动配置。
```

## 4. 前端设计

前端可以提供一个“我的偏好 / Agent 偏好设置”页面。

当前最小版本推荐只提供一个短文本框，不做复杂分类。

```text
长期偏好：多行文本框
启用长期偏好：开关
保存 / 清空：按钮
最近更新时间：只读展示
```

示例：

```text
默认供应商选择 A 供应商；
库存安全阈值不能低于 200；
导出报表优先 Excel；
发送邮件前必须人工确认。
```

限制建议：

```text
总长度 100 到 300 字。
不允许上传文件。
不允许多条无限追加。
用户可以随时修改、清空或关闭。
```

这一版牺牲了一些精细控制，但换来实现简单、解释清楚、评测成本低。

## 5. 加载策略

当前最小版本采用“全部注入”。

```text
1. 根据 tenant_id + user_id 读取当前用户启用偏好。
2. 如果 enabled=false 或 preference_text 为空，则不注入。
3. 如果 enabled=true，则每次 LLM 调用都把 preference_text 放在 prompt 前部。
4. 不判断库存、采购、财务等业务场景。
5. 不按 prompt_type 做复杂分流。
6. 通过最大长度控制 token 成本。
```

这样做的理由：

```text
1. 实现简单，不需要额外意图识别。
2. 评测简单，只需要验证是否按 user_id / tenant_id 正确加载。
3. 用户偏好很短，长期 token 开销可控。
4. 偏好放在 prompt 前缀，有机会命中 prompt caching。
```

需要坚持的边界：

```text
长期偏好可以被所有 LLM prompt 看见。
但长期偏好不能覆盖当前 query。
长期偏好不能绕过权限校验。
长期偏好不能绕过参数校验。
长期偏好不能绕过 HITL。
写操作仍然必须确认。
```

## 6. 和上下文压缩机制的关系

当前我们设计的上下文机制可以分成：

```text
current_query
+ recent_working_set
+ conversation_summary
+ retrieved_evidence / tool_result_summary
+ user_preference_memory
```

其中各部分职责不同：

```text
current_query
当前用户本轮明确表达，优先级最高。

recent_working_set
按 token budget 保留的最近原文上下文，用于处理指代、HITL、刚刚的工具结果。

conversation_summary
较早历史的压缩摘要，只帮助模型理解前文，不作为强事实。

retrieved_evidence / tool_result_summary
工具结果或历史证据摘要，带 result_id，可追溯。

user_preference_memory
用户显式配置的长期偏好，用于默认值建议、展示偏好和约束提醒。
```

优先级建议：

```text
1. 系统安全规则 / 权限规则
2. 当前用户 query
3. 当前任务状态和 HITL 状态
4. 最近原始消息和工具结果
5. 用户显式长期偏好
6. conversation_summary
```

也就是说：

```text
当前 query 明确说供应商选择 B，则不能因为长期偏好默认 A 而改成 A。
当前权限不允许访问华南区，则不能因为长期偏好默认华南区而放行。
当前任务是写操作，则不能因为长期偏好存在默认值而跳过 HITL。
```

## 7. 和 Token Budget Compaction 的关系

长期偏好不应该进入 conversation_summary，也不应该被会话 compaction 改写。

它应该作为稳定 prompt 前缀的一部分注入：

```text
System Prompt
+ Safety / Tool Rules
+ User Preference Memory
+ Conversation Summary
+ Recent Working Set
+ Retrieved Evidence
+ Current User Query
```

这样设计的好处是：

```text
1. 用户偏好不会被 summary 压缩丢失。
2. summary 失败不会污染长期偏好。
3. 用户修改偏好后，下一轮立即生效。
4. 可以单独评测长期偏好是否正确加载和使用。
5. System Prompt + User Preference Memory 相对稳定，有机会命中 prompt caching。
```

如果 prompt token 接近上限，裁剪优先级建议是：

```text
1. 裁剪低相关 RAG 片段。
2. 压缩工具 observation。
3. 压缩较早 session history。
4. 保留当前 query、HITL 状态、安全规则和必要工具 schema。
5. 如果长期偏好仍然导致超限，说明偏好文本过长，应要求用户缩短或在后端截断。
```

长期偏好的 token 控制方式：

```text
preference_text <= 100 到 300 字。
长期偏好整体 <= 500 tokens。
不做多条偏好的复杂筛选。
超过限制时直接拒绝保存或截断，并提示用户缩短。
```

### 7.1 Prompt Caching / KV Cache 关系

把 system prompt 和用户长期偏好放在每次调用的最前面，有机会降低成本和延迟，但前提是模型服务支持 prompt caching。

#### 7.1.1 Prompt Caching 是什么

Prompt Caching 可以理解为模型服务商对“重复 prompt 前缀”的复用机制。

普通 LLM 调用时，每次请求都会重新处理输入 tokens：

```text
system prompt
+ tool rules
+ user preference memory
+ conversation summary
+ recent messages
+ current query
-> 模型重新计算整段输入
```

如果大量请求的前半部分完全相同，例如 system prompt、工具规则、长期偏好都不变，那么服务商可以缓存这段前缀已经计算过的中间状态。下一次请求再出现同样前缀时，模型不必完整重复计算这部分，从而降低输入成本和延迟。

可以简化理解为：

```text
第一次请求：
稳定前缀 A + 动态内容 X -> 计算 A 和 X

第二次请求：
稳定前缀 A + 动态内容 Y -> 复用 A，只主要计算 Y
```

这里的“稳定前缀 A”就是 prompt caching 的核心。

#### 7.1.2 和 KV Cache 的区别

```text
KV cache
通常是模型推理服务内部的 attention cache。

Prompt caching
API 服务商对重复 prompt prefix 做缓存复用，命中后输入 token 成本和延迟可能降低。
```

两者关系：

```text
KV cache 是底层推理技术概念。
Prompt caching 是 API 产品能力。
API 用户通常不能直接操作 KV cache，只能通过稳定 prompt prefix 来提高 prompt caching 命中率。
```

所以面试或项目文档里更稳的说法是：

```text
我们把 system prompt 和长期偏好做成稳定前缀，以利用模型服务的 prompt caching 能力。
```

而不是说：

```text
我们直接控制了模型 KV cache。
```

后者容易被追问底层推理框架实现。

#### 7.1.3 Prompt Caching 如何生效

主流 API 的具体机制不完全一样，但共同点是：

```text
1. 缓存的是 prompt 前缀，不是任意中间片段。
2. 前缀越稳定，越容易命中。
3. 动态内容应该放在后面。
4. 前缀变化会导致缓存失效或重新建立。
5. 缓存命中后，服务商通常会在 usage 中返回 cached_tokens 或类似字段。
```

OpenAI 的 prompt caching 是自动触发的。它会缓存请求开头重复出现的 token 前缀，适合 system prompt、工具定义、长文档前缀等稳定内容。官方文档中也强调，可以通过返回的 cached tokens 观察缓存命中情况。

Anthropic 的 prompt caching 更强调 cache breakpoint 和缓存生命周期。开发者可以把较稳定的大块内容放在 cache breakpoint 之前，例如系统指令、工具定义、背景文档等。

Gemini 也有 context caching / implicit caching 的思路，用于复用大段稳定上下文，减少重复输入成本。

虽然不同厂商实现不同，但工程原则相同：

```text
把稳定内容放前面。
把动态内容放后面。
保持稳定内容字面一致。
记录缓存命中指标。
```

#### 7.1.4 本项目为什么适合做稳定前缀

当前项目里有几类内容天然比较稳定：

```text
Global System Prompt
Agent 的角色、行为边界、输出要求。

Safety / Tool Rules
权限、参数校验、HITL、不得跳过确认等规则。

User Preference Memory
用户手动维护的长期偏好，通常不会频繁变化。
```

这些内容可以组成 Stable Prefix：

```text
Stable Prefix:
1. Global System Prompt
2. Safety / Tool Rules
3. User Preference Memory

Dynamic Context:
4. Conversation Summary
5. Recent Working Set
6. Tool Result Summary
7. Current User Query
```

这样设计有两个好处：

```text
1. 上下文结构更清晰。
2. 如果模型服务支持 prompt caching，稳定前缀更容易命中缓存。
```

#### 7.1.5 如何提高缓存命中率

当前项目可以按 prompt caching 的最佳实践组织 prompt：

```text
1. Global System Prompt
   尽量固定，全用户共享。

2. Agent Safety / Tool Rules
   尽量固定，例如权限、HITL、参数校验规则。

3. User Preference Memory
   当前用户长期偏好，用户不修改时基本固定。

4. Dynamic Context
   conversation_summary、recent_messages、tool results、current_query。
```

具体要求：

```text
静态内容放前面。
动态内容放后面。
不要在前缀里放当前时间、trace_id、request_id、随机字段。
长期偏好顺序和格式保持稳定。
用户修改长期偏好后，version_hash 变化，缓存重新开始。
```

还要注意：

```text
1. 不要每次重新排序长期偏好。
2. 不要在稳定前缀里拼接“当前时间”。
3. 不要在稳定前缀里拼接任务 ID、trace ID、request ID。
4. 不要在稳定前缀里拼接每轮不同的工具结果。
5. 不要让 prompt 模板在不同调用里随机换行或换格式。
```

一个反例：

```text
System Prompt:
当前时间：2026-06-17 10:01:03
当前请求 ID：req_abc
...
```

这个写法会让每次请求前缀都不同，缓存很难命中。

正确做法：

```text
System Prompt:
你是 ERP Agent，需要遵守权限校验、参数校验和 HITL 流程。

User Preference Memory:
默认供应商选择 A 供应商；库存安全阈值不能低于 200。

Dynamic Context:
当前时间：2026-06-17 10:01:03
当前请求 ID：req_abc
当前用户请求：...
```

动态字段放在后面，即使后面变化，也不会破坏前面稳定前缀。

#### 7.1.6 哪些情况会导致缓存失效

常见失效原因：

```text
1. system prompt 发生变化。
2. 用户长期偏好发生变化。
3. prompt 模板换行、标点、空格发生变化。
4. 把当前时间、trace_id、request_id 放到了前缀里。
5. 工具描述或工具列表每次顺序不同。
6. 模型、接口或服务商不支持 prompt caching。
7. 缓存超过服务商 TTL。
8. prompt 前缀太短，没有达到服务商缓存阈值。
```

所以要给长期偏好维护 `version_hash`：

```text
preference_text 不变 -> version_hash 不变 -> 前缀稳定。
preference_text 变化 -> version_hash 变化 -> 缓存重新建立。
```

#### 7.1.7 是否一定能省钱

不一定。

需要注意：

```text
1. 是否省钱取决于模型服务商是否支持 prompt caching。
2. OpenAI、Anthropic、Gemini 等主流服务都有类似能力，但具体命中条件和计费不同。
3. 国内模型或私有化部署不一定支持，需要看网关、推理框架或模型 API。
4. OpenAI 这类服务通常要求重复 prefix 足够长才会产生明显缓存收益。
5. 如果 system prompt + 长期偏好很短，收益可能不明显，但 prompt 结构仍然更清晰。
```

也就是说，设计上应该这样表述：

```text
稳定前缀为 prompt caching 创造条件。
是否真的省钱，要看模型服务返回的 cached_tokens 和计费规则。
```

不要说：

```text
只要把长期记忆放前面就一定省钱。
```

#### 7.1.8 如何验证是否命中缓存

建议记录模型返回的缓存字段，例如：

```text
cached_tokens
prompt_tokens
completion_tokens
preference_version_hash
```

这样可以验证长期偏好前缀是否真的带来了缓存命中，而不是只停留在设计上。

推荐 trace：

```json
{
  "event_type": "prompt_cache_usage",
  "model": "xxx",
  "prompt_tokens": 4200,
  "cached_tokens": 1800,
  "preference_version_hash": "sha256:xxxx",
  "stable_prefix_hash": "sha256:yyyy"
}
```

推荐指标：

```text
prompt_cache_hit_rate = cached_tokens > 0 的请求数 / 总请求数
cached_token_ratio = cached_tokens / prompt_tokens
average_cached_tokens_per_request
preference_version_change_count
```

如果长期偏好不变，但 cached_tokens 长期为 0，需要检查：

```text
模型服务是否支持 prompt caching。
稳定前缀是否真的一致。
前缀是否太短。
是否把动态字段放进了前缀。
是否每次工具描述顺序都在变化。
```

## 8. 使用规则

长期偏好进入 prompt 时，应明确告诉模型：

```text
以下是用户显式配置的长期偏好。
它们可以用于理解用户习惯、生成候选参数或提醒约束。
它们不能覆盖当前用户请求。
它们不能绕过权限校验、参数校验和 HITL。
当前 query 与长期偏好冲突时，以当前 query 为准；高风险冲突需要向用户确认。
```

推荐 prompt 片段：

```text
User Preference Memory:
以下偏好由当前用户在前端显式配置，按 user_id 和 tenant_id 隔离。
这些偏好只用于辅助理解、默认候选和约束提醒。
不要仅凭偏好执行写操作，不要跳过权限校验和人工确认。
如果当前用户请求与偏好冲突，以当前请求为准；涉及高风险动作时先确认。

用户长期偏好：
默认供应商选择 A 供应商；库存安全阈值不能低于 200；导出报表优先 Excel。
```

## 9. 具体例子

### 9.1 默认供应商

用户偏好：

```text
默认供应商优先选择 A 供应商。
```

用户请求：

```text
帮我创建一张 A15 的采购申请。
```

正确处理：

```text
系统可以把 A 供应商作为候选默认值。
如果创建采购申请是写操作，仍然进入 HITL。
确认信息中展示：供应商将使用你的默认偏好 A 供应商，是否确认？
```

错误处理：

```text
直接使用 A 供应商创建采购申请，不确认。
```

如果用户请求：

```text
帮我用 B 供应商创建一张 A15 的采购申请。
```

正确处理：

```text
当前 query 明确指定 B 供应商。
以 B 供应商为准。
不能被长期偏好 A 覆盖。
```

### 9.2 库存最低阈值

用户偏好：

```text
库存安全阈值不能低于 200。
```

用户请求：

```text
把 A15 的安全库存调成 150。
```

正确处理：

```text
系统发现请求低于用户长期约束。
如果这是硬约束，拒绝并说明原因。
如果这是软约束，进入 HITL，提示用户该操作低于长期设置。
```

错误处理：

```text
直接调用修改库存工具，把安全库存改成 150。
```

### 9.3 展示偏好

用户偏好：

```text
库存查询结果优先用表格展示。
```

用户请求：

```text
查一下 A15 和 E7 的库存。
```

正确处理：

```text
工具查询结果仍按正常流程获取。
最终回答时使用表格。
```

这类偏好不影响工具参数，只影响回答格式，风险较低。

## 10. 权限与安全边界

长期偏好不能替代权限系统。

例如用户偏好：

```text
默认查询华南区库存。
```

但当前用户权限只有：

```text
region_scope = ["华东区"]
```

则系统必须拒绝或澄清：

```text
你当前没有华南区库存查询权限，无法按该偏好执行。
```

由于当前最小版选择全部注入，不做复杂 scope 过滤，所以更重要的是在工具调用前做权限校验：

```text
长期偏好即使进入 prompt，也不能让工具越权。
模型可以看到用户偏好，但最终 API 调用必须经过 permission guard。
如果偏好涉及用户当前无权访问的组织、区域、供应商，工具调用前必须拦截。
```

这意味着：全部注入简化了上下文构造，但没有简化权限链路。权限仍然是工具调用前的硬门禁。

## 11. 数据流

完整链路如下：

```text
用户在前端设置偏好
-> 后端校验长度、tenant_id、user_id
-> 写入 UserPreferenceMemory
-> 生成 version_hash
-> 用户发起 Agent 请求
-> ContextManager 构造上下文
-> PreferenceMemoryManager 根据 tenant_id + user_id 读取启用偏好
-> 把 User Preference Memory 作为稳定 prompt 前缀注入
-> LLM 做工具选择、参数抽取或回答
-> 工具调用前仍然走参数校验、权限校验、HITL
-> trace 记录 preference_loaded、preference_applied、preference_conflict、cached_tokens
```

推荐新增 trace：

```json
{
  "event_type": "preference_memory_loaded",
  "user_id": "user_001",
  "tenant_id": "tenant_001",
  "enabled": true,
  "token_count": 126,
  "version_hash": "sha256:xxxx"
}
```

如果偏好参与了候选参数：

```json
{
  "event_type": "preference_memory_applied",
  "usage": "draft_param",
  "param_name": "supplier",
  "param_value": "A供应商",
  "requires_hitl": true
}
```

如果当前 query 和偏好冲突：

```json
{
  "event_type": "preference_memory_conflict",
  "current_query_value": "B供应商",
  "preference_value": "A供应商",
  "resolution": "current_query_wins"
}
```

如果模型服务返回缓存字段，也建议记录：

```json
{
  "event_type": "prompt_cache_usage",
  "preference_version_hash": "sha256:xxxx",
  "prompt_tokens": 4200,
  "cached_tokens": 1800
}
```

## 12. 后端模块建议

可以新增：

```text
entity/preference_memory_entity.py
preferences/preference_memory_manager.py
prompt/prompt_builder.py
```

职责划分：

```text
PreferenceMemoryManager
负责增删改查用户偏好，按 tenant_id + user_id 读取启用偏好。

PromptBuilder
负责把 Global System Prompt、Safety Rules、User Preference Memory 和动态上下文按稳定顺序拼接。

PreferencePolicy
当前最小版只负责两件事：偏好不能覆盖当前 query，偏好不能绕过权限、参数校验和 HITL。

TokenBudgetManager
负责限制 preference_text 长度，并记录 prompt_tokens / cached_tokens。
```

## 13. 评测设计

需要补充单元测试：

```text
1. user_id / tenant_id 隔离正确。
2. 禁用偏好不会注入 prompt。
3. preference_text 为空时不会注入。
4. 超过长度限制时拒绝保存或截断。
5. version_hash 在偏好变化后更新。
6. prompt 前缀顺序稳定：System Prompt -> Safety Rules -> User Preference Memory -> Dynamic Context。
7. 当前 query 与偏好冲突时，以 query 为准。
8. 偏好不会绕过权限和 HITL。
```

需要补充集成测试：

```text
1. 默认供应商作为候选值，但写操作仍然 HITL。
2. 当前 query 指定 B 供应商时，不被默认 A 覆盖。
3. 库存阈值偏好低于请求值时正常放行。
4. 请求值违反库存阈值时触发拦截或二次确认。
5. 展示偏好只影响最终回答格式，不影响工具参数。
6. 长期偏好被修改后，下一轮请求立即使用新偏好。
7. 不同用户同一 session_id 或不同 session 下不会串偏好。
8. 每次 LLM 调用前都包含相同格式的 User Preference Memory 前缀。
9. 如果模型返回 cached_tokens，trace 能记录缓存命中情况。
```

核心指标：

```text
preference_load_accuracy
长期偏好加载准确率。

preference_conflict_resolution_accuracy
当前 query 与偏好冲突时的处理准确率。

preference_safe_usage_rate
偏好没有绕过权限、参数校验和 HITL 的比例。

preference_token_overhead
长期偏好带来的平均 token 增量。

prompt_cache_hit_rate
支持 prompt caching 的模型服务中，稳定前缀的缓存命中率。
```

## 14. 最小可行版本

如果不想做复杂，建议最小版本如下：

```text
1. 前端提供“用户偏好设置”页面。
2. 页面只有一个多行文本框和启用开关。
3. 偏好总长度限制 100 到 300 字。
4. 字段包括 tenant_id、user_id、preference_text、enabled、version_hash、updated_at。
5. 只允许 user_manual 来源。
6. 每次 LLM 调用都把 User Preference Memory 放在 prompt 前部。
7. 不做 scope 过滤，不做 prompt_type 分流，不做模型自动写入。
8. 偏好只作为 prompt_context，不直接生成写操作参数。
9. 所有写操作仍然必须 HITL。
10. trace 记录偏好加载情况和 cached_tokens。
11. 补充偏好隔离、冲突、HITL、prompt 前缀稳定性的测试。
```

这个版本已经足够在面试中讲清楚：

```text
我们做了长期记忆，但不是不可控的模型自动记忆。
我们选择了用户显式配置的短偏好记忆，优先解决来源可信、可编辑、可审计、权限隔离和 prompt 前缀稳定性。
```

## 15. 面试表达口径

可以这样讲：

```text
我没有一上来做模型自动沉淀长期记忆，因为 ERP 场景里长期记忆如果来源不清楚，很容易造成参数污染和越权风险。

我们采用了更稳的显式偏好记忆：用户在前端维护一段很短的长期偏好文本，例如默认供应商、安全库存阈值、报表格式。这些偏好按 tenant_id 和 user_id 隔离，存到数据库，用户可以随时修改、清空或关闭。

在上下文构造时，我们没有做很复杂的场景识别，而是把这段短偏好统一放在每次 LLM 调用的 prompt 前部，和 system prompt 组成稳定前缀。这样实现简单，也更容易命中 prompt caching。动态内容，例如 conversation_summary、recent messages、工具结果和当前 query，都放在后面。

但是偏好不会覆盖当前 query，也不会绕过权限、参数校验和 HITL。比如用户默认供应商是 A，但本轮明确说用 B，就以 B 为准；如果用默认 A 创建采购申请，也仍然要人工确认。这样既能体现长期记忆能力，又不会把业务安全交给模型猜测。

如果模型服务支持 prompt caching，我们还会记录 cached_tokens，验证稳定前缀是否真的被缓存。这样长期偏好不仅服务个性化，也能在重复调用中降低一部分输入成本和首 token 延迟。
```

一句话总结：

```text
长期记忆可以做，但当前最稳的是“用户显式配置、全部注入、稳定前缀”的偏好记忆，而不是“模型自动总结出来的记忆”。
```

## 16. Prompt 系统落地建议

为了兼顾长期偏好和 prompt caching，建议建立统一的 prompt 构造系统。

推荐结构：

```text
Stable Prefix:
1. Global System Prompt
2. Safety / Tool Rules
3. User Preference Memory

Dynamic Context:
4. Conversation Summary
5. Recent Working Set
6. Retrieved Evidence / Tool Result Summary
7. Current User Query
```

稳定前缀示例：

```text
System:
你是 ERP Agent，需要遵守权限校验、参数校验和 HITL 流程。

Safety / Tool Rules:
写操作、删除操作、导出发送等动作必须经过人工确认。
长期偏好不能覆盖当前请求，不能绕过权限和 HITL。

User Preference Memory:
以下内容由当前用户在前端手动维护，按 tenant_id 和 user_id 隔离。
默认供应商选择 A 供应商；库存安全阈值不能低于 200；导出报表优先 Excel。
```

动态上下文示例：

```text
Conversation Summary:
{conversation_summary}

Recent Messages:
{recent_messages}

Tool Result Summary:
{tool_result_summary}

Current User Query:
{current_query}
```

落地要求：

```text
1. Stable Prefix 的文本格式保持稳定。
2. 不要把当前时间、trace_id、request_id 放进 Stable Prefix。
3. 用户长期偏好变化时更新 version_hash。
4. 动态上下文全部放在 Stable Prefix 后面。
5. 如果模型服务返回 cached_tokens，写入 trace。
6. 如果模型服务不支持 prompt caching，这套结构仍然保留，因为它能让上下文层次更清楚。
```

## 17. 参考资料

- OpenAI Prompt Caching: https://platform.openai.com/docs/guides/prompt-caching
- Anthropic Prompt Caching: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- Gemini Context Caching: https://ai.google.dev/gemini-api/docs/caching
