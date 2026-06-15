from __future__ import annotations

from typing import Any, Dict, Optional, TypedDict

GRAPH_TITLE_FAILURE = "Failed tool chain"
TASK_STATUS_FINISH = -1
TASK_STATUS_WAIT_CONFIRM = 100
TASK_SUCCESS_CODE = 200
TASK_SYS_OUTPUT_STOP = "Task stopped: "
TASK_TYPE_APIS = 2
TASK_TYPE_SINGLE = 1

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - dependency may be absent in legacy envs
    END = "__end__"
    START = "__start__"
    StateGraph = None


class ERPGraphState(TypedDict, total=False):
    query: str
    raw_query: str
    task_id: str
    task_desc: str
    task_type: int
    operator_context: Dict[str, Any]
    tool_id: int
    tool_name: str
    operation_id: str
    tool_check_result: Dict[str, Any]
    final_status: str
    next_action: str
    error: str


class ERPAgentLangGraphWorkflow:
    """LangGraph orchestration layer for the ERP Agent workflow.

    The graph deliberately keeps domain logic in the existing managers/hubs.
    Its job is to make state transitions explicit and inspectable:
    classify -> select tool -> validate params/guardrails -> persist HITL gate.
    """

    def __init__(self, planning_hub: Any):
        self.hub = planning_hub
        self._graph = None

    @property
    def is_available(self) -> bool:
        return StateGraph is not None

    def run(self, query: str, task_id: str) -> Dict[str, Any]:
        if not self.is_available:
            raise RuntimeError("langgraph is not installed")
        graph = self._compiled_graph()
        return graph.invoke({
            "query": query,
            "raw_query": query,
            "task_id": task_id,
            "final_status": "running",
        })

    def resume_with_human_feedback(self, task: Any, human_feedback: str) -> None:
        """Bridge the current task-state HITL flow into the LangGraph branch.

        The existing frontend/backend protocol persists HITL state in MongoDB
        through pending_action and pending_payload. Until a durable LangGraph
        checkpointer is introduced, resume is routed through the existing
        handler while recording explicit graph-resume trace events.
        """
        self._trace(task, "langgraph_resume_requested", {
            "pending_action": getattr(task, "pending_action", ""),
            "feedback": human_feedback,
        })
        self.hub.api_planning_handle_human_feedback(task, human_feedback)
        refreshed = self.hub.task_manager.get_task_by_id(task.task_id)
        self._trace(refreshed or task, "langgraph_resume_completed", {
            "status": getattr(refreshed or task, "status", None),
            "pending_action": getattr(refreshed or task, "pending_action", ""),
        })

    def _compiled_graph(self):
        if self._graph is not None:
            return self._graph
        builder = StateGraph(ERPGraphState)
        builder.add_node("load_task", self._load_task)
        builder.add_node("classify_task", self._classify_task)
        builder.add_node("select_tool", self._select_tool)
        builder.add_node("check_tool", self._check_tool)
        builder.add_node("persist_decision", self._persist_decision)

        builder.add_edge(START, "load_task")
        builder.add_conditional_edges(
            "load_task",
            self._route_after_load,
            {"classify_task": "classify_task", "end": END},
        )
        builder.add_edge("classify_task", "select_tool")
        builder.add_conditional_edges(
            "select_tool",
            self._route_after_select,
            {"check_tool": "check_tool", "end": END},
        )
        builder.add_edge("check_tool", "persist_decision")
        builder.add_edge("persist_decision", END)

        self._graph = builder.compile()
        return self._graph

    def _load_task(self, state: ERPGraphState) -> ERPGraphState:
        task = self._task(state)
        if task is None:
            return {
                **state,
                "final_status": "failed",
                "next_action": "end",
                "error": "task_not_found",
            }
        operator_context = self.hub._operator_context_from_task(task)
        self._trace(task, "langgraph_node_completed", {
            "node": "load_task",
            "operator_context": operator_context.to_dict(),
        })
        return {
            **state,
            "raw_query": getattr(task, "raw_query", "") or state.get("raw_query", ""),
            "operator_context": operator_context.to_dict(),
            "next_action": "classify_task",
        }

    def _classify_task(self, state: ERPGraphState) -> ERPGraphState:
        query = state.get("query", "")
        task_id = state.get("task_id", "")
        is_single_task, root_task_description = self.hub.generate_task_hub.gen_root_task(query)
        if is_single_task:
            task_type = TASK_TYPE_SINGLE
            task_desc = query
            message = f"LangGraph classified [{query}] as a single-tool ERP task"
        else:
            task_type = TASK_TYPE_APIS
            task_desc = root_task_description
            message = f"LangGraph classified [{query}] as a multi-tool ERP workflow"
        self.hub._set_task_type(query, task_id, task_type, message)
        task = self._task(state)
        self._trace(task, "langgraph_node_completed", {
            "node": "classify_task",
            "task_type": task_type,
            "task_desc": task_desc,
        })
        return {
            **state,
            "task_type": task_type,
            "task_desc": task_desc,
        }

    def _select_tool(self, state: ERPGraphState) -> ERPGraphState:
        task = self._task(state)
        operator_context = self.hub._operator_context_from_task(task)
        task_desc = state.get("task_desc") or state.get("query", "")
        raw_query = state.get("raw_query") or state.get("query", "")
        self._trace(task, "operator_context_loaded", operator_context.to_dict())
        tool = self.hub.api_selection_hub.get_tool_coarse_and_fine(
            task_desc,
            None,
            topK=self.hub.topK,
            operator_context=operator_context,
            permission_guard=self.hub.permission_guard,
        )
        if tool is None:
            msg = f"No matching ERP tool found for request [{raw_query}]"
            self._trace(task, "no_tool_found", {
                "query": task_desc,
                "raw_query": raw_query,
                "guardrail_action": "clarify",
                "hallucination_type": "tool",
            })
            self.hub.task_manager.update_task_recorder(
                state["task_id"],
                TASK_STATUS_FINISH,
                TASK_SYS_OUTPUT_STOP + msg,
                graph_title=GRAPH_TITLE_FAILURE,
            )
            if task is not None:
                self.hub.trace_manager.finish_trace(task.trace_id, msg, status="failed")
            return {
                **state,
                "final_status": "failed",
                "next_action": "end",
                "error": "no_tool_found",
            }

        self._trace(task, "tool_selected", {
            "query": task_desc,
            "tool_id": tool.tool_id,
            "operation_id": tool.operationId,
            "tool_name": tool.name_for_human,
        })
        self._trace(task, "langgraph_node_completed", {
            "node": "select_tool",
            "tool_id": tool.tool_id,
            "operation_id": tool.operationId,
        })
        return {
            **state,
            "tool_id": tool.tool_id,
            "tool_name": tool.name_for_human,
            "operation_id": tool.operationId,
            "next_action": "check_tool",
        }

    def _check_tool(self, state: ERPGraphState) -> ERPGraphState:
        task = self._task(state)
        tool = self._tool(state)
        if tool is None:
            return {
                **state,
                "final_status": "failed",
                "next_action": "end",
                "error": "tool_missing_after_selection",
            }
        task_desc = state.get("task_desc") or state.get("query", "")
        raw_query = state.get("raw_query") or state.get("query", "")
        operator_context = self.hub._operator_context_from_task(task)
        result = self.hub._tool_check(
            tool,
            task_desc,
            raw_query,
            operator_context=operator_context,
            task=task,
        )
        self._trace(task, "langgraph_node_completed", {
            "node": "check_tool",
            "result_code": result.get("code"),
            "result_type": result.get("result"),
        })
        return {
            **state,
            "tool_check_result": result,
        }

    def _persist_decision(self, state: ERPGraphState) -> ERPGraphState:
        task = self._task(state)
        tool = self._tool(state)
        result = state.get("tool_check_result") or {}
        if not result:
            return {
                **state,
                "final_status": "failed",
                "error": "empty_tool_check_result",
            }

        if result.get("code") == TASK_SUCCESS_CODE:
            return self._persist_tool_execution_gate(state, task, tool, result)

        if result.get("result") == "missing_param_need_user":
            return self._persist_missing_param_gate(state, task, tool, result)

        block_event = "permission_blocked" if result.get("result") == "permission_denied" else "guardrail_blocked"
        self._trace(task, block_event, {
            "tool": result.get("tool"),
            "reason": result.get("task_description"),
            "guardrail": result.get("guardrail", {}),
            "permission": result.get("permission", {}),
        })
        self.hub.task_manager.update_task_recorder(
            state["task_id"],
            TASK_STATUS_FINISH,
            TASK_SYS_OUTPUT_STOP + result.get("task_description", "LangGraph workflow blocked"),
            graph_title=GRAPH_TITLE_FAILURE,
        )
        if task is not None:
            self.hub.trace_manager.finish_trace(
                task.trace_id,
                result.get("task_description", ""),
                status=block_event,
            )
        return {
            **state,
            "final_status": block_event,
            "next_action": "end",
        }

    def _persist_tool_execution_gate(
        self,
        state: ERPGraphState,
        task: Any,
        tool: Any,
        result: Dict[str, Any],
    ) -> ERPGraphState:
        raw_query = state.get("raw_query") or state.get("query", "")
        task_desc = state.get("task_desc") or state.get("query", "")
        self._trace(task, "params_extracted", {
            "tool_id": getattr(tool, "tool_id", None),
            "params": result.get("param", {}),
            "guardrail": result.get("guardrail", {}),
        })
        self._trace(task, "langgraph_human_gate_created", {
            "gate": "tool_execution_confirm",
            "tool_id": getattr(tool, "tool_id", None),
            "operation_id": getattr(tool, "operationId", ""),
            "params": result.get("param", {}),
        })
        system_output = (
            f"LangGraph selected tool [{getattr(tool, 'name_for_human', '')}] "
            f"for request [{raw_query}] with params {result.get('param', {})}. "
            "Please confirm execution, or reply cancel/no to stop this task."
        )
        self.hub.task_manager.update_task_recorder(
            state["task_id"],
            TASK_STATUS_WAIT_CONFIRM,
            system_output,
            graph_title="Confirm execution",
            curr_task_desc=task_desc,
            curr_tool_id=getattr(tool, "tool_id", 0),
            curr_tool_param=result.get("param", {}),
            pending_action="tool_execution_confirm",
            pending_payload={
                "graph": "langgraph_erp_agent",
                "thread_id": state["task_id"],
                "tool_id": getattr(tool, "tool_id", None),
                "tool_name": getattr(tool, "name_for_human", ""),
                "operation_id": getattr(tool, "operationId", ""),
                "params": result.get("param", {}),
                "task_desc": task_desc,
                "raw_query": raw_query,
                "permission": result.get("permission", {}),
            },
        )
        return {
            **state,
            "final_status": "waiting_human",
            "next_action": "end",
        }

    def _persist_missing_param_gate(
        self,
        state: ERPGraphState,
        task: Any,
        tool: Any,
        result: Dict[str, Any],
    ) -> ERPGraphState:
        task_desc = state.get("task_desc") or state.get("query", "")
        raw_query = state.get("raw_query") or state.get("query", "")
        missing_params = result.get("missing_param", [])
        missing_names = ", ".join(
            str(item.get("description") or item.get("name"))
            for item in missing_params
        )
        self._trace(task, "missing_params_need_user", {
            "tool_id": getattr(tool, "tool_id", None),
            "operation_id": getattr(tool, "operationId", ""),
            "known_params": result.get("param", {}),
            "missing_params": missing_params,
        })
        self._trace(task, "langgraph_human_gate_created", {
            "gate": "missing_params_clarify",
            "tool_id": getattr(tool, "tool_id", None),
            "operation_id": getattr(tool, "operationId", ""),
        })
        self.hub.task_manager.update_task_recorder(
            state["task_id"],
            TASK_STATUS_WAIT_CONFIRM,
            f"Missing required information: {missing_names}. Please provide it directly, or cancel this task.",
            graph_title="Waiting for missing parameters",
            curr_task_desc=task_desc,
            curr_tool_id=getattr(tool, "tool_id", 0),
            curr_tool_param=result.get("param", {}),
            pending_action="missing_params_clarify",
            pending_payload={
                "graph": "langgraph_erp_agent",
                "thread_id": state["task_id"],
                "original_query": raw_query,
                "current_task_desc": task_desc,
                "tool_id": getattr(tool, "tool_id", None),
                "tool_name": getattr(tool, "name_for_human", ""),
                "operation_id": getattr(tool, "operationId", ""),
                "known_params": result.get("param", {}),
                "missing_params": missing_params,
            },
        )
        return {
            **state,
            "final_status": "waiting_human",
            "next_action": "end",
        }

    def _route_after_load(self, state: ERPGraphState) -> str:
        return "end" if state.get("next_action") == "end" else "classify_task"

    def _route_after_select(self, state: ERPGraphState) -> str:
        return "end" if state.get("next_action") == "end" else "check_tool"

    def _task(self, state: ERPGraphState) -> Optional[Any]:
        task_id = state.get("task_id")
        if not task_id:
            return None
        return self.hub.task_manager.get_task_by_id(task_id)

    def _tool(self, state: ERPGraphState) -> Optional[Any]:
        tool_id = state.get("tool_id")
        if not tool_id:
            return None
        tools = self.hub.tool_manager.get_tools_by_ids([tool_id])
        return tools[0] if tools else None

    def _trace(self, task: Any, event_type: str, payload: Dict[str, Any]) -> None:
        if task is None or not getattr(task, "trace_id", ""):
            return
        self.hub.trace_manager.add_event(task.trace_id, event_type, payload)
