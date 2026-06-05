import argparse
import copy
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from prompt.evals.integration_runner import (
    evaluate_dataset,
    enforce_thresholds,
    load_dataset,
    values_equal,
)


INTEGRATION_DATASET_DIR = Path(__file__).resolve().parent / "integration_datasets"
DEFAULT_DATASET = INTEGRATION_DATASET_DIR / "online_workflows.json"

TASK_STATUS_WAIT_CONFIRM = 100
TASK_STATUS_FINISH = -1


class OnlineIntegrationClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def post_json(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib_request.Request(url, data=body, headers=self.headers, method="POST")
        try:
            with urllib_request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text else {}
        except HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} from {path}: {text}") from exc
        except URLError as exc:
            raise RuntimeError(f"Cannot reach {url}: {exc}") from exc

    def start_task(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.post_json("/api_planning", payload)

    def send_feedback(self, task_id: str, feedback: str) -> Dict[str, Any]:
        return self.post_json("/api_planning", {"taskId": task_id, "query": feedback})

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        data = self.post_json("/api_task_status", {"task_id": task_id})
        return data.get("task") or {}

    def get_trace(self, task_id: str = "", trace_id: str = "") -> Dict[str, Any]:
        data = self.post_json("/api_trace_status", {"task_id": task_id, "trace_id": trace_id})
        return data.get("trace") or {}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _extract_quantity(*values: Any) -> Optional[int]:
    text = " ".join(str(value or "") for value in values)
    patterns = [
        r"数量\D{0,8}(\d+)",
        r"(\d+)\s*件",
        r"quantity['\"]?\s*[:=]\s*(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _missing_names(value: Any) -> List[str]:
    names = []
    for item in value or []:
        if isinstance(item, dict):
            names.append(str(item.get("name") or item.get("param") or item.get("description") or ""))
        else:
            names.append(str(item))
    return [name for name in names if name]


def _event_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _event_type(event: Dict[str, Any]) -> str:
    return str(event.get("event_type") or event.get("type") or "")


def _append_call_once(
    calls: List[Dict[str, Any]],
    seen: set,
    tool: str,
    params: Dict[str, Any],
    status: str = "success",
    confirmed: Optional[bool] = None,
) -> None:
    signature = (tool, _stable_json(params))
    if signature in seen:
        return
    seen.add(signature)
    call = {"tool": tool, "params": params, "status": status}
    if confirmed is not None:
        call["confirmed"] = confirmed
    calls.append(call)


def _pending_params(pending_action: str, pending_payload: Dict[str, Any]) -> Dict[str, Any]:
    if pending_action == "tool_execution_confirm":
        params = pending_payload.get("params")
        return params if isinstance(params, dict) else {}
    if pending_action == "missing_params_clarify":
        params = dict(pending_payload.get("known_params") or {})
        missing = _missing_names(pending_payload.get("missing_params"))
        if missing:
            params["missing"] = missing
        return params
    if pending_action == "ambiguity_confirm":
        candidate = pending_payload.get("candidate") or {}
        params = candidate if isinstance(candidate, dict) else {}
        quantity = _extract_quantity(
            pending_payload.get("resolved_query"),
            pending_payload.get("original_query"),
            candidate,
        )
        if quantity is not None:
            params = dict(params)
            params["quantity"] = quantity
        return params
    if pending_action == "rewrite_grounding_clarify":
        return {}
    return {}


def _pending_virtual_tool(pending_action: str) -> str:
    if pending_action == "missing_params_clarify":
        return "ask_user_clarification"
    if pending_action == "rewrite_grounding_clarify":
        return "ask_user_clarification"
    return "ask_user_confirmation"


def _pending_operation(pending_action: str, pending_payload: Dict[str, Any]) -> str:
    if pending_action == "ambiguity_confirm":
        return "resolve_ambiguity"
    if pending_action == "rewrite_grounding_clarify":
        return "ask_user_clarification"
    return str(pending_payload.get("operation_id") or pending_payload.get("tool_name") or "")


def _apply_hitl_intent(
    hitl_events: List[Dict[str, Any]],
    feedback: str,
    intent: str,
    handled: bool = True,
) -> None:
    for event in reversed(hitl_events):
        if event.get("intent"):
            continue
        if feedback and event.get("feedback") and event.get("feedback") != feedback:
            continue
        event["intent"] = intent
        event["handled"] = handled
        return


def _append_trace_events(
    events: List[Dict[str, Any]],
    start: int,
    calls: List[Dict[str, Any]],
    hitl_events: List[Dict[str, Any]],
    seen_virtual: set,
    confirm_for_next_tool: bool,
) -> Tuple[int, bool]:
    for event in events[start:]:
        event_type = _event_type(event)
        payload = _event_payload(event)

        if event_type == "ambiguity_detected":
            quantity = _extract_quantity(
                payload.get("resolved_query"),
                payload.get("original_query"),
                payload.get("candidate"),
            )
            params = {"quantity": quantity} if quantity is not None else {}
            _append_call_once(calls, seen_virtual, "resolve_ambiguity", params)

        elif event_type == "rewrite_grounding_failed":
            _append_call_once(calls, seen_virtual, "ask_user_clarification", {})

        elif event_type == "missing_params_need_user":
            params = dict(payload.get("known_params") or {})
            missing = _missing_names(payload.get("missing_params"))
            if missing:
                params["missing"] = missing
            _append_call_once(calls, seen_virtual, "ask_user_clarification", params)

        elif event_type == "guardrail_blocked":
            guardrail = payload.get("guardrail") or {}
            params = {
                "reason": payload.get("reason")
                or guardrail.get("reason")
                or guardrail.get("hallucination_type")
                or "guardrail_blocked"
            }
            _append_call_once(calls, seen_virtual, "guardrail_block", params)

        elif event_type == "human_feedback_intent":
            intent = str(payload.get("intent") or "")
            feedback = str(payload.get("feedback") or "")
            _apply_hitl_intent(hitl_events, feedback, intent, handled=True)
            if intent == "confirm":
                confirm_for_next_tool = True

        elif event_type == "missing_params_feedback_parsed":
            parse = payload.get("parse") or {}
            _apply_hitl_intent(
                hitl_events,
                str(payload.get("feedback") or ""),
                str(parse.get("intent") or "provide_info"),
                handled=True,
            )

        elif event_type == "missing_params_feedback_unclear":
            _apply_hitl_intent(
                hitl_events,
                str(payload.get("feedback") or ""),
                "unclear",
                handled=True,
            )

        elif event_type == "missing_params_aborted":
            _apply_hitl_intent(hitl_events, str(payload.get("feedback") or ""), "abort", handled=True)

        elif event_type == "ambiguity_resolved":
            resolution_type = str(payload.get("resolution_type") or "")
            intent = "confirm_candidate" if "confirm" in resolution_type else "provide_info"
            _apply_hitl_intent(hitl_events, str(payload.get("feedback") or ""), intent, handled=True)

        elif event_type == "ambiguity_feedback_unclear":
            _apply_hitl_intent(hitl_events, str(payload.get("feedback") or ""), "unclear", handled=True)

        elif event_type == "ambiguity_aborted":
            _apply_hitl_intent(hitl_events, str(payload.get("feedback") or ""), "abort", handled=True)

        elif event_type == "rewrite_grounding_resolved":
            _apply_hitl_intent(hitl_events, str(payload.get("feedback") or ""), "provide_info", handled=True)

        elif event_type == "rewrite_grounding_aborted":
            _apply_hitl_intent(hitl_events, str(payload.get("feedback") or ""), "abort", handled=True)

        elif event_type == "tool_invocation_started":
            calls.append(
                {
                    "tool": payload.get("operation_id") or payload.get("tool") or payload.get("tool_name"),
                    "params": payload.get("params") or {},
                    "status": "started",
                    "confirmed": confirm_for_next_tool,
                }
            )
            confirm_for_next_tool = False

        elif event_type == "tool_invocation_finished":
            tool = payload.get("operation_id") or payload.get("tool") or payload.get("tool_name")
            status_code = payload.get("status_code")
            status = "success" if str(status_code) == "200" else "exception"
            for call in reversed(calls):
                if call.get("tool") == tool and call.get("status") == "started":
                    call["status"] = status
                    call["result_summary"] = payload.get("result_summary", "")
                    if status == "exception":
                        call["exception_handled"] = True
                    break
            if status == "exception":
                _append_call_once(
                    calls,
                    seen_virtual,
                    "report_tool_exception",
                    {"failedTool": tool},
                    status="success",
                )

    return len(events), confirm_for_next_tool


def _match_pending_expected(
    expected: Optional[Dict[str, Any]],
    pending_action: str,
    pending_payload: Dict[str, Any],
    pending_params: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    if not expected:
        return False, ["no simulated human feedback left for pending action"]

    errors = []
    expected_when = _norm(expected.get("when"))
    if expected_when and expected_when != _norm(pending_action):
        errors.append(f"expected pending action {expected_when}, got {pending_action}")

    expected_tool = _norm(expected.get("expected_tool"))
    if expected_tool:
        candidates = {
            _norm(pending_payload.get("operation_id")),
            _norm(pending_payload.get("tool_name")),
            _norm(_pending_operation(pending_action, pending_payload)),
            _norm(_pending_virtual_tool(pending_action)),
        }
        if expected_tool not in candidates:
            errors.append(f"expected pending tool {expected_tool}, got {sorted(candidates)}")

    for name, expected_value in (expected.get("expected_params") or {}).items():
        actual_value = pending_params.get(name)
        if not values_equal(expected_value, actual_value):
            errors.append(f"expected pending param {name}={expected_value}, got {actual_value}")

    expected_missing = _missing_names(expected.get("expected_missing_params"))
    actual_missing = _missing_names(pending_payload.get("missing_params")) or _missing_names(
        pending_params.get("missing")
    )
    for name in expected_missing:
        if name not in actual_missing:
            errors.append(f"expected missing param {name}, got {actual_missing}")

    return not errors, errors


def _normalize_status(task: Dict[str, Any], trace: Dict[str, Any]) -> str:
    pending_action = task.get("pendingAction")
    if pending_action:
        return "waiting_user"

    final_text = f"{task.get('systemOutput') or ''}\n{trace.get('final_answer') or ''}"
    trace_status = _norm(trace.get("status"))
    if trace_status == "aborted":
        return "aborted"
    if trace_status == "guardrail_blocked":
        return "guardrail_blocked"
    if "循环" in final_text or "loop" in final_text.lower():
        return "loop_stopped"
    if "不属于 erp" in final_text.lower() or "未找到合适" in final_text or "无法调用业务工具" in final_text:
        return "rejected"
    if trace_status == "failed":
        return "failed_handled"
    if any(token in final_text for token in ["失败", "错误", "timeout", "异常"]):
        return "failed_handled"

    raw_status = task.get("status")
    if raw_status == TASK_STATUS_FINISH or trace_status == "finished":
        return "completed"
    if raw_status == TASK_STATUS_WAIT_CONFIRM:
        return "waiting_user"
    return str(raw_status or trace_status or "unknown")


class OnlineIntegrationRunner:
    def __init__(self, client: OnlineIntegrationClient, poll_interval: float, timeout: float):
        self.client = client
        self.poll_interval = poll_interval
        self.timeout = timeout

    def run_case(self, case: Dict[str, Any]) -> Dict[str, Any]:
        case_result = copy.deepcopy(case)
        aggregate = {
            "task_status": "unknown",
            "tool_calls": [],
            "hitl_events": [],
            "used_result_facts": [],
            "final_answer": "",
            "exception_handled": False,
            "runner_errors": [],
            "raw_tasks": [],
            "raw_traces": [],
        }

        turns = case.get("turns")
        if not turns:
            turns = [
                {
                    "request": case.get("request") or {"query": case.get("natural_language", "")},
                    "human_simulation": case.get("human_simulation", []),
                }
            ]

        session_id = None
        for turn_index, turn in enumerate(turns):
            request_payload = self._build_request(case, turn, turn_index, session_id)
            session_id = request_payload.get("sessionId") or request_payload.get("session_id") or session_id
            actual = self._run_turn(request_payload, turn.get("human_simulation", []))
            aggregate["tool_calls"].extend(actual["tool_calls"])
            aggregate["hitl_events"].extend(actual["hitl_events"])
            aggregate["runner_errors"].extend(actual["runner_errors"])
            aggregate["raw_tasks"].extend(actual["raw_tasks"])
            aggregate["raw_traces"].extend(actual["raw_traces"])
            aggregate["final_answer"] = actual.get("final_answer", "") or aggregate["final_answer"]
            aggregate["task_status"] = actual.get("task_status", aggregate["task_status"])
            aggregate["exception_handled"] = aggregate["exception_handled"] or actual.get(
                "exception_handled", False
            )

        expected_facts = case.get("expected", {}).get("required_result_facts", [])
        final_answer = str(aggregate.get("final_answer", ""))
        aggregate["used_result_facts"] = [
            str(fact) for fact in expected_facts if str(fact) and str(fact) in final_answer
        ]
        case_result["actual_trace"] = aggregate
        return case_result

    def _build_request(
        self,
        case: Dict[str, Any],
        turn: Dict[str, Any],
        turn_index: int,
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        payload = {
            "query": case.get("natural_language", ""),
            "contexts": [],
            "isCopilot": True,
            "isContext": False,
            "sessionId": session_id or f"eval-{case.get('id', 'case')}-{uuid.uuid4()}",
        }
        payload.update(turn.get("request") or {})
        payload.setdefault("query", case.get("natural_language", ""))
        if "sessionId" not in payload and "session_id" not in payload:
            payload["sessionId"] = session_id or f"eval-{case.get('id', 'case')}-{turn_index}-{uuid.uuid4()}"
        return payload

    def _run_turn(self, request_payload: Dict[str, Any], human_steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        response = self.client.start_task(request_payload)
        task_id = response.get("task_id") or response.get("taskId")
        trace_id = response.get("trace_id") or response.get("traceId") or ""
        if not task_id:
            raise RuntimeError(f"/api_planning did not return task_id: {response}")

        calls: List[Dict[str, Any]] = []
        hitl_events: List[Dict[str, Any]] = []
        runner_errors: List[str] = []
        raw_tasks: List[Dict[str, Any]] = []
        raw_traces: List[Dict[str, Any]] = []
        seen_virtual = set()
        seen_pending = set()
        processed_event_count = 0
        confirm_for_next_tool = False
        human_index = 0
        deadline = time.time() + self.timeout
        last_task: Dict[str, Any] = {}
        last_trace: Dict[str, Any] = {}

        while time.time() < deadline:
            last_task = self.client.get_task_status(task_id)
            try:
                last_trace = self.client.get_trace(task_id=task_id, trace_id=trace_id)
            except RuntimeError as exc:
                last_trace = {}
                runner_errors.append(str(exc))

            raw_tasks.append(last_task)
            if last_trace:
                raw_traces.append(last_trace)
                events = last_trace.get("events") or []
                processed_event_count, confirm_for_next_tool = _append_trace_events(
                    events,
                    processed_event_count,
                    calls,
                    hitl_events,
                    seen_virtual,
                    confirm_for_next_tool,
                )

            pending_action = last_task.get("pendingAction") or ""
            if last_task.get("status") == TASK_STATUS_WAIT_CONFIRM or pending_action:
                pending_payload = last_task.get("pendingPayload") or {}
                pending_params = _pending_params(pending_action, pending_payload)
                virtual_tool = _pending_virtual_tool(pending_action)
                pending_signature = (pending_action, virtual_tool, _stable_json(pending_params))
                if pending_signature not in seen_pending:
                    seen_pending.add(pending_signature)
                    _append_call_once(calls, seen_virtual, virtual_tool, pending_params)

                step = human_steps[human_index] if human_index < len(human_steps) else None
                matched, errors = _match_pending_expected(step, pending_action, pending_payload, pending_params)
                event = {
                    "pending_action": pending_action,
                    "tool": _pending_operation(pending_action, pending_payload),
                    "operation_id": pending_payload.get("operation_id", ""),
                    "tool_name": pending_payload.get("tool_name", ""),
                    "virtual_tool": virtual_tool,
                    "params": pending_params,
                    "missing_params": _missing_names(pending_payload.get("missing_params")),
                    "feedback": step.get("feedback", "") if step else "",
                    "matched": matched,
                    "handled": False,
                    "errors": errors,
                }
                hitl_events.append(event)

                if not step:
                    break
                if not matched:
                    runner_errors.extend(errors)
                    break
                if step.get("feedback") is None:
                    event["handled"] = True
                    break

                self.client.send_feedback(task_id, str(step.get("feedback", "")))
                human_index += 1
                time.sleep(self.poll_interval)
                continue

            if last_task.get("status") == TASK_STATUS_FINISH:
                break

            time.sleep(self.poll_interval)

        else:
            runner_errors.append(f"timeout waiting for task {task_id}")

        if last_trace:
            events = last_trace.get("events") or []
            processed_event_count, confirm_for_next_tool = _append_trace_events(
                events,
                processed_event_count,
                calls,
                hitl_events,
                seen_virtual,
                confirm_for_next_tool,
            )

        for event in hitl_events:
            if not event.get("intent") and event.get("matched") and not event.get("errors"):
                event["handled"] = event.get("handled", False)

        final_answer = str(last_trace.get("final_answer") or last_task.get("systemOutput") or "")
        exception_handled = any(call.get("exception_handled") for call in calls) or any(
            call.get("tool") == "report_tool_exception" for call in calls
        )
        return {
            "task_status": _normalize_status(last_task, last_trace),
            "tool_calls": calls,
            "hitl_events": hitl_events,
            "used_result_facts": [],
            "final_answer": final_answer,
            "exception_handled": exception_handled,
            "runner_errors": runner_errors,
            "raw_tasks": raw_tasks[-3:],
            "raw_traces": raw_traces[-1:],
        }


def _case_summary(dataset: Dict[str, Any], selected: List[Dict[str, Any]]) -> Dict[str, Any]:
    categories: Dict[str, int] = {}
    for case in selected:
        category = case.get("category", "uncategorized")
        categories[category] = categories.get(category, 0) + 1
    return {
        "dataset": dataset.get("name"),
        "total_cases": len(selected),
        "categories": categories,
        "case_ids": [case.get("id") for case in selected],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run online end-to-end ERP Agent integration evals.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--base-url", default=os.getenv("ERP_AGENT_BASE_URL", ""))
    parser.add_argument("--token", default=os.getenv("ERP_AGENT_TEST_TOKEN", ""))
    parser.add_argument("--output", default="")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--request-timeout", type=float, default=20.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-task-accuracy", type=float, default=None)
    parser.add_argument("--min-completion", type=float, default=None)
    parser.add_argument("--max-invalid-tool-call-rate", type=float, default=None)
    args = parser.parse_args()

    dataset = load_dataset(Path(args.dataset))
    cases = dataset.get("cases", [])
    if args.case_id:
        selected_ids = set(args.case_id)
        cases = [case for case in cases if case.get("id") in selected_ids]
    dataset = dict(dataset)
    dataset["cases"] = cases

    if args.dry_run:
        print(json.dumps(_case_summary(dataset, cases), ensure_ascii=False, indent=2))
        return

    if not args.base_url:
        raise SystemExit("--base-url is required for online integration tests")

    client = OnlineIntegrationClient(args.base_url, token=args.token, timeout=args.request_timeout)
    runner = OnlineIntegrationRunner(client, args.poll_interval, args.timeout)
    evaluated_cases = []
    for case in cases:
        try:
            evaluated_cases.append(runner.run_case(case))
        except Exception as exc:
            failed = copy.deepcopy(case)
            failed["actual_trace"] = {
                "task_status": "runner_error",
                "tool_calls": [],
                "hitl_events": [],
                "used_result_facts": [],
                "final_answer": str(exc),
                "runner_errors": [str(exc)],
            }
            evaluated_cases.append(failed)

    report_dataset = dict(dataset)
    report_dataset["cases"] = evaluated_cases
    report = evaluate_dataset(report_dataset)
    report["mode"] = "online_e2e"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")

    enforce_thresholds(report, args)


if __name__ == "__main__":
    main()
