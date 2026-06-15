# ERP 工具执行型 Multi-Agent 方案（无 RAG 版本）

生成日期：2026-06-15  
适用项目：`ERP-system`  
当前定位：只做工具执行，不包装知识库问答和 RAG。

## 1. 一句话定位

> 这是一个面向 ERP / 供应链运维场景的工具执行型 Multi-Agent Copilot。系统不做知识库问答，而是专注把自然语言请求稳定转换为受权限、参数校验、风险控制、人工确认和审计保护的 ERP API 调用流程。

面试时可以强调：

> 这版设计暂时不包装 RAG，因为 ERP 工具执行已经足够复杂。重点不是让模型“回答得像”，而是让模型在真实业务 API 调用前后做到可控、可审计、可恢复，尤其是写操作不能直接放给模型自由执行。

## 2. 目标和非目标

### 2.1 目标

- 支持自然语言触发 ERP 工具调用。
- 支持单工具查询、多工具链路、写操作确认。
- 支持缺参澄清、参数修改、取消、确认。
- 支持用户、角色、租户、工具、参数范围级权限校验。
- 支持高风险工具的 guardrail 和人工确认。
- 支持 LangGraph 状态编排，能解释每一步为什么发生。
- 支持分阶段评测和端到端回归。

### 2.2 非目标

当前阶段不做：

- 通用知识库问答。
- RAG 文档检索。
- Web Search。
- Neo4j 知识图谱。
- 多模态文档理解。
- 大规模开放式自主 Agent。

不要在面试中说：

- “项目已经完整支持企业知识库问答。”
- “系统已经实现 Hybrid RAG。”
- “Agent 可以自己任意探索和调用所有 API。”

可以说：

- “当前聚焦工具执行链路，RAG 是后续可选增强，不是本阶段核心。”
- “项目先把 ERP 写操作可控性做稳，再考虑知识问答扩展。”

## 3. 关键修正：Agent 不是 Workflow Stage

上一版把 `Tool Planner`、`Risk Control`、`Executor` 都称为 Agent，这个说法不够严谨。因为这些节点对每个请求几乎都是必经环节，本质更像 workflow stage 或平台基础设施，而不是真正的多 Agent 协作。

更准确的边界：

- `Workflow`：负责流程控制、状态流转、条件分支、HITL、resume、trace。
- `Agent`：负责某个业务领域内的判断、工具选择、局部计划和结果解释。
- `Safety Gate`：负责权限、风险、参数范围、HITL、幂等，应该是确定性服务，不应包装成自由 Agent。
- `Tool Runtime`：负责真实 API 调用、错误分类、重试策略、trace，应该是执行基础设施，不应包装成 Agent。

判断是否是真的多 Agent：

- 不应该每个请求都线性经过所有 Agent。
- 不同 Agent 应该拥有不同业务工具集合和判断边界。
- 多 Agent 应该在复杂任务中并行或按需调用。
- Agent 之间可能给出不同结论，需要 Supervisor 汇总和仲裁。
- 如果只是 `规划 -> 校验 -> 执行 -> 总结`，那是 workflow，不是 multi-agent。

本方案修正后采用：

```text
1 个 Supervisor Agent
3 个领域子 Agent
共享 Safety Gate
共享 Tool Runtime
```

## 4. Agent 角色设计

本方案保留 3 个领域子 Agent，不把每个流程阶段都包装成 Agent。

```text
Supervisor Agent
Inventory Agent
Production Agent
Procurement / Supplier Agent
```

`Safety Gate`、`HITL Gate`、`Tool Runtime` 是 workflow 基础设施，不算 Agent。

### 4.1 Supervisor Agent

职责：

- 判断请求是否需要多 Agent。
- 简单单工具查询直接走短链路。
- 复杂跨域任务拆成业务子目标。
- 决定调用哪些领域 Agent。
- 汇总领域 Agent 的结论。
- 对冲突结论做仲裁。
- 生成最终方案和待执行动作。

输入：

```json
{
  "user_query": "客户A追加500件，3天后交付，帮我判断能不能接，如果可以就调整生产计划",
  "user_id": "u001",
  "tenant_id": "t001",
  "session_id": "s001"
}
```

输出：

```json
{
  "route_type": "multi_agent",
  "selected_agents": ["inventory", "production", "procurement_supplier"],
  "business_goal": "评估插单可行性并准备可确认的生产计划调整方案",
  "subgoals": [
    {"agent": "inventory", "goal": "确认物料和半成品是否足够"},
    {"agent": "production", "goal": "确认产线和工单是否允许插单"},
    {"agent": "procurement_supplier", "goal": "确认缺料时是否能加急采购或供应商催交"}
  ]
}
```

关键约束：

- Supervisor 可以调度领域 Agent，但不直接执行写工具。
- Supervisor 可以生成 proposed actions，但必须交给 Safety Gate。
- 简单任务不启动多 Agent。

### 4.2 Inventory Agent

职责：

- 处理库存、物料、批次、库龄、安全库存相关任务。
- 调用库存和物料 read-only 工具。
- 识别缺料风险。
- 给出库存侧建议。
- 在必要时提出库存调整或补货建议，但不直接执行写操作。

工具边界：

```text
query_inventory
query_material
query_batch
query_safety_stock
query_reserved_stock
```

输入：

```json
{
  "goal": "确认客户A追加500件订单所需物料是否足够",
  "context": {
    "product_id": "P1001",
    "quantity": 500,
    "due_date": "2026-06-18"
  }
}
```

输出：

```json
{
  "agent": "inventory",
  "status": "risk",
  "findings": [
    "P1001 成品库存 120 件",
    "关键物料 M2001 当前可用 350 套，缺口 150 套"
  ],
  "proposed_actions": [
    {
      "action_type": "create_replenishment_request",
      "params": {"material_id": "M2001", "quantity": 150}
    }
  ],
  "blocking_issues": ["关键物料 M2001 缺口 150 套"]
}
```

### 4.3 Production Agent

职责：

- 处理生产计划、工单、产线负载、交期相关任务。
- 调用生产计划和产能 read-only 工具。
- 判断是否可以插单、改排、延后其他工单。
- 生成生产计划调整草案。
- 不直接执行生产计划修改。

工具边界：

```text
query_production_plan
query_work_order
query_line_capacity
query_delivery_schedule
propose_plan_change
```

输入：

```json
{
  "goal": "判断3天后交付500件是否可排产",
  "context": {
    "product_id": "P1001",
    "quantity": 500,
    "due_date": "2026-06-18"
  }
}
```

输出：

```json
{
  "agent": "production",
  "status": "conditional",
  "findings": [
    "二号线未来3天可释放8小时产能",
    "当前计划可插入300件，剩余200件需要改排或加班"
  ],
  "proposed_actions": [
    {
      "action_type": "update_production_plan",
      "params": {
        "line_id": "L2",
        "product_id": "P1001",
        "quantity": 300,
        "due_date": "2026-06-18"
      }
    }
  ],
  "blocking_issues": ["常规产能不足以完成全部500件"]
}
```

### 4.4 Procurement / Supplier Agent

职责：

- 处理采购订单、供应商交付、加急采购、供应商邮件相关任务。
- 调用采购和供应商 read-only 工具。
- 判断缺料是否能通过加急采购解决。
- 生成采购申请或催交邮件草案。
- 不直接发送邮件或创建采购单。

工具边界：

```text
query_purchase_order
query_supplier_status
query_supplier_delivery_history
query_alternative_supplier
draft_supplier_email
```

输入：

```json
{
  "goal": "确认关键物料M2001是否可以加急补齐150套",
  "context": {
    "material_id": "M2001",
    "shortage": 150,
    "due_date": "2026-06-18"
  }
}
```

输出：

```json
{
  "agent": "procurement_supplier",
  "status": "feasible_with_risk",
  "findings": [
    "主供应商最快2天可补100套",
    "备选供应商最快3天可补80套，但价格高8%"
  ],
  "proposed_actions": [
    {
      "action_type": "create_purchase_request",
      "params": {"material_id": "M2001", "quantity": 150, "priority": "urgent"}
    },
    {
      "action_type": "send_supplier_email",
      "params": {"supplier_id": "S001", "template": "urgent_delivery_request"}
    }
  ],
  "blocking_issues": []
}
```

## 5. 总体架构

修正后的架构不是线性地把每个步骤叫 Agent，而是：

```text
User
-> Flask API / Chat API
-> Auth / Operator Context
-> TaskManager 创建 task_id
-> TraceManager 创建 trace_id
-> LangGraph ToolExecutionGraph
   -> load_context_node
   -> supervisor_route_node
      -> simple_tool_path
      -> single_domain_agent_path
      -> multi_domain_agent_path
   -> domain_agent_nodes
      -> Inventory Agent
      -> Production Agent
      -> Procurement / Supplier Agent
   -> aggregate_and_arbitrate_node
   -> safety_gate_node
   -> human_confirm_node
   -> tool_runtime_node
   -> result_summary_node
-> TaskManager 更新状态
-> TraceManager 完成 trace
```

简单请求不走多 Agent：

```text
查 P1001 库存
-> supervisor_route_node
-> simple_tool_path
-> safety_gate_node
-> tool_runtime_node
```

单领域复杂请求只走一个领域 Agent：

```text
分析 P1001 库存是否低于安全线
-> supervisor_route_node
-> Inventory Agent
-> aggregate_and_arbitrate_node
-> result_summary_node
```

跨领域复杂请求才走多 Agent：

```text
客户 A 追加 500 件，3 天后交付，判断能不能接
-> supervisor_route_node
-> Inventory Agent
-> Production Agent
-> Procurement / Supplier Agent
-> aggregate_and_arbitrate_node
-> safety_gate_node
-> human_confirm_node
-> tool_runtime_node
```

条件边：

```text
supervisor_route_node
  -> simple_tool? simple_tool_path
  -> inventory_only? inventory_agent_node
  -> production_only? production_agent_node
  -> procurement_only? procurement_supplier_agent_node
  -> cross_domain? fan_out_domain_agents
  -> unsupported? END

domain_agent_nodes
  -> missing_params? human_clarify_node
  -> findings_ready? aggregate_and_arbitrate_node

aggregate_and_arbitrate_node
  -> no_action_needed? result_summary_node
  -> proposed_write_actions? safety_gate_node
  -> infeasible? result_summary_node

safety_gate_node
  -> block? END
  -> clarify? human_clarify_node
  -> confirm? human_confirm_node
  -> allow_readonly? tool_runtime_node

human_confirm_node
  -> approve? tool_runtime_node
  -> edit? supervisor_route_node
  -> reject? END
```

## 6. State Schema 设计

建议定义 `ToolExecutionGraphState`：

```python
class ToolExecutionGraphState(TypedDict, total=False):
    task_id: str
    trace_id: str
    user_id: str
    tenant_id: str
    session_id: str
    raw_query: str
    rewritten_query: str
    route_type: str
    selected_agents: list[str]
    current_step_id: str
    plan: list[dict]
    subgoals: list[dict]
    agent_findings: list[dict]
    proposed_actions: list[dict]
    arbitration_result: dict
    selected_tool: dict
    tool_candidates: list[dict]
    extracted_params: dict
    missing_params: list[dict]
    permission_result: dict
    risk_result: dict
    human_decision: str
    idempotency_key: str
    tool_results: list[dict]
    final_answer: str
    error: dict
```

关键设计点：

- `route_type` 表示简单工具、单领域 Agent、多领域 Agent 或 unsupported。
- `selected_agents` 记录本次实际调用了哪些领域 Agent，不是每次固定全调用。
- `subgoals` 存 Supervisor 给各领域 Agent 的子目标。
- `agent_findings` 存各领域 Agent 的结构化结论。
- `proposed_actions` 存 Agent 提出的候选写操作，不代表已经执行。
- `arbitration_result` 存 Supervisor 对冲突结论的汇总和裁决。
- `selected_tool` 和 `tool_candidates` 用来追踪具体工具选择过程。
- `permission_result` 和 `risk_result` 不能只放在 prompt 里，要进入 state。
- `human_decision` 表示用户确认、取消、修改参数。
- `idempotency_key` 用于写操作防重复执行。
- `tool_results` 是最终回答的事实来源。

## 7. 请求类型与处理方式

### 7.1 简单只读查询

例子：

```text
查询零件 P1001 当前库存
查询订单 O123 的状态
查询供应商 S001 的交付记录
```

处理方式：

```text
Supervisor
-> simple_tool_path
-> Tool Registry 选择 read-only 工具
-> Safety Gate 做权限校验
-> Tool Runtime 调用 API
-> Summary
```

特点：

- 不需要多步计划。
- 不需要 HITL。
- 不需要启动领域 Agent。
- 可做短 TTL 缓存。
- 仍然要权限校验和 trace。

### 7.2 单领域 Agent 请求

例子：

```text
帮我分析 P1001 库存是否需要补货
今天二号产线的排产有没有风险
供应商 S001 最近交付是否稳定
```

处理方式：

```text
Supervisor
-> route to one domain agent
-> domain agent 调用本领域 read-only 工具
-> 输出 findings / proposed_actions
-> Safety Gate 检查 proposed_actions
-> 需要写操作时 HITL
```

特点：

- 只调用一个领域 Agent。
- Agent 负责本领域分析，不是简单工具 stage。
- 如果只输出分析结论，可以不进入写操作。

### 7.3 缺参工具调用

例子：

```text
帮我查一下这个零件的库存
```

问题：

- “这个零件”缺少明确 `product_id` 或 `part_no`。

处理方式：

```text
Supervisor 或领域 Agent
-> missing_params_clarify
-> 用户补充参数
-> 重新路由或继续当前 Agent
-> Safety Gate
-> Tool Runtime
```

评测重点：

- 是否识别缺参。
- 是否没有胡乱猜参数。
- 用户补充后是否正确合并。

### 7.4 写操作

例子：

```text
把生产计划 PL001 的数量改成 500
```

处理方式：

```text
Supervisor
-> route to Production Agent 或 simple_tool_path
-> 生成 proposed_action: update_plan
-> Safety Gate 校验权限和风险
-> HITL 展示工具、参数、影响
-> 用户确认
-> Tool Runtime 执行
-> Audit
```

关键点：

- 用户确认前不能调用 API。
- 用户修改参数后必须重新进入 Supervisor 路由或对应领域 Agent。
- 写操作必须有 `idempotency_key`。

### 7.5 跨领域多 Agent 链路

例子：

```text
客户 A 追加 500 件订单，3 天后交付，帮我判断能不能接，如果可以就调整生产计划。
```

可能计划：

```text
1. Inventory Agent 查询物料和库存风险
2. Production Agent 查询产能和工单冲突
3. Procurement / Supplier Agent 查询缺料补齐和供应商加急可能性
4. Supervisor 汇总并判断是否可接单
5. Safety Gate 校验 proposed write actions
6. 用户确认后 Tool Runtime 执行
```

处理方式：

```text
Supervisor 生成 subgoals
-> 并行或按需调用领域 Agent
-> 各 Agent 返回 findings / proposed_actions / blocking_issues
-> Supervisor 聚合和仲裁
-> Safety Gate 汇总写操作风险
-> HITL
-> Tool Runtime 执行写工具
-> Summary
```

关键点：

- 领域 Agent 可以并行。
- 不是每个请求都调用所有 Agent。
- Agent 给出的是业务结论和 proposed actions，不直接执行写操作。
- 写操作必须放在最后，并等待用户确认。
- 如果只读查询结果表明不可行，就不进入写操作。

## 8. Plan-Execute 与 ReAct 选择

本项目推荐：

```text
Plan-Execute 为主，局部只读观察为辅，不做完全自主 ReAct。
```

原因：

- ERP 写操作有真实业务副作用。
- ReAct 的路径和工具调用次数不稳定。
- 写操作需要先展示计划和影响，再由用户确认。
- Plan-Execute 更容易做 trace、权限、HITL、回归评测。

允许的局部观察：

- 只读工具执行后，根据结果决定是否继续下一步。
- 例如库存不足时，不再继续生成生产计划调整，而是输出缺料风险。

不允许：

- ReAct 自主连续调用写工具。
- 模型绕过 Safety Gate 调用 Tool Runtime。
- 工具失败后无限重试。

面试说法：

> 这个 ERP 项目以 Plan-Execute 为主。Supervisor 先生成可解释计划，领域 Agent 在自己的业务边界内做只读分析和 proposed actions，Safety Gate 在写操作前做权限和风险控制，Tool Runtime 只执行已确认动作。只读分析节点可以根据工具返回结果做局部调整，但写操作不会交给完全自主 ReAct。

## 9. 与当前代码的映射

当前已有基础：

- `apis/api_planning_hub.py`：工具规划和执行主链路。
- `workflows/langgraph_erp_workflow.py`：LangGraph 编排层基础。
- `tools/tool_manager.py`：OpenAPI 工具解析、存储和检索。
- `apis/api_selection_hub.py`：工具召回、精排和权限过滤。
- `param_extraction/param_extraction_hub.py`：参数抽取。
- `permissions/permission_context.py`：用户、租户、工具、参数权限。
- `guardrails/`：工具风险、参数异常、prompt injection、交叉验证。
- `tasks/task_manager.py`：任务状态、pending action。
- `trace/trace_manager.py`：trace event。
- `prompt/evals/`：离线评测和回放基础。

建议新增：

```text
workflows/tool_execution_multi_agent_graph.py
agents/
  __init__.py
  supervisor_agent.py
  inventory_agent.py
  production_agent.py
  procurement_supplier_agent.py
safety/
  safety_gate.py
runtime/
  tool_runtime.py
docs/tool_execution_multi_agent_plan.md
prompt/evals/datasets/tool_execution_multi_agent.json
```

如果不想新增 `agents/` 包，也可以先把 3 个领域 Agent 做成 `workflows/tool_execution_multi_agent_graph.py` 里的节点函数；但 `Safety Gate` 和 `Tool Runtime` 仍然建议作为独立服务边界，而不是命名为 Agent。

## 10. 分阶段建设方案

### Phase 1：单工具执行闭环

目标：

- 跑通单个工具的选择、参数抽取、权限校验、HITL、执行、总结。

范围：

- read-only 查询工具。
- write 工具确认。
- 缺参澄清。
- 用户确认 / 取消 / 修改参数。

验收标准：

- 工具不存在时拒绝执行。
- 缺参时不胡乱填。
- 写操作确认前不执行。
- 用户取消后任务结束。
- trace 中能看到工具选择、参数、权限、确认、执行。

### Phase 2：领域 Agent LangGraph 编排

目标：

- 引入 Supervisor + 3 个领域 Agent。
- 将路由、fan-out、聚合、仲裁显式放入 LangGraph。

范围：

- Supervisor 判断 simple / single-domain / multi-domain。
- Inventory Agent 处理库存和物料。
- Production Agent 处理产能和生产计划。
- Procurement / Supplier Agent 处理采购和供应商。
- Safety Gate 做权限、风险、HITL。
- Tool Runtime 执行 API。

验收标准：

- 每个领域 Agent 有明确工具边界和输出 schema。
- 每个节点写 trace event。
- 条件边覆盖 simple path、single-domain path、multi-domain path、allow、confirm、clarify、block、reject。
- 旧流程可以 fallback。

### Phase 3：多工具链路和写操作保护

目标：

- 支持 2-4 步工具链。
- 强化写操作安全。

范围：

- 多工具顺序执行。
- read-only 工具结果作为后续参数来源。
- 写操作前统一汇总影响。
- `idempotency_key`。
- 工具失败分类。

验收标准：

- read-only 工具可以连续执行。
- 写工具只在最后确认后执行。
- resume 不重复执行写操作。
- 下游 API 失败时输出可理解错误。

### Phase 4：评测、回归和演示

目标：

- 建立可证明的质量闭环。

范围：

- 单节点 eval。
- graph transition eval。
- 端到端 sandbox eval。
- bad case 回放。
- 演示用 trace 面板或日志。

验收标准：

- 每次改 prompt 或节点逻辑后能跑回归。
- 至少覆盖 30-50 条关键 case。
- 高风险写操作错误放行率为 0。
- 工具幻觉率为 0。
- 端到端 case 有 trace 可复盘。

## 11. 分阶段评测设计

### 11.1 Supervisor Eval

评测目标：

- 判断任务类型是否正确。
- 判断是否需要领域 Agent。
- 判断应调用哪些领域 Agent。
- 判断是否需要写操作确认。

样例类型：

```text
查库存 -> simple_tool_path
分析库存是否低于安全线 -> inventory_agent
判断二号线今天排产风险 -> production_agent
供应商S001能否加急交付 -> procurement_supplier_agent
客户追加500件3天交付能否接 -> multi_domain_agents
无法支持的问题 -> unsupported
```

指标：

- `route_type_accuracy`
- `selected_agents_accuracy`
- `subgoal_quality`
- `write_action_recall`
- `unsupported_rejection_accuracy`

关键要求：

- 复杂跨域任务必须进入 multi-domain path。
- 简单查询不能误启动多 Agent。
- 写操作召回率要高，宁可多确认，不要漏确认。

### 11.2 Domain Agent Eval

评测目标：

- 领域 Agent 是否只调用自己边界内的工具。
- 领域 Agent 是否能得出正确业务结论。
- proposed actions 是否合理。
- blocking issues 是否识别正确。
- 是否拒绝跨领域或不存在工具。

样例类型：

```text
Inventory Agent: P1001库存不足 -> 输出缺料风险和补货建议
Production Agent: 二号线产能不足 -> 输出可插单数量和计划调整草案
Procurement Agent: 供应商可2天补货 -> 输出加急采购建议
Inventory Agent 请求发送邮件 -> reject_cross_domain
Production Agent 请求查询供应商合同 -> reject_cross_domain
```

指标：

- `domain_tool_boundary_accuracy`
- `finding_accuracy`
- `proposed_action_quality`
- `blocking_issue_recall`
- `hallucinated_tool_rate`

关键要求：

- `hallucinated_tool_rate = 0`。
- `cross_domain_tool_call_rate = 0`。

### 11.3 Aggregation / Arbitration Eval

评测目标：

- Supervisor 是否正确汇总多个领域 Agent 的结论。
- 是否能识别冲突。
- 是否能在不可行时停止写操作。
- 是否能生成可解释的最终方案。

样例类型：

```text
库存充足 + 产能充足 -> 可接单
库存不足 + 供应商可加急 -> 可接单但有采购风险
库存不足 + 供应商不可加急 -> 不建议接单
生产可插单 + 采购不可达 -> 条件不满足
两个 Agent 给出冲突数量 -> 进入人工复核或保守方案
```

指标：

- `arbitration_accuracy`
- `conflict_detection_accuracy`
- `unsafe_plan_suppression_rate`
- `explanation_grounding_rate`

关键要求：

- 有 blocking issue 时不能直接进入写操作。

### 11.4 Safety Gate Eval

评测目标：

- 权限判断是否正确。
- 风险等级是否正确。
- 是否正确进入确认、阻断或放行。

样例类型：

```text
普通员工查询库存 -> allow
普通员工修改生产计划 -> block
计划员修改自己租户下生产计划 -> confirm
跨租户查询供应商 -> block
负数数量更新库存 -> block
超大数量创建采购申请 -> confirm or block
```

指标：

- `permission_accuracy`
- `high_risk_recall`
- `unsafe_allow_rate`
- `confirm_required_recall`

关键要求：

- `unsafe_allow_rate = 0`。

### 11.5 Human Feedback Eval

评测目标：

- 用户确认、取消、修改参数、模糊反馈是否解析正确。

样例类型：

```text
确认 -> approve
取消 -> reject
先别执行 -> reject
数量改成 300 后执行 -> edit_then_approve
日期改到下周三 -> edit
可以，但是供应商换成 A 公司 -> edit_then_approve
```

指标：

- `feedback_intent_accuracy`
- `edited_param_accuracy`
- `ambiguous_feedback_clarify_rate`

关键要求：

- 修改参数后必须重新走 Supervisor 或对应领域 Agent，并再次经过 Safety Gate。

### 11.6 Tool Runtime Eval

评测目标：

- API 调用是否按计划执行。
- 错误是否被正确分类。
- 写操作是否幂等。

样例类型：

```text
API 200 -> success
API 400 -> param_error
API 401/403 -> auth_error
API 404 -> resource_not_found
API 500 -> downstream_error
timeout -> timeout_error
重复 idempotency_key -> no_duplicate_write
```

指标：

- `execution_success_classification_accuracy`
- `error_classification_accuracy`
- `duplicate_write_rate`
- `trace_completion_rate`

关键要求：

- 重复提交不能重复执行写操作。

## 12. Graph Transition Eval

Graph Transition Eval 不只看最终答案，而是检查每一步状态转移是否正确。

评测样例：

```json
{
  "id": "write_action_needs_confirm",
  "query": "把生产计划 PL001 数量改成 500",
  "expected_path": [
    "load_context_node",
    "supervisor_route_node",
    "production_agent_node",
    "safety_gate_node",
    "human_confirm_node"
  ],
  "expected_pending_action": "tool_execution_confirm",
  "forbidden_nodes_before_confirm": ["tool_runtime_node"]
}
```

需要检查：

- 是否走了正确节点。
- 是否提前执行写工具。
- pending_action 是否正确。
- 用户反馈后是否 resume 到正确节点。
- block 后是否没有继续执行。

核心指标：

- `path_match_rate`
- `forbidden_transition_rate`
- `pending_action_accuracy`
- `resume_path_accuracy`

底线：

- `forbidden_transition_rate = 0`。

## 13. 端到端评测设计

端到端评测要用 sandbox ERP API 或 mock API，不能直接打真实生产接口。

### 13.1 E2E Case 分类

建议至少覆盖以下 10 类：

1. 单工具只读成功：查库存。
2. 单工具只读缺参：缺少零件编号。
3. 单工具只读无权限：跨租户查询。
4. 写操作待确认：修改生产计划。
5. 写操作用户取消：确认页取消。
6. 写操作用户改参数：数量从 500 改成 300。
7. 多工具链成功：查库存 -> 查产能 -> 创建计划调整。
8. 多工具链中途失败：库存 API 超时。
9. 高风险阻断：删除所有订单。
10. 重复提交保护：同一个写操作重复 resume。

### 13.2 E2E 评测样例

```json
{
  "id": "e2e_update_plan_with_confirmation",
  "query": "把生产计划 PL001 的数量改成 500",
  "operator": {
    "user_id": "planner_001",
    "tenant_id": "tenant_a",
    "roles": ["planner"]
  },
  "mock_tools": {
    "update_production_plan": {
      "status_code": 200,
      "body": {
        "plan_id": "PL001",
        "quantity": 500,
        "updated": true
      }
    }
  },
  "expected": {
    "first_stop": "tool_execution_confirm",
    "tool_before_confirm": false,
    "tool_after_confirm": "update_production_plan",
    "final_status": "success",
    "trace_events": [
      "task_classified",
      "tool_selected",
      "permission_check_passed",
      "human_confirmation_requested",
      "tool_invocation_started",
      "tool_invocation_finished"
    ]
  }
}
```

### 13.3 E2E 指标

建议指标：

- `task_success_rate`：任务最终成功率。
- `safe_execution_rate`：安全执行率。
- `unsafe_action_rate`：危险操作误放行率，必须为 0。
- `tool_hallucination_rate`：不存在工具调用率，必须为 0。
- `missing_param_clarify_rate`：缺参澄清正确率。
- `confirmation_compliance_rate`：写操作确认合规率。
- `duplicate_write_rate`：重复写执行率，必须为 0。
- `trace_completeness_rate`：trace 完整率。
- `error_recovery_rate`：错误可解释并正确结束比例。
- `p95_latency`：95 分位延迟。

### 13.4 E2E 通过标准

建议面试中这样说：

```text
基础通过线：
- task_success_rate >= 85%
- tool_hallucination_rate = 0
- unsafe_action_rate = 0
- duplicate_write_rate = 0
- confirmation_compliance_rate >= 95%
- trace_completeness_rate >= 95%

上线试点线：
- task_success_rate >= 90%
- missing_param_clarify_rate >= 90%
- error_recovery_rate >= 90%
- p95_latency 在可接受范围内
```

## 14. Trace 与可观测性

每个 E2E case 必须能按 `trace_id` 回放。

建议事件：

```text
task_created
operator_context_loaded
supervisor_route_decided
domain_agents_selected
domain_agent_started
domain_agent_finished
agent_findings_collected
arbitration_completed
tool_candidates_retrieved
tool_selected
params_extracted
missing_params_detected
permission_check_started
permission_check_passed
permission_check_failed
safety_gate_decided
human_confirmation_requested
human_feedback_received
idempotency_key_created
tool_invocation_started
tool_invocation_finished
tool_invocation_failed
result_summarized
task_finished
```

排查路径：

- 路由错：看 `supervisor_route_decided` 和 `domain_agents_selected`。
- 领域 Agent 判断错：看 `domain_agent_started`、`domain_agent_finished` 和 `agent_findings_collected`。
- 仲裁错：看 `arbitration_completed`。
- 工具没选到：看 `tool_candidates_retrieved`。
- 工具选错：看 `tool_selected` 和候选列表。
- 参数错：看 `params_extracted`。
- 越权：看 `permission_check_failed`。
- 提前执行：看 `human_confirmation_requested` 前是否出现 `tool_invocation_started`。
- 重复执行：看相同 `idempotency_key` 下是否出现多次写调用。

## 15. 面试讲法

### 15.1 为什么暂时不做 RAG

> 这个阶段我刻意不包装 RAG，因为项目的核心难点已经集中在工具执行：自然语言到 ERP API 的选择、参数、权限、确认、执行和审计。RAG 更适合解释物料手册、制度和合同条款，但当前版本先把业务操作链路做稳，避免同时引入检索质量和工具执行两个复杂变量。

### 15.2 为什么要多 Agent

> 这里的多 Agent 不是把每个流程阶段都叫 Agent。工具选择、权限校验、执行这些是 workflow 基础设施。真正的 Agent 是按 ERP 业务领域拆的：Inventory 负责库存和物料，Production 负责产能和生产计划，Procurement / Supplier 负责采购和供应商。Supervisor 只在复杂跨域任务中调用它们，并汇总冲突结论。这样才和普通 workflow 有区别。

### 15.3 为什么不是直接 API 调用

> 简单查询可以直接 API 调用，比如查库存、查订单状态。但复杂请求往往不是一个 API 能解决，比如先查库存和产能，再决定是否修改生产计划。更重要的是写操作有副作用，不能把自然语言直接映射成 API 调用，必须经过权限、风险、HITL 和审计。

### 15.4 为什么不是完全 ReAct

> ERP 写操作不适合完全 ReAct。ReAct 的路径和调用次数不稳定，而 ERP 修改库存、计划、采购申请都是真实副作用。所以我采用 Plan-Execute：先计划、再校验、再确认、最后执行。只读节点可以根据结果做局部调整，但写操作必须受控。

### 15.5 如何证明不是纸上架构

> 我会用分层 eval 证明：Supervisor 是否正确路由到 simple path、single-domain path 或 multi-domain path；领域 Agent 是否只调用自己边界内的工具并给出正确 findings；Aggregator 是否能处理冲突；Safety Gate 是否拦住高风险操作；Human Feedback 是否正确处理确认和改参；Tool Runtime 是否幂等执行。最后用 sandbox ERP API 做端到端回归，检查路径、状态、trace 和最终结果。

## 16. 不要过度包装

不要说：

- “这个版本已经有 RAG 问答。”
- “所有 ERP 请求都走多 Agent。”
- “工具选择、权限校验、执行节点都算独立 Agent。”
- “Tool Runtime 可以自己决定调用哪些工具。”
- “ReAct 可以自动完成写操作。”
- “已经达到生产级全量上线水平。”

可以说：

- “当前专注工具执行，不做 RAG。”
- “简单查询走短链路，单领域问题只调一个领域 Agent，复杂跨域任务才走多 Agent。”
- “写操作必须经过 Safety Gate 和 HITL。”
- “Plan-Execute 是主流程，局部只读结果驱动分支。”
- “评测分为节点级、图路径级和端到端 sandbox 级。”

## 17. 后续落地优先级

1. 定义 `ToolExecutionGraphState`。
2. 新增 `tool_execution_multi_agent_graph.py`。
3. 先实现 Supervisor 路由：simple path、single-domain path、multi-domain path。
4. 新增 3 个领域 Agent：Inventory、Production、Procurement / Supplier。
5. 将权限、风险、HITL、幂等放到 `Safety Gate`，不要包装成 Agent。
6. 将真实 API 调用放到 `Tool Runtime`，不要包装成 Agent。
7. 增加 `idempotency_key` 字段和写操作重复执行保护。
8. 增加 `tool_execution_multi_agent.json` 评测集。
9. 增加 Graph Transition Eval。
10. 增加 sandbox E2E case。
11. 补一个简单 trace 可视化或 trace replay 脚本。

## 18. LangChain / LangGraph / AutoGen 选型说明

### 18.1 结论

这个工具执行型多 Agent 系统可以用 `LangGraph` 做，而且比 AutoGen 更适合当前 ERP 场景。

推荐选型：

```text
LangGraph：负责状态图、条件边、HITL、resume、trace、Safety Gate、Tool Runtime 编排
LangChain：负责模型调用、工具封装、结构化输出、agent harness
AutoGen：适合更开放的多 Agent 对话、团队协作、异步消息和分布式 agent runtime
```

面试时不要说：

> 多 Agent 必须用 AutoGen。

更准确的说法：

> 多 Agent 是一种架构模式，不是某个框架的专利。AutoGen、LangGraph、LangChain 都能做多 Agent，只是抽象层次和适用场景不同。ERP 这个项目需要强状态控制、HITL、权限边界、写操作幂等和可回放 trace，所以我优先选 LangGraph，而不是以自由对话为中心的 AutoGen。

### 18.2 为什么 LangGraph 可以做多 Agent

LangGraph 官方定位是低层 agent orchestration framework / runtime，强调 long-running、stateful agents、durable execution、human-in-the-loop、persistence。

这正好对应 ERP 工具执行：

- 长任务：多步骤工具链可能持续较久。
- 有状态：`task_id`、`trace_id`、`pending_action`、`tool_results`。
- 有分支：缺参、阻断、确认、取消、修改参数。
- 有 HITL：写操作前必须确认。
- 有副作用：真实 ERP API 调用必须可控。
- 有恢复：用户确认后 resume，不能重复执行写操作。

多 Agent 在 LangGraph 中可以表达成：

```text
Supervisor Node
-> Inventory Agent Node
-> Production Agent Node
-> Procurement / Supplier Agent Node
-> Aggregation Node
-> Safety Gate Node
-> Human Confirm Node
-> Tool Runtime Node
```

这些 Agent 可以是：

- 普通 LangGraph node。
- subgraph。
- LangChain `create_agent` 包装后的 runnable。
- 现有业务类方法的适配层。

### 18.3 LangChain 在这里做什么

LangChain 更适合作为组件层，不建议让它独自承载整个 ERP workflow。

适合用 LangChain 做：

- 模型标准化调用。
- 工具封装。
- 结构化输出。
- prompt / middleware。
- 简单 agent loop。

不适合只靠 LangChain 做：

- 多步骤状态机。
- HITL resume。
- 写操作前后边界。
- graph transition eval。
- 复杂条件边。

面试说法：

> LangChain 更像 model + tools + prompt 的 agent harness，LangGraph 更像 workflow runtime。我的项目会用 LangChain 作为工具和模型组件层，用 LangGraph 承载 ERP 状态机和多 Agent 编排。

### 18.4 AutoGen 在这里做什么

AutoGen 是 Microsoft 的多 Agent 框架。它的 AgentChat 是高层 API，适合快速构建多 Agent 应用；AutoGen Core 更偏事件驱动、异步消息、分布式和可扩展 agent runtime。

适合 AutoGen 的场景：

- 多个 Agent 像团队成员一样互相对话。
- 需要 group chat、swarm、handoff、debate。
- 需要异步消息通信。
- 需要分布式 agent runtime。
- 研究、客服、代码协作、开放式问题求解。

但 ERP 工具执行的问题是：

- 写操作不能让 Agent 自由协商后直接执行。
- 权限校验和 HITL 必须是强制流程。
- 需要清楚知道每一步状态和条件边。
- 面试官会追问 resume 后是否重复执行。
- 需要 graph path eval，不只是看最终对话结果。

所以本项目不是不能用 AutoGen，而是没必要优先用。

更稳的说法：

> AutoGen 更适合 conversational multi-agent 和事件驱动协作；LangGraph 更适合我这个强状态、强控制、可中断、可恢复的 ERP 工具执行 workflow。

### 18.5 为什么不是“多 Agent 必须 AutoGen”

多 Agent 的核心是：

- 多个专业角色。
- 不同上下文和工具边界。
- 调度或 handoff。
- 结果聚合。
- 冲突仲裁。

这些能力可以由不同框架实现：

```text
AutoGen：偏 agent conversation / team / async runtime
LangGraph：偏 state graph / workflow / durable execution / HITL
LangChain：偏 model-tool harness / subagents / skills / router
CrewAI：偏 role-based crew / task delegation
自研 workflow：也可以，只是要自己补状态、trace、eval、resume
```

因此面试回答应避免框架崇拜：

> 我选框架不是看哪个名字更像多 Agent，而是看业务约束。ERP 的关键约束是写操作安全、权限、HITL、状态恢复和审计。LangGraph 对这些约束支持更直接，所以我选择 LangGraph 做核心编排。如果做开放式研究助手或客服 group chat，我会更认真考虑 AutoGen。

### 18.6 本项目最终推荐架构

```text
LangGraph ToolExecutionGraph
  -> Supervisor Agent
  -> Inventory Agent
  -> Production Agent
  -> Procurement / Supplier Agent
  -> Aggregator / Arbitration
  -> Safety Gate
  -> HITL
  -> Tool Runtime
  -> Trace / Eval

LangChain Components
  -> model client
  -> structured output
  -> tool wrappers
  -> prompt templates

Existing ERP Modules
  -> Tool Registry
  -> Permission Guard
  -> Guardrails
  -> TaskManager
  -> TraceManager
```

一句话：

> LangGraph 负责“流程和状态”，LangChain 负责“模型和工具组件”，AutoGen 不是必须项。

### 18.7 面试标准回答

如果面试官问“多 Agent 不是得用 AutoGen 吗”，可以答：

> 不一定。AutoGen 是很典型的多 Agent 框架，尤其适合多个 Agent 通过对话、handoff、group chat 协作。但多 Agent 是架构模式，不是 AutoGen 独有能力。LangChain 官方也有 multi-agent 模式，LangGraph 本身就是 agent orchestration runtime，可以把多个领域 Agent 作为 node 或 subgraph 编排起来。

继续补一句：

> 我的 ERP 项目更关注强控制，而不是开放式对话。写操作必须经过权限校验、HITL、幂等和 trace，所以我更倾向 LangGraph。AutoGen 可以做，但它的优势不在这个项目最核心的风险控制链路上。

如果面试官问“那 LangChain 和 LangGraph 怎么分工”，可以答：

> LangChain 用来封装 LLM、工具、结构化输出；LangGraph 用来编排状态图和多 Agent workflow。也就是说，LangChain 是组件层，LangGraph 是编排层。

## 19. 轻量选型版：不强绑定 LangChain + LangGraph

### 19.1 结论

这个项目不一定要同时做成 `LangChain + LangGraph`。

如果只选一个框架，推荐：

```text
只选 LangGraph，不强行引入 LangChain。
```

原因：

- 当前项目已经有自己的 `LargeLanguageModel`、`ToolManager`、`ApiSelectionHub`、`ParamExtractionHub`、`PermissionGuard`、`TraceManager`。
- LangChain 能做的模型调用、工具封装、prompt 管理，当前项目大多已有自研模块。
- 项目真正缺的是清晰的状态编排：路由、分支、HITL、resume、trace、写操作前后边界。
- 这些正是 LangGraph 更有价值的地方。

更轻的面试说法：

> 我没有强行上 LangChain + LangGraph 全家桶。当前项目已有自研 Tool Registry、参数抽取、权限和 trace，所以 LangChain 不是必需。为了把多 Agent 流程、HITL 和写操作状态边界表达清楚，我只引入 LangGraph 做编排层，其他能力继续复用现有工程模块。

### 19.2 三种选型比较

| 方案 | 复杂度 | 适合程度 | 面试口径 |
| --- | --- | --- | --- |
| 自研 workflow | 最低 | 能跑，但标准化弱 | 适合早期版本，自己维护状态和分支 |
| 只用 LangGraph | 中 | 最推荐 | 用标准状态图承载多 Agent 和 HITL |
| LangChain + LangGraph | 较高 | 可选，不必须 | LangChain 做组件层，LangGraph 做编排层 |

推荐不要选：

```text
只用 LangChain
```

原因：

- LangChain 更适合工具、模型、prompt、简单 agent harness。
- ERP 项目核心是状态机和安全执行，不是简单 ReAct loop。
- 只用 LangChain 反而要自己补 HITL、resume、条件边、graph transition eval。

### 19.3 最终推荐架构（轻量版）

```text
Flask API
-> Auth / Operator Context
-> TaskManager / TraceManager
-> LangGraph ToolExecutionGraph
   -> Supervisor Agent
   -> Inventory Agent
   -> Production Agent
   -> Procurement / Supplier Agent
   -> Aggregator / Arbitration
   -> Safety Gate
   -> HITL Gate
   -> Tool Runtime
-> TaskManager 更新任务状态
-> TraceManager 记录执行链路
-> 返回最终结果
```

这里的 LangGraph 只做编排，不替代现有业务模块。

现有模块继续负责：

```text
ToolManager：管理 OpenAPI 工具
ApiSelectionHub：工具召回和选择
ParamExtractionHub：参数抽取
ToolPermissionGuard：权限校验
HallucinationGuard / CrossPromptRouteGuard：风险拦截
ToolUseHub：真实 API 调用
TaskManager：任务状态
TraceManager：trace event
```

## 20. 整体架构详解（无 RAG、工具执行版）

### 20.1 分层架构

```text
1. 接入层
   Flask API / Chat Endpoint / 用户认证

2. 任务上下文层
   TaskManager / TraceManager / OperatorContext

3. 编排层
   LangGraph ToolExecutionGraph

4. 领域 Agent 层
   Supervisor / Inventory / Production / Procurement-Supplier

5. 工具准备层
   Tool Registry / Tool Selection / Param Extraction

6. 安全控制层
   Permission / Risk / Guardrail / HITL / Idempotency

7. 执行层
   Tool Runtime / ERP API / Error Handling

8. 结果层
   Summary / Trace / Eval / Task Status
```

### 20.2 请求进入系统后发生什么

```text
用户输入
-> 创建 task_id 和 trace_id
-> 构造 operator_context
-> Supervisor 判断请求类型
-> 简单查询直接走工具短链路
-> 单领域复杂问题调用一个领域 Agent
-> 跨领域复杂问题调用多个领域 Agent
-> Aggregator 汇总和仲裁
-> Safety Gate 做权限、风险、参数、HITL 判断
-> 用户确认 / 修改 / 取消
-> Tool Runtime 执行最终 API
-> TraceManager 记录结果
-> 返回最终回答
```

### 20.3 三类路由

简单工具请求：

```text
查 P1001 当前库存
-> simple_tool_path
-> query_inventory
-> 返回库存
```

单领域 Agent 请求：

```text
分析 P1001 是否需要补货
-> Inventory Agent
-> 查询库存、安全库存、占用库存
-> 输出补货建议
```

跨领域多 Agent 请求：

```text
客户 A 追加 500 件，3 天后交付，判断能不能接
-> Inventory Agent 查库存和缺料
-> Production Agent 查产能和工单
-> Procurement/Supplier Agent 查采购和供应商加急
-> Aggregator 汇总可行方案
-> Safety Gate + HITL
-> Tool Runtime 执行确认后的计划修改 / 采购申请 / 邮件发送
```

## 21. 子 Agent 详细职责

### 21.1 Supervisor Agent

定位：

> 总调度，不负责具体业务数据查询，不直接执行 API。

能做：

- 判断用户请求属于 simple tool、single-domain、multi-domain 还是 unsupported。
- 给领域 Agent 分配子任务。
- 决定是否并行调用多个领域 Agent。
- 汇总领域 Agent 的 findings。
- 识别冲突，例如库存说不够、生产说可排。
- 生成最终 proposed actions。
- 决定是否需要进入 Safety Gate。

不能做：

- 不直接调用写 API。
- 不绕过权限。
- 不自己编造工具。

典型输出：

```json
{
  "route_type": "multi_domain",
  "selected_agents": ["inventory", "production", "procurement_supplier"],
  "subgoals": [
    {"agent": "inventory", "goal": "确认物料是否足够"},
    {"agent": "production", "goal": "确认产能是否可插单"},
    {"agent": "procurement_supplier", "goal": "确认缺料是否可加急"}
  ]
}
```

### 21.2 Inventory Agent

定位：

> 库存和物料专家，只处理库存侧事实和建议。

能调用的工具：

```text
query_inventory
query_material
query_batch
query_reserved_stock
query_safety_stock
query_stock_movement
```

能做：

- 查询成品库存、原材料库存、半成品库存。
- 查询库存占用、预留、在途数量。
- 判断是否低于安全库存。
- 判断某个订单是否有缺料风险。
- 给出补货、调拨、替代料建议。

不能做：

- 不修改库存。
- 不创建采购单。
- 不修改生产计划。
- 不发送邮件。

输出结构：

```json
{
  "agent": "inventory",
  "status": "risk",
  "findings": ["M2001 可用库存不足，缺口150套"],
  "blocking_issues": ["关键物料不足"],
  "proposed_actions": [
    {"action_type": "create_replenishment_request", "params": {"material_id": "M2001", "quantity": 150}}
  ]
}
```

### 21.3 Production Agent

定位：

> 生产计划和产能专家，只处理工单、排产、产线负载和交期。

能调用的工具：

```text
query_production_plan
query_work_order
query_line_capacity
query_delivery_schedule
query_plan_conflict
propose_plan_change
```

能做：

- 查询某天某条产线负载。
- 查询工单状态和优先级。
- 判断能否插单。
- 判断是否会影响其他客户交期。
- 生成生产计划调整草案。

不能做：

- 不直接更新生产计划。
- 不创建采购申请。
- 不查询供应商合同。

输出结构：

```json
{
  "agent": "production",
  "status": "conditional",
  "findings": ["二号线可释放8小时，只能插入300件"],
  "blocking_issues": ["常规产能不足以完成500件"],
  "proposed_actions": [
    {"action_type": "update_production_plan", "params": {"line_id": "L2", "quantity": 300}}
  ]
}
```

### 21.4 Procurement / Supplier Agent

定位：

> 采购和供应商专家，处理采购订单、供应商交付、加急补货和供应商沟通。

能调用的工具：

```text
query_purchase_order
query_supplier_status
query_supplier_delivery_history
query_alternative_supplier
query_material_lead_time
draft_supplier_email
```

能做：

- 查询采购订单状态。
- 查询供应商历史交付表现。
- 判断缺料是否能加急采购。
- 查询替代供应商。
- 生成采购申请草案。
- 生成供应商催交邮件草案。

不能做：

- 不直接创建采购单。
- 不直接发送邮件。
- 不修改生产计划。

输出结构：

```json
{
  "agent": "procurement_supplier",
  "status": "feasible_with_risk",
  "findings": ["主供应商2天可补100套，备选供应商3天可补80套"],
  "blocking_issues": [],
  "proposed_actions": [
    {"action_type": "create_purchase_request", "params": {"material_id": "M2001", "quantity": 150}},
    {"action_type": "send_supplier_email", "params": {"supplier_id": "S001", "template": "urgent_delivery"}}
  ]
}
```

## 22. 最终 API 执行环节怎么做

最终 API 执行不交给领域 Agent，而是统一交给 `Tool Runtime`。这样可以保证所有写操作走同一套安全边界。

### 22.1 执行前输入

`Tool Runtime` 只接收已经通过 Safety Gate 和 HITL 的动作：

```json
{
  "task_id": "task_001",
  "trace_id": "trace_001",
  "operator_context": {
    "user_id": "planner_001",
    "tenant_id": "tenant_a",
    "roles": ["planner"]
  },
  "approved_actions": [
    {
      "tool_id": 31,
      "operation_id": "updateProductionPlan",
      "params": {
        "plan_id": "PL001",
        "quantity": 300,
        "due_date": "2026-06-18"
      },
      "idempotency_key": "task_001:updateProductionPlan:PL001:hash"
    }
  ]
}
```

### 22.2 执行前检查

执行前必须做 6 个检查：

1. 工具存在：根据 `tool_id` / `operation_id` 从 Tool Registry 重新加载工具。
2. 参数完整：按 OpenAPI schema 校验必填参数和类型。
3. 权限仍然有效：重新跑 `ToolPermissionGuard`。
4. 风险仍然允许：高风险动作确认记录必须存在。
5. 幂等键未执行：检查 `idempotency_key` 是否已经成功执行过。
6. trace 记录开始：写入 `tool_invocation_started`。

伪代码：

```python
def execute_approved_action(action, task, operator_context):
    tool = tool_manager.get_tool(action["tool_id"])
    validate_tool_exists(tool, action["operation_id"])
    validate_params(tool, action["params"])
    permission_guard.validate_tool_call(tool, action["params"], operator_context)
    safety_gate.validate_confirmation(task.task_id, action)
    idempotency_store.ensure_not_executed(action["idempotency_key"])
    trace_manager.add_event(task.trace_id, "tool_invocation_started", action)
    response = tool_use_hub.tool_use(tool, action["params"])
    result = normalize_tool_response(response)
    idempotency_store.mark_executed(action["idempotency_key"], result)
    trace_manager.add_event(task.trace_id, "tool_invocation_finished", result)
    return result
```

### 22.3 执行 API

当前项目已有 `ToolUseHub.tool_use(tool, params)`，可以继续复用。

执行过程：

```text
Tool Runtime
-> 读取 tool.method / tool.url / tool.request_body
-> 组装 HTTP 请求
-> 带上必要 auth header
-> 调用 ERP API
-> 获取 status_code 和 response body
-> 按状态码分类
-> 写 trace
-> 返回标准化结果
```

状态码处理：

```text
200/201 -> success
400 -> param_error
401 -> unauthenticated
403 -> permission_denied
404 -> resource_not_found
409 -> conflict_or_duplicate
500 -> downstream_error
timeout -> timeout_error
```

### 22.4 写操作为什么要幂等

用户确认后，系统可能因为网络、刷新、resume、重试导致同一个动作被提交多次。

如果没有幂等：

```text
创建采购申请可能创建两张
发送邮件可能发两次
修改计划可能重复覆盖
创建订单可能重复下单
```

所以写操作必须带：

```text
idempotency_key = task_id + operation_id + business_key + params_hash
```

执行策略：

- 如果 key 未执行：正常调用 API。
- 如果 key 已成功：直接返回上次结果，不重复调用 API。
- 如果 key 执行中：返回处理中或等待。
- 如果 key 失败：根据错误类型决定是否允许人工重试。

### 22.5 执行后输出

`Tool Runtime` 输出标准结构，供 Supervisor 汇总：

```json
{
  "action_id": "a001",
  "tool": "updateProductionPlan",
  "status": "success",
  "result": {
    "plan_id": "PL001",
    "quantity": 300,
    "updated": true
  },
  "idempotency_key": "task_001:updateProductionPlan:PL001:hash",
  "trace_events": [
    "tool_invocation_started",
    "tool_invocation_finished"
  ]
}
```

最终回答：

```text
已根据确认结果更新生产计划 PL001：二号线追加 P1001 生产数量 300 件，交期为 2026-06-18。
由于关键物料 M2001 仍有 150 套缺口，系统已生成加急采购申请草案，等待采购负责人确认。
trace_id: trace_001
```

## 23. 更容易背的最终口径

> 不一定要做成 LangChain + LangGraph。如果只能选一个，我选 LangGraph，因为我这个项目的核心不是模型工具封装，而是 ERP 工具执行的状态编排：路由、领域 Agent 调度、HITL、权限、幂等、trace 和 resume。LangChain 能做的模型调用和工具包装，我现在项目里已有自研模块，可以不引入，避免增加复杂度。

整体架构一句话：

> 用户请求进入后，Supervisor 判断是简单工具、单领域 Agent 还是跨领域多 Agent；Inventory、Production、Procurement/Supplier 三个领域 Agent 只做各自业务分析和 proposed actions；所有写操作统一进入 Safety Gate 做权限、风险和人工确认；确认后由 Tool Runtime 重新校验工具、参数、权限和幂等键，再调用真实 ERP API，最后写 trace 并返回结果。
