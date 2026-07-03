# 显式长期记忆与缓存机制设计

本文档整理面试中关于“显式长期记忆如何注入、是否每次查库、Redis 和 LRU 如何取舍、Prompt 前缀如何复用”的回答口径。

## 1. 背景

项目包装点之一是增加显式长期记忆：

```text
前端提供一个用户可编辑窗口。
用户可以维护不超过 200 行的长期个人偏好。
后端按 user_id / tenant_id 保存。
每次和 LLM 对话时，将这部分内容加入上下文前缀。
```

这里的长期记忆更准确地说是：

```text
显式用户画像记忆
用户可编辑偏好记忆
Profile Memory
```

它不是系统自动从对话中抽取出来的隐式长期记忆，也不是业务事实库，更不是权限系统。

典型内容包括：

```text
默认供应商优先选择 A 供应商。
库存低于 200 需要提醒。
报表默认按华东区展示。
金额展示保留两位小数。
导出表格默认包含 SKU、供应商、库存、更新时间。
```

## 2. 长期记忆的定位

长期记忆只用于帮助模型理解用户偏好和默认表达。

它不能替代：

```text
当前用户 query
工具参数抽取
权限校验
数据库实时状态
HITL 人工确认
业务 API 返回结果
```

例如用户长期记忆写着：

```text
默认供应商选择 A 供应商。
```

当用户说：

```text
帮我创建一笔苹果手机 A15 的订单，数量 100。
```

模型可以把“默认供应商 A”作为偏好参考，但不能直接绕过：

```text
用户是否有权限访问该供应商
供应商是否真实存在
该供应商是否支持当前商品
创建订单工具参数是否合法
执行前是否经过 HITL
```

因此推荐在 prompt 中明确写边界：

```text
以下是用户长期偏好，仅用于理解用户习惯。
不得将其视为权限、事实依据或已确认业务参数。
涉及工具调用时，仍需根据当前请求、权限、参数校验和 HITL 执行。
```

## 3. 长期记忆什么时候注入

长期记忆不应该散落在各个 LLM 调用点里手动拼接。

更合理的方式是由统一的 `PromptContextBuilder` 或 `ContextManager` 注入。

每次构造 LLM messages 时，统一从请求上下文中读取当前用户的 `ProfileMemorySnapshot`，然后放在稳定 prompt 前缀中。

推荐上下文顺序：

```text
System Prompt
+ Developer / Runtime Rules
+ User Profile Memory
+ Conversation Summary
+ Recent Messages
+ Current User Query
```

这样所有 LLM 节点都通过同一个入口拿上下文，避免出现：

```text
某个节点忘记注入长期记忆
不同节点注入格式不一致
不同节点读取到不同版本记忆
trace 无法复盘当时模型看到的长期记忆
```

## 4. Prompt 前缀是什么数据结构

Prompt 前缀不应该是全局变量。

它更合理的形态是一个“请求级不可变对象”。

示例：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileMemorySnapshot:
    user_id: str
    tenant_id: str
    memory_text: str
    memory_version: int
    memory_hash: str
    token_count: int
    updated_at: str


@dataclass(frozen=True)
class PromptPrefix:
    messages: list[dict]
    prompt_template_version: str
    memory_version: int
    memory_hash: str
```

请求进入后端时：

```text
读取 ProfileMemorySnapshot
-> PromptContextBuilder 渲染 PromptPrefix
-> 放入 RequestContext
-> 本次请求内部多个 LLM 节点复用同一个 RequestContext
```

示例：

```python
request_context.prompt_prefix = [
    {
        "role": "system",
        "content": SYSTEM_PROMPT,
    },
    {
        "role": "system",
        "content": (
            "以下是用户长期偏好，仅用于理解用户习惯，"
            "不能作为权限或已确认业务参数：\n"
            "默认供应商选择 A；库存低于 200 需要提醒。"
        ),
    },
]
```

后续不同 LLM 节点只是在这个前缀后面追加动态内容：

```text
工具选择：
prompt_prefix + tool_selection_instruction + current_query + candidate_tools

参数抽取：
prompt_prefix + param_extraction_instruction + selected_tool_schema + current_query

HITL 意图识别：
prompt_prefix + hitl_intent_instruction + pending_action + user_feedback

最终总结：
prompt_prefix + answer_instruction + tool_result
```

## 5. 为什么不能用 global 变量

不能把长期记忆或 prompt 前缀做成全局变量。

原因是：

```text
不同用户的长期记忆不同。
不同租户的数据和权限不同。
用户可能随时修改长期记忆。
多线程 / 多请求下 global 容易串用户。
线上 trace 需要知道本次请求使用的是哪一版 memory。
服务多实例部署时，单进程 global 不共享。
```

更正确的范围是：

```text
数据库：最终持久化存储
Redis：跨进程 / 跨实例缓存
RequestContext：单次请求内复用
PromptPrefix：本次请求内各 LLM 节点复用的稳定前缀
```

## 6. 是否每次 LLM 调用都查数据库

不建议每次 LLM 调用都查数据库。

一次用户请求内部可能触发多个 LLM 节点：

```text
工具选择
参数抽取
HITL 意图识别
最终总结
异常解释
```

如果每个节点都查询数据库，会带来：

```text
重复 I/O
延迟增加
数据库压力增加
同一次请求中可能读到不同版本记忆
trace 不好复盘
```

更合理的方式是：

```text
请求开始时读取一次长期记忆 snapshot
-> 放入 RequestContext
-> 本次请求内部所有 LLM 调用复用该 snapshot
```

低 QPS 或 demo 阶段，可以先做到：

```text
每个用户请求查一次 DB
本次请求内复用 RequestContext
```

正式线上或多实例部署时，推荐：

```text
DB 持久化 + Redis 缓存 + RequestContext 复用
```

## 7. Redis 缓存方案

Redis 适合这个长期记忆场景，因为它是：

```text
跨进程共享
跨服务实例共享
支持 TTL
支持显式失效
支持监控命中率
可以降低主数据库读压力
可以稳定 P95 / P99 延迟
```

长期记忆的数据特点：

```text
写少读多
用户主动编辑
内容变化不频繁
每次请求都可能需要注入 prompt
一次请求内部可能多次调用 LLM
```

推荐使用 cache-aside 模式：

```text
用户请求进入后端
-> ProfileMemoryService.get_snapshot(user_id, tenant_id)
-> 先查 Redis
-> Redis 命中：返回 memory snapshot
-> Redis 未命中：查数据库
-> 写入 Redis
-> 返回 memory snapshot
-> 放入 RequestContext
-> 本次请求内所有 LLM 节点复用
```

### 7.1 Redis key 设计

推荐 key：

```text
agent:profile_memory:{tenant_id}:{user_id}
```

value 为 JSON：

```json
{
  "tenant_id": "t001",
  "user_id": "u1001",
  "memory_text": "默认供应商选择 A；库存低于 200 需要提醒。",
  "memory_version": 7,
  "memory_hash": "sha256_xxx",
  "token_count": 42,
  "updated_at": "2026-06-22T10:30:00",
  "source": "user_explicit_profile"
}
```

如果希望更严格地通过 version 区分缓存，也可以使用：

```text
agent:profile_memory:{tenant_id}:{user_id}:{memory_version}
```

但这会增加旧版本 key 清理成本。

更常用的是固定 key + value 中带 version。

### 7.2 用户修改长期记忆时如何刷新

用户在前端修改长期记忆时：

```text
1. 后端校验内容长度、行数、敏感字段。
2. 计算 token_count。
3. 计算 memory_hash。
4. 数据库事务更新 memory_text、memory_version、memory_hash、updated_at。
5. 写穿 Redis 或删除 Redis key。
6. 后续请求读取新版本。
```

推荐写穿缓存：

```text
DB update success
-> Redis set new snapshot
```

如果担心 DB 和 Redis 不一致，也可以：

```text
DB update success
-> Redis delete key
-> 下次请求 miss 后从 DB 回填
```

删除更简单，写穿体验更快。

### 7.3 TTL 设置

长期记忆变化不频繁，可以设置较长 TTL：

```text
TTL = 6 小时 / 12 小时 / 24 小时
```

但不要只依赖 TTL。

用户编辑后应该主动刷新或删除缓存。

TTL 只是兜底，防止长期脏缓存。

### 7.4 Redis 的真正价值

如果一个请求只查一次，且数据库是主键查询，Redis 和 DB 的单次时延差距不一定非常大。

例如：

```text
Redis：0.2ms - 2ms
同机房数据库主键查询：3ms - 20ms
```

这个差距单看一次请求不夸张。

Redis 的价值不只是“快几毫秒”，而是：

```text
降低主数据库读压力
跨多实例共享缓存
统一缓存失效
降低 P95 / P99 尾延迟
数据库抖动时缓存可以兜住一部分读流量
便于监控 cache hit rate
便于和 memory_version 做一致性控制
```

## 8. LRU 缓存方案

LRU 是进程内缓存。

在 Python 中可以使用：

```python
from cachetools import TTLCache

profile_memory_cache = TTLCache(maxsize=10000, ttl=3600)
```

也可以使用 `functools.lru_cache`，但它更适合纯函数，不太适合需要显式失效和 TTL 的用户记忆缓存。

推荐结构：

```text
key = tenant_id + user_id
value = ProfileMemorySnapshot
淘汰策略 = 最近最少使用
TTL = 30 分钟到 2 小时
```

读取流程：

```text
请求进入后端
-> 查本进程 LRU
-> 命中：返回 snapshot
-> 未命中：查数据库
-> 写入 LRU
-> 放入 RequestContext
```

用户修改长期记忆时：

```text
更新 DB
-> 删除当前进程 LRU key
```

如果是单进程服务，这样可以工作。

如果是多进程 / 多实例部署，就会遇到问题：

```text
用户请求命中实例 A，更新了长期记忆。
实例 A 删除了本地 LRU。
实例 B 的 LRU 里仍然可能保留旧版本。
后续请求如果打到实例 B，就可能读到旧记忆。
```

因此 LRU 的优点是：

```text
实现简单
没有外部依赖
读取速度最快
适合本地开发
适合单进程 demo
适合作为 Redis 不可用时的 fallback
```

缺点是：

```text
多进程不共享
多实例不共享
服务重启缓存丢失
用户修改后难以全局失效
不好做统一命中率监控
不适合作为正式线上主缓存
```

## 9. Redis 和 LRU 如何选择

推荐结论：

```text
正式线上方案：Redis
本地开发 / 单进程 demo：LRU
稳妥工程方案：DB + Redis + RequestContext，LRU 只作为可选 fallback
```

选择 Redis 的原因：

```text
项目包装为企业 ERP Copilot。
企业系统一般是多用户、多实例部署。
长期记忆需要 user_id / tenant_id 隔离。
用户修改后需要统一失效。
trace 和监控需要统一记录。
Redis 更符合线上工程口径。
```

如果面试官质疑“DB 查询一次也不慢”，可以承认：

```text
低 QPS 下，单次请求只查一次 DB 是可以接受的。
Redis 不是绝对必要。
```

但进一步说明：

```text
当服务多实例部署、用户量上来、LLM 节点增多、P95/P99 延迟敏感时，Redis 可以降低数据库压力并稳定尾延迟。
```

## 10. 推荐加入工程的具体方案

如果要把这套机制加入当前工程，推荐做轻量版本，不要一开始做得太重。

### 10.1 数据表

新增用户长期记忆表：

```text
user_profile_memory
```

字段：

```text
id
tenant_id
user_id
memory_text
memory_version
memory_hash
token_count
created_at
updated_at
```

约束：

```text
unique(tenant_id, user_id)
```

### 10.2 服务类

新增：

```text
ProfileMemoryService
```

核心方法：

```python
class ProfileMemoryService:
    def get_snapshot(self, tenant_id: str, user_id: str) -> ProfileMemorySnapshot:
        ...

    def update_memory(self, tenant_id: str, user_id: str, memory_text: str) -> ProfileMemorySnapshot:
        ...
```

### 10.3 缓存策略

推荐：

```text
默认使用 Redis。
Redis 不可用时，可以降级查 DB。
本地开发可以使用 LRUCache。
```

伪代码：

```python
def get_snapshot(tenant_id: str, user_id: str) -> ProfileMemorySnapshot:
    cache_key = f"agent:profile_memory:{tenant_id}:{user_id}"

    cached = redis.get(cache_key)
    if cached:
        return ProfileMemorySnapshot.from_json(cached)

    row = db.get_profile_memory(tenant_id, user_id)
    snapshot = ProfileMemorySnapshot.from_row(row)
    redis.set(cache_key, snapshot.to_json(), ex=86400)
    return snapshot
```

### 10.4 RequestContext

新增请求级上下文：

```python
@dataclass
class RequestContext:
    tenant_id: str
    user_id: str
    session_id: str
    profile_memory: ProfileMemorySnapshot
    prompt_prefix: PromptPrefix
```

请求入口：

```text
收到用户请求
-> ProfileMemoryService.get_snapshot
-> PromptContextBuilder.build_prefix
-> 构造 RequestContext
-> 后续所有节点复用 RequestContext
```

### 10.5 PromptContextBuilder

示例：

```python
class PromptContextBuilder:
    def build_prefix(self, profile_memory: ProfileMemorySnapshot) -> PromptPrefix:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        if profile_memory.memory_text.strip():
            messages.append({
                "role": "system",
                "content": (
                    "以下是用户长期偏好，仅用于理解用户习惯，"
                    "不能作为权限或已确认业务参数：\n"
                    f"{profile_memory.memory_text}"
                ),
            })
        return PromptPrefix(
            messages=messages,
            prompt_template_version="profile_memory_prefix_v1",
            memory_version=profile_memory.memory_version,
            memory_hash=profile_memory.memory_hash,
        )
```

### 10.6 Trace 记录

每次请求 trace 中记录：

```text
user_id
tenant_id
session_id
memory_version
memory_hash
profile_memory_token_count
prompt_template_version
profile_memory_cache_hit
```

这样线上复盘时可以知道：

```text
本次请求是否注入了长期记忆
注入的是哪一版长期记忆
长期记忆是否来自缓存
长期记忆是否过长
```

## 11. 与 Prompt Caching 的关系

长期记忆一般不频繁变化，适合放在 prompt 的稳定前缀区。

推荐顺序：

```text
System Prompt
+ Long-term Profile Memory
+ Dynamic Conversation Context
+ Current Task
```

如果模型服务支持 Prompt Caching 或 KV Cache，稳定前缀更容易命中缓存。

注意：

```text
稳定内容要放在前面。
动态内容不要插在长期记忆前面。
长期记忆频繁修改会降低缓存命中。
不同用户的长期记忆不同，跨用户无法共享缓存。
```

Prompt Caching 不是替代 Redis 的缓存。

两者缓存层级不同：

```text
Redis：缓存业务侧长期记忆数据，减少数据库读取。
Prompt Caching：缓存模型侧前缀计算，减少模型推理成本。
RequestContext：缓存本次请求中的 prompt prefix，避免重复构造。
```

## 12. 面试表达

可以这样回答：

> 我们的长期记忆不是 global 变量，也不是每个 LLM 节点都去查数据库。它是用户显式维护的 Profile Memory，数据库做最终存储。请求开始时，后端通过 ProfileMemoryService 获取当前用户的 memory snapshot，然后 PromptContextBuilder 把它渲染成稳定的 PromptPrefix，放入 RequestContext。本次请求里的工具选择、参数抽取、HITL 判断和最终总结都复用这个 RequestContext。

继续补充：

> 低 QPS 阶段，每个用户请求查一次数据库其实也可以，因为这是按 user_id 的主键查询。但如果包装成线上系统，我会选 Redis 做主缓存，而不是只靠本地 LRU。Redis 能跨多进程和多实例共享缓存，支持统一失效、TTL、命中率监控，也能降低数据库压力和稳定 P95/P99 延迟。LRU 更适合本地开发或单进程 demo，因为它不共享，用户修改长期记忆后很难让所有实例同时失效。

最后强调边界：

> 长期记忆只表达用户偏好，不是权限和业务事实来源。即使长期记忆里写了默认供应商或库存阈值，工具调用时仍然要经过当前 query、参数校验、权限校验和 HITL。Trace 里会记录 memory_version 和 memory_hash，方便复盘模型当时到底看到了哪一版长期记忆。
