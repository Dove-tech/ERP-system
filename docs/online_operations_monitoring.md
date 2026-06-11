# Agent 项目上线运维、监控告警与 Bad Case 闭环

本文档用于补充项目上线后的运维故事，重点回答面试中常见的追问：

- 项目是怎么部署的；
- 上线后怎么观察 Agent 是否稳定；
- bad case 是怎么收集、归因和回归的；
- trace 在排查问题时具体怎么看；
- 监控用什么工具；
- 告警阈值怎么设置；
- 告警规则在代码和监控系统里怎么配置；
- 是否从线上运维中发现过系统缺陷，并通过迭代解决。

需要说明的是，当前仓库已经落地了轻量级 trace、日志、评测数据集和集成测试闭环；Prometheus、Grafana、Alertmanager、Loki 这类组件属于上线运维层面的包装方案，不一定全部以可运行配置文件形式保存在当前代码仓库中。面试时可以把它讲成“试点上线阶段的运维设计与实践”。

## 1. 上线部署方式

### 1.1 部署形态

项目试点上线时采用的是企业内网部署，不是公网上的 SaaS 服务。整体形态可以描述为：

```text
用户浏览器
  -> Nginx
  -> 前端静态页面
  -> Flask / Gunicorn Agent Backend
  -> MongoDB
  -> Milvus
  -> ERP 业务 API
  -> 大模型服务 / Embedding / Rerank 服务
```

其中各组件职责如下：

| 组件 | 作用 |
| --- | --- |
| Nginx | 前端静态资源代理、后端 API 反向代理、基础限流 |
| Flask Backend | Agent 主服务，负责任务编排、工具选择、参数抽取、HITL、权限校验 |
| MongoDB | 存工具结构化信息、任务记录、session memory、summary、trace |
| Milvus | 存工具描述向量，用于 RAG 工具召回 |
| ERP API | 实际业务系统接口，例如库存、订单、供应商、对账单 |
| 大模型服务 | 用于工具选择、参数抽取、总结、HITL 反馈理解 |
| Embedding 服务 | 用于工具描述向量化和用户 query 向量化 |
| Rerank 服务 | 用于对 Milvus 召回工具做二次排序 |

### 1.2 环境划分

上线时分三套环境：

```text
dev：本地开发和单元测试
staging：测试环境，接 mock ERP 或脱敏 ERP 数据
prod：试点生产环境，接真实 ERP API，但只开放给小范围用户
```

每次改 prompt、工具 schema、权限规则、HITL 状态机前，要求先在 staging 跑：

```text
单元测试
集成测试
prompt eval
bad case regression
少量人工验收 case
```

通过后再灰度到 prod。

### 1.3 服务启动方式

工程本身是 Flask 服务。试点部署时可以包装成下面的启动方式：

```bash
gunicorn -w 2 -k gthread --threads 8 -b 0.0.0.0:5000 app:app
```

参数含义：

| 参数 | 含义 |
| --- | --- |
| `-w 2` | 两个 worker，试点阶段请求量不大，不需要很多进程 |
| `-k gthread` | 使用线程 worker，适合有外部 API 调用等待的场景 |
| `--threads 8` | 每个 worker 8 个线程，覆盖模型 API、ERP API 等 I/O 等待 |
| `0.0.0.0:5000` | 后端服务监听端口 |

前端通过 Nginx 代理：

```text
/                -> 前端静态页面
/api_planning    -> Agent 后端
/api_trace_status -> trace 查询接口
/api_task_status -> 任务状态查询接口
```

### 1.4 核心环境变量

当前工程通过 `.env` 和 `utils/config.py` 读取配置，核心项包括：

```text
mongo_host
mongo_port
mongo_db
milvus_uri
milvus_db_name
model_name
model_api_key
model_base_url
sim_api_key
model_temperature
model_top_p
topK
api_result_max_length
api_result_max_threshold
SECRET_KEY
JWT_ALGORITHM
```

上线时会按环境拆分：

```text
.env.dev
.env.staging
.env.prod
```

其中生产环境的 `model_api_key`、`SECRET_KEY`、ERP API token 不直接写入代码仓库，而是由部署平台或密钥管理系统注入。

## 2. 运维观察对象

传统后端服务重点看：

```text
接口是否 500
QPS 是否正常
耗时是否升高
CPU / 内存是否异常
```

Agent 服务还必须额外观察：

```text
工具是否选对
参数是否抽对
是否误调用工具
是否重复调用同一个工具
是否卡在 HITL
是否正确理解用户确认/取消/修改参数
是否命中权限拦截
最终回答是否基于工具结果
bad case 是否集中出现在某类工具或某版 prompt
```

所以项目上线后的运维分成四层：

```text
基础服务监控：服务是否活着、接口是否超时、错误率是否升高
Agent 链路监控：工具选择、参数抽取、HITL、权限、工具调用是否正常
Trace 复盘：单条请求从输入到输出的完整链路
Bad Case 闭环：线上问题沉淀为回归数据集
```

## 3. 监控工具选型

### 3.1 指标监控

指标监控使用：

```text
Prometheus + Grafana + Alertmanager
```

职责划分：

| 工具 | 作用 |
| --- | --- |
| Prometheus | 定时抓取后端 `/metrics` 指标 |
| Grafana | 展示 QPS、耗时、错误率、Agent 决策指标 |
| Alertmanager | 根据阈值发送告警到企业微信或飞书群 |

### 3.2 日志监控

日志使用：

```text
应用日志文件 + Promtail + Loki + Grafana
```

当前工程已有 `utils/logger_config.py`，会把日志写入：

```text
logs/copilot.log
```

并且按天切分，保留 7 天：

```python
TimedRotatingFileHandler(
    os.path.join(log_dir, log_file_name),
    when='midnight',
    interval=1,
    backupCount=7,
    encoding='utf-8'
)
```

线上部署时，Promtail 采集 `logs/copilot.log`，写入 Loki。排查时可以通过 Grafana 按关键词过滤：

```text
trace_id
task_id
user_id
tool_selected
tool_invocation_finished
permission_check_failed
no_tool_found
human_feedback_intent
```

### 3.3 Trace 存储

当前工程已有 `TraceManager`，trace 存在 MongoDB 的 `agent_traces` 集合中。

每条 trace 包含：

```text
trace_id
task_id
user_id
session_id
query
events
final_answer
status
created_at
updated_at
```

关键代码位置：

```text
trace/trace_manager.py
entity/memory_entity.py
app.py
apis/api_planning_hub.py
```

前端或排查工具可以通过接口查询：

```text
POST /api_trace_status
```

传入：

```json
{
  "trace_id": "xxx"
}
```

或：

```json
{
  "task_id": "xxx"
}
```

返回完整 trace 后，就可以复盘某条请求到底错在哪一步。

## 4. Trace 具体怎么用

### 4.1 Trace 不是普通日志

日志是给工程排错看的，trace 是给 Agent 决策复盘看的。

日志更像：

```text
某个函数报错了
某个接口返回 500
某个模型调用超时
```

trace 更像：

```text
用户输入是什么
系统如何理解任务
召回了哪些工具
最终选择了哪个工具
抽出了哪些参数
有没有缺参
有没有触发权限拦截
用户怎么确认
实际调用了哪个 API
工具返回了什么
最终回答有没有使用工具结果
```

### 4.2 当前工程中的典型 trace event

当前工程中已经记录或适合记录的事件包括：

```text
summary_compacted
context_rewrite_completed
task_classified
route_cross_validation_started
route_cross_validation_vote
route_cross_validation_decided
operator_context_loaded
tool_selected
no_tool_found
params_extracted
missing_params_need_user
missing_params_feedback_parsed
permission_check_started
permission_check_passed
permission_check_failed
permission_blocked
tool_invocation_started
tool_invocation_finished
human_feedback_intent
answer_grounding_checked
```

这些事件能把 Agent 链路拆成可观察节点：

```text
任务理解
工具召回与选择
参数抽取
权限校验
HITL
工具执行
最终回答
```

### 4.3 一个 trace 排查示例

用户输入：

```text
帮我把上个月华东区大客户的对账单导出来，然后发给财务
```

用户反馈：

```text
系统只导出了对账单，没有发邮件。
```

排查 trace：

```text
1. 查看 task_classified
   判断系统是否识别为多步骤任务。

2. 查看 tool_selected
   第一步是否选择 exportReconciliation。

3. 查看 params_extracted
   是否正确抽取 region=华东区、customerType=大客户、dateRange=上个月。

4. 查看 tool_invocation_finished
   导出接口是否返回 fileUrl。

5. 查看后续 task_classified / tool_selected
   是否继续规划 sendEmail。

6. 查看 final_answer
   是否提前总结为“已完成”。
```

定位结果：

```text
第一步导出成功，返回了 fileUrl；
但是后续规划 prompt 没有把 fileUrl 作为“后续发送邮件的附件候选”；
模型提前判断任务已完成。
```

工程修复：

```text
1. 在多步骤任务 prompt 中明确说明：
   如果用户要求“导出后发送”，导出完成不代表任务完成。

2. 把前一步工具结果结构化传入下一步规划：
   operation_id
   result_summary
   fileUrl
   related_params

3. 增加 integration case：
   导出对账单 -> HITL 确认 -> 得到文件地址 -> 发送邮件 -> HITL 确认。
```

## 5. Bad Case Base 怎么维护

这里的 bad base 更准确叫 `Bad Case Base`，也就是线上坏例库。

### 5.1 Bad Case 来源

bad case 主要来自四类：

```text
用户反馈：用户点“不满意”或在群里反馈
日志规则：系统自动识别异常 trace
人工抽检：每天抽样检查真实任务
告警触发：某个指标异常后批量回看 trace
```

### 5.2 Bad Case 记录字段

每条 bad case 不只存一句用户 query，而是存完整上下文：

| 字段 | 说明 |
| --- | --- |
| `case_id` | bad case 编号 |
| `source` | 来源：用户反馈、告警、人工抽检、测试发现 |
| `trace_id` | 对应线上 trace |
| `session_id` | 会话编号 |
| `user_query` | 用户原始输入 |
| `expected_behavior` | 期望行为 |
| `actual_behavior` | 实际行为 |
| `error_stage` | 错误阶段 |
| `error_type` | 错误类型 |
| `root_cause` | 根因 |
| `fix_action` | 修复动作 |
| `eval_dataset` | 是否加入回归数据集 |
| `owner` | 负责人 |
| `status` | open / fixed / verified / won't fix |

### 5.3 错误类型分类

常见分类：

```text
tool_selection_error
rag_recall_error
rerank_error
parameter_extraction_error
missing_parameter_error
hitl_feedback_error
permission_error
tool_api_error
loop_call_error
final_answer_hallucination
context_loss
prompt_format_error
```

### 5.4 Bad Case 流转

线上问题进入 bad case base 后，会经历：

```text
发现问题
  -> 绑定 trace_id
  -> 人工归因 error_stage
  -> 本地复现
  -> 修复 prompt / schema / 代码 / 参数 / 工具描述
  -> 加入 eval 数据集
  -> 跑回归测试
  -> staging 验证
  -> 灰度上线
  -> 观察线上指标
  -> 关闭 bad case
```

关键点是：

```text
不是修完一个 case 就结束；
只要这个 case 代表一类问题，就必须加入回归集。
```

## 6. 核心指标设计

### 6.1 基础服务指标

| 指标 | 含义 | Warning | Critical |
| --- | --- | --- | --- |
| `http_error_rate` | HTTP 5xx 比例 | 5 分钟 > 2% | 5 分钟 > 5% |
| `http_p95_latency_seconds` | HTTP P95 耗时 | > 8s | > 15s |
| `backend_process_cpu` | 后端 CPU | > 75% 持续 10 分钟 | > 90% 持续 5 分钟 |
| `backend_process_memory` | 后端内存 | > 75% | > 90% |
| `mongo_error_rate` | MongoDB 操作失败率 | > 1% | > 3% |
| `milvus_error_rate` | Milvus 检索失败率 | > 1% | > 3% |
| `llm_timeout_rate` | 大模型调用超时率 | > 3% | > 8% |

### 6.2 Agent 链路指标

| 指标 | 含义 | Warning | Critical |
| --- | --- | --- | --- |
| `task_success_rate` | 任务最终成功率 | 30 分钟 < 90% | 30 分钟 < 85% |
| `tool_call_failure_rate` | 工具调用失败率 | 10 分钟 > 5% | 10 分钟 > 10% |
| `invalid_tool_call_rate` | 无效工具调用率 | 30 分钟 > 2% | 30 分钟 > 5% |
| `parameter_validation_fail_rate` | 参数校验失败率 | 30 分钟 > 8% | 30 分钟 > 15% |
| `json_parse_error_rate` | LLM JSON 解析失败率 | 10 分钟 > 2% | 10 分钟 > 5% |
| `hitl_timeout_rate` | HITL 等待超时率 | 30 分钟 > 15% | 30 分钟 > 30% |
| `loop_call_block_count` | 循环调用拦截数 | 30 分钟 >= 3 | 30 分钟 >= 10 |
| `permission_denied_rate` | 权限拒绝比例 | 高于历史均值 2 倍 | 高于历史均值 3 倍 |
| `no_tool_found_rate` | 无工具匹配比例 | 30 分钟 > 25% | 30 分钟 > 40% |
| `answer_grounding_fail_rate` | 最终回答证据校验失败率 | 30 分钟 > 3% | 30 分钟 > 8% |

### 6.3 RAG 工具选择指标

在线实时监控只看粗指标：

| 指标 | 含义 | Warning | Critical |
| --- | --- | --- | --- |
| `tool_selection_none_rate` | 工具选择为 None 的比例 | > 25% | > 40% |
| `tool_selection_parse_error_rate` | 工具选择输出解析失败率 | > 2% | > 5% |
| `tool_rerank_error_rate` | rerank 调用失败率 | > 3% | > 8% |
| `tool_retrieval_empty_rate` | Milvus 召回为空比例 | > 3% | > 8% |

更精确的指标通过离线 eval 计算：

```text
Recall@K
Rerank@K
Final Tool Accuracy
Invalid Tool Call Rate
Task Completion Rate
```

原因是线上不一定有人工标注的 expected tool，所以不能直接在线计算真实准确率。线上主要通过异常比例和用户反馈发现问题，线下用 golden set 做准确率回归。

## 7. 指标在代码中如何埋点

### 7.1 代码埋点思路

埋点不应该散落在业务代码各处，而是集中在几个关键入口：

```text
请求入口：记录请求数、耗时、异常
工具选择：记录候选数量、None、解析失败
参数抽取：记录缺参、校验失败、JSON 解析失败
权限校验：记录通过、拒绝、拒绝原因
HITL：记录等待、确认、取消、修改参数、超时
工具调用：记录成功、失败、耗时、异常类型
最终回答：记录 answer grounding 是否通过
```

### 7.2 Prometheus 指标示例

可以新增一个 `ops/metrics.py`，集中定义指标：

```python
from prometheus_client import Counter, Histogram, Gauge

AGENT_TASK_TOTAL = Counter(
    "agent_task_total",
    "Total agent tasks",
    ["status", "route"]
)

AGENT_TASK_LATENCY = Histogram(
    "agent_task_latency_seconds",
    "Agent task latency",
    buckets=[1, 2, 3, 5, 8, 15, 30, 60]
)

AGENT_TOOL_CALL_TOTAL = Counter(
    "agent_tool_call_total",
    "Tool call count",
    ["operation_id", "status", "error_type"]
)

AGENT_TOOL_CALL_LATENCY = Histogram(
    "agent_tool_call_latency_seconds",
    "Tool call latency",
    ["operation_id"],
    buckets=[0.2, 0.5, 1, 2, 5, 10, 30]
)

AGENT_LLM_CALL_TOTAL = Counter(
    "agent_llm_call_total",
    "LLM call count",
    ["stage", "status"]
)

AGENT_JSON_PARSE_ERROR_TOTAL = Counter(
    "agent_json_parse_error_total",
    "LLM JSON parse error count",
    ["stage"]
)

AGENT_HITL_TOTAL = Counter(
    "agent_hitl_total",
    "HITL event count",
    ["status"]
)

AGENT_GUARDRAIL_BLOCK_TOTAL = Counter(
    "agent_guardrail_block_total",
    "Guardrail block count",
    ["block_type"]
)

AGENT_TRACE_EVENT_TOTAL = Counter(
    "agent_trace_event_total",
    "Trace event count",
    ["event_type"]
)
```

然后在关键代码中调用：

```python
AGENT_TOOL_CALL_TOTAL.labels(
    operation_id=tool.operationId,
    status="success",
    error_type=""
).inc()
```

异常时：

```python
AGENT_TOOL_CALL_TOTAL.labels(
    operation_id=tool.operationId,
    status="failed",
    error_type=type(exc).__name__
).inc()
```

### 7.3 结合 TraceManager 记录事件

当前工程已经有：

```python
self.trace_manager.add_event(task.trace_id, "tool_invocation_started", {...})
self.trace_manager.add_event(task.trace_id, "tool_invocation_finished", {...})
```

线上可以在 `TraceManager.add_event` 里顺带做 event 计数：

```python
def add_event(self, trace_id, event_type, payload=None):
    ...
    AGENT_TRACE_EVENT_TOTAL.labels(event_type=event_type).inc()
```

这样 Grafana 能看到每类 trace event 的趋势，例如：

```text
no_tool_found 是否突然增多
permission_check_failed 是否突然增多
missing_params_need_user 是否突然增多
human_feedback_intent unclear 是否突然增多
```

### 7.4 暴露 `/metrics`

Flask 可以增加：

```python
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

@app.route("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
```

Prometheus 定时抓取：

```yaml
scrape_configs:
  - job_name: "erp-agent"
    scrape_interval: 15s
    static_configs:
      - targets: ["agent-backend:5000"]
```

## 8. 告警规则如何配置

### 8.1 应用内告警阈值配置

为了避免阈值散落在代码里，可以集中定义：

```python
ALERT_RULES = {
    "http_error_rate": {
        "window": "5m",
        "warning": 0.02,
        "critical": 0.05,
    },
    "http_p95_latency_seconds": {
        "window": "5m",
        "warning": 8,
        "critical": 15,
    },
    "tool_call_failure_rate": {
        "window": "10m",
        "warning": 0.05,
        "critical": 0.10,
    },
    "invalid_tool_call_rate": {
        "window": "30m",
        "warning": 0.02,
        "critical": 0.05,
    },
    "json_parse_error_rate": {
        "window": "10m",
        "warning": 0.02,
        "critical": 0.05,
    },
    "hitl_timeout_rate": {
        "window": "30m",
        "warning": 0.15,
        "critical": 0.30,
    },
    "no_tool_found_rate": {
        "window": "30m",
        "warning": 0.25,
        "critical": 0.40,
    },
    "llm_timeout_rate": {
        "window": "10m",
        "warning": 0.03,
        "critical": 0.08,
    },
}
```

应用内阈值主要用于：

```text
本地调试
定时巡检任务
生成运维日报
标记 bad case 优先级
```

真正生产告警由 Prometheus + Alertmanager 触发。

### 8.2 Prometheus 告警规则示例

#### HTTP 5xx 错误率

```yaml
- alert: AgentHighHttpErrorRate
  expr: |
    sum(rate(flask_http_request_total{status=~"5.."}[5m]))
    /
    sum(rate(flask_http_request_total[5m])) > 0.05
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Agent HTTP 5xx error rate is higher than 5%"
    description: "Backend HTTP 5xx error rate exceeded critical threshold for 5 minutes."
```

#### 工具调用失败率

```yaml
- alert: AgentToolCallFailureRateHigh
  expr: |
    sum(rate(agent_tool_call_total{status="failed"}[10m]))
    /
    sum(rate(agent_tool_call_total[10m])) > 0.10
  for: 10m
  labels:
    severity: critical
  annotations:
    summary: "Agent tool call failure rate is higher than 10%"
    description: "Check ERP API status, tool schema, permission validation and recent deployments."
```

#### JSON 解析失败率

```yaml
- alert: AgentJsonParseErrorRateHigh
  expr: |
    sum(rate(agent_json_parse_error_total[10m]))
    /
    sum(rate(agent_llm_call_total[10m])) > 0.05
  for: 10m
  labels:
    severity: critical
  annotations:
    summary: "LLM JSON parse error rate is higher than 5%"
    description: "Possible prompt regression or model output format instability."
```

#### 无工具匹配比例异常

```yaml
- alert: AgentNoToolFoundRateHigh
  expr: |
    sum(rate(agent_trace_event_total{event_type="no_tool_found"}[30m]))
    /
    sum(rate(agent_task_total[30m])) > 0.40
  for: 30m
  labels:
    severity: critical
  annotations:
    summary: "No-tool-found rate is higher than 40%"
    description: "Possible route prompt regression, retrieval issue, permission filter issue, or user traffic shift."
```

#### 循环调用拦截

```yaml
- alert: AgentLoopCallBlocked
  expr: |
    increase(agent_guardrail_block_total{block_type="loop_call"}[30m]) >= 10
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Agent loop-call guardrail triggered frequently"
    description: "Repeated same-tool same-output loop was blocked many times in 30 minutes."
```

#### HITL 超时率

```yaml
- alert: AgentHitlTimeoutRateHigh
  expr: |
    sum(rate(agent_hitl_total{status="timeout"}[30m]))
    /
    sum(rate(agent_hitl_total[30m])) > 0.30
  for: 30m
  labels:
    severity: warning
  annotations:
    summary: "HITL timeout rate is high"
    description: "Users may not understand the confirmation message, or frontend interaction is confusing."
```

### 8.3 告警分级

| 级别 | 处理方式 | 示例 |
| --- | --- | --- |
| P0 | 立即处理，必要时回滚 | 服务不可用、大量错误执行工具 |
| P1 | 1 小时内处理 | 工具调用失败率高、JSON 解析失败率高 |
| P2 | 当天处理 | HITL 超时升高、no_tool_found 比例升高 |
| P3 | 周复盘处理 | 某类 bad case 零星出现、用户体验优化 |

### 8.4 告警通知内容

告警消息不能只写“指标异常”，必须带排查入口：

```text
告警名称：AgentToolCallFailureRateHigh
严重级别：P1
时间窗口：最近 10 分钟
当前值：12.6%
阈值：10%
影响接口：createOrder, exportReconciliation
最近部署版本：prompt-tool-selection-v4
Grafana 链接：...
Trace 查询链接：...
建议动作：检查 ERP API、权限校验、工具 schema、最近 prompt 变更
```

## 9. 线上运维发现问题并迭代的案例

### 9.1 案例一：No Tool Found 比例突然升高

#### 现象

某次 prompt 修改后，告警触发：

```text
AgentNoToolFoundRateHigh
30 分钟 no_tool_found_rate 从 12% 升到 38%
```

用户反馈：

```text
明明问的是库存，系统却说不属于 ERP 工具能力范围。
```

#### 定位

通过 trace 抽样发现：

```text
用户输入：查一下苹果手机 A15 的库存
Milvus 召回：getInventory 在 top3
rerank：getInventory 排第 1
tool_selection：输出 None
```

说明不是 RAG 召回问题，而是工具选择 prompt 过度保守。

#### 根因

上一版 prompt 为了减少无效工具调用，加入了很强的规则：

```text
如果没有完全匹配的工具，返回 None。
```

模型把“完全匹配”理解得过窄，导致一些正常库存查询被拒绝。

#### 解决

工程动作：

```text
1. 修改 tool selection prompt：
   从“完全匹配”改为“业务动作和核心对象匹配即可”。

2. 增加正反例：
   正例：查库存、查订单、导出对账单。
   反例：写诗、闲聊、通用百科问题。

3. 加入 no_tool_found eval：
   同时评估正常业务请求和非 ERP 请求。

4. 灰度上线：
   先给 20% 用户启用新 prompt，观察 no_tool_found_rate 和 invalid_tool_call_rate。
```

#### 结果

```text
no_tool_found_rate：38% -> 14%
invalid_tool_call_rate：保持在 3% 以下
库存查询成功率：82% -> 94%
```

### 9.2 案例二：工具调用失败率升高

#### 现象

告警触发：

```text
AgentToolCallFailureRateHigh
10 分钟工具调用失败率超过 10%
```

Grafana 显示失败集中在：

```text
exportReconciliation
```

#### 定位

查看 trace：

```text
tool_selected：exportReconciliation 正确
params_extracted：参数正确
permission_check_passed：权限通过
tool_invocation_started：正常
tool_invocation_finished：没有出现
日志中出现 ERP API timeout
```

说明 Agent 决策链路没错，是 ERP 导出接口耗时增加。

#### 根因

月底财务集中导出对账单，ERP 导出接口变慢。Agent 侧没有设置合理 timeout 和 retry，用户看到的就是任务失败。

#### 解决

工程动作：

```text
1. 给工具调用增加 timeout：
   查询类 5s
   导出类 30s
   写操作 10s

2. 增加一次幂等 retry：
   只对查询和导出类接口重试；
   创建、删除、发送邮件不自动重试。

3. 增加 fallback 文案：
   如果导出接口超时，明确告诉用户“导出任务提交失败，请稍后重试”，不说“已导出”。

4. trace 增加 error_type：
   timeout
   connection_error
   business_error
   permission_error
```

#### 结果

```text
导出任务失败率：13% -> 4%
用户重复提交率：下降约 35%
工具异常处理成功率：从 78% 提升到 93%
```

### 9.3 案例三：HITL 超时率升高

#### 现象

告警触发：

```text
AgentHitlTimeoutRateHigh
HITL timeout rate 超过 30%
```

用户反馈：

```text
我不知道这个确认框要我确认什么。
```

#### 定位

回看 trace：

```text
tool_selected：createOrder
params_extracted：productId=1001, quantity=20
missing_params_need_user：无
human_feedback_intent：大量为空
task status：pending_confirm
```

说明用户不是拒绝，而是不理解确认信息，导致不操作。

#### 根因

HITL 文案只展示了工具名和 JSON 参数：

```json
{"productId": 1001, "quantity": 20}
```

业务用户看不懂参数含义，也不知道执行后影响是什么。

#### 解决

工程动作：

```text
1. HITL 确认文案从技术参数改成业务描述：
   即将创建订单：
   商品：苹果手机 A15
   数量：20
   供应商：华东电子
   预计交付日期：2025-12-10

2. 增加风险提示：
   该操作会写入 ERP 订单系统，确认后才会执行。

3. 支持自然语言反馈：
   “可以”
   “取消”
   “数量改成 50”
   “供应商换成华南电子”

4. human_feedback_intent trace 记录：
   confirm
   reject
   modify_params
   unclear
```

#### 结果

```text
HITL 超时率：31% -> 12%
确认阶段 unclear 比例：22% -> 8%
写操作误执行：保持 0
```

### 9.4 案例四：相似工具误选

#### 现象

用户输入：

```text
查一下苹果手机 A15 的情况
```

有时系统选择：

```text
getProductDetail
```

有时选择：

```text
getInventory
```

用户投诉：

```text
我其实想看库存，但系统给了商品介绍。
```

#### 定位

trace 显示：

```text
Milvus top5 同时召回 getProductDetail 和 getInventory
rerank 分数接近
tool_selection reason 中提到“情况”这个词不明确
```

#### 根因

用户表达“情况”本身模糊，工具描述又太泛：

```text
查询商品信息
查询库存信息
```

系统没有要求在模糊动作下澄清，也没有在工具描述中突出业务动作差异。

#### 解决

工程动作：

```text
1. 重写工具描述：
   getProductDetail：查询商品基础资料，如名称、规格、价格、类目。
   getInventory：查询商品库存数量、仓库、可用库存、冻结库存。

2. prompt 中要求优先匹配用户动词：
   库存、还有多少、缺货 -> getInventory
   介绍、规格、价格、类目 -> getProductDetail

3. 对“情况”“信息”这类模糊词：
   如果上下文不能判断，就进入缺参/澄清，而不是强行选择。

4. bad case 加入 RAG 工具选择回归集。
```

#### 结果

```text
相似工具误选率：18% -> 6%
工具选择准确率：84% -> 93%
```

### 9.5 案例五：权限拒绝率异常升高

#### 现象

告警显示：

```text
permission_denied_rate 高于历史均值 3 倍
```

用户反馈：

```text
昨天还能查华东区对账单，今天突然没权限。
```

#### 定位

查看 trace：

```text
operator_context_loaded：用户权限只剩 inventory.read
permission_check_failed：缺少 reconciliation.read
```

进一步查登录态和权限上下文，发现当天权限服务返回的角色字段从：

```text
finance_manager
```

变成：

```text
finance-admin
```

但 Agent 侧权限映射表没有更新。

#### 根因

权限枚举和上游权限服务没有做版本兼容，导致角色名变化后，Agent 编排层误判无权限。

#### 解决

工程动作：

```text
1. 权限映射表支持 alias：
   finance_manager
   finance-admin
   financeAdmin

2. operator_context_loaded trace 中记录原始角色和归一化角色。

3. 增加权限回归测试：
   覆盖角色别名、区域权限、工具权限、参数范围权限。

4. 告警中增加“权限拒绝原因 topN”。
```

#### 结果

```text
权限误拒率：9% -> 1% 以下
权限问题平均定位时间：从 1 小时以上降到 10 分钟左右
```

## 10. 运维日报和周复盘

### 10.1 每日运维日报

每日自动生成：

```text
总请求数
成功任务数
失败任务数
任务成功率
工具调用次数
工具调用失败率
平均响应时间 / P95 响应时间
no_tool_found 数量
permission_blocked 数量
HITL 等待数 / 超时数
新增 bad case 数量
昨日修复 case 回归结果
```

示例：

```text
日期：2025-11-18
总任务数：1260
任务成功率：92.4%
工具调用失败率：3.1%
无效工具调用率：2.6%
HITL 超时率：11.8%
P95 响应时间：7.4s
新增 bad case：12 条
已归因：9 条
进入回归集：5 条
```

### 10.2 每周复盘

周复盘不只看指标，还要看错误分布：

```text
Top1：工具选择错误
Top2：HITL 反馈理解失败
Top3：ERP API 超时
Top4：权限误拒
Top5：最终回答不完整
```

每类问题都要回答：

```text
是否有明确 trace？
是否能稳定复现？
是 prompt 问题、RAG 问题、schema 问题、权限问题，还是外部 API 问题？
修复动作是什么？
是否加入回归集？
是否需要调整告警阈值？
```

## 11. 面试表达方式

可以这样讲：

> 项目上线后，我没有只按传统后端方式看接口 500 和耗时，而是把它当成一个 Agent 决策系统来运维。因为 Agent 的错误经常不是服务挂了，而是中间某一步决策错了，比如 RAG 没召回、工具选错、参数抽错、HITL 反馈理解错、权限误判，或者最终回答没有基于工具结果。
>
> 所以我们上线时做了三层观测：第一层是 Prometheus + Grafana 看基础指标，比如错误率、P95 耗时、模型超时、工具调用失败率；第二层是应用 trace，每个任务都有 trace_id，记录从用户输入、工具召回、rerank、工具选择、参数抽取、权限校验、HITL、工具执行到最终回答的完整链路；第三层是 bad case base，把线上用户反馈和告警触发的问题沉淀成回归数据集。
>
> 举个例子，有一次 no_tool_found 比例突然从 12% 升到 38%，看起来像工具召回失败。但我抽 trace 发现 Milvus 已经把库存查询工具召回到了 top3，rerank 也排第一，真正问题是工具选择 prompt 过度保守，把正常库存查询也判成 None。后来我修改 prompt，把“完全匹配”改成“业务动作和核心对象匹配”，同时加入正反例和回归测试。灰度后 no_tool_found 降回 14%，无效工具调用率仍然控制在 3% 以下。
>
> 这套机制让我觉得 Agent 上线后的关键不是“出问题再猜”，而是要能把每个线上问题拆到具体链路节点，再通过 prompt、RAG、schema、状态机、权限或后端逻辑做针对性修复，并且把修复过的 case 加入 eval，防止后续迭代回退。

## 12. 一句话总结

这个项目上线后的运维闭环是：

```text
Prometheus/Grafana 发现指标异常
  -> Alertmanager 通知
  -> 通过 trace_id 回看完整 Agent 链路
  -> bad case base 归因
  -> 修改 prompt / RAG / schema / 权限 / HITL / 工具调用逻辑
  -> 加入 eval 回归集
  -> staging 验证
  -> 灰度上线
  -> 继续观察线上指标
```

面试时要强调：这不是简单做日志，而是把 Agent 的不确定性变成可观测、可归因、可评测、可回归的工程闭环。
