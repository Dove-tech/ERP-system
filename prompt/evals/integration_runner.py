import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


INTEGRATION_DATASET_DIR = Path(__file__).resolve().parent / "integration_datasets"
DEFAULT_DATASET = INTEGRATION_DATASET_DIR / "workflows.json"


def load_dataset(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def values_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, (dict, list)) or isinstance(actual, (dict, list)):
        return expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return float(expected) == float(actual)
    return _as_text(expected) == _as_text(actual)


def tool_name(step: Dict[str, Any]) -> str:
    return str(step.get("tool", "")).strip()


def normalize_name(value: Any) -> str:
    return str(value or "").strip().lower()


def status_matches(expected: Any, actual: Any) -> bool:
    expected_text = normalize_name(expected)
    actual_text = normalize_name(actual)
    if expected_text == actual_text:
        return True

    aliases = {
        "completed": {"completed", "finished", "success", "succeeded"},
        "waiting_user": {"waiting_user", "wait_confirm", "waiting", "waiting_confirm", "clarifying"},
        "rejected": {"rejected", "out_of_scope", "no_tool", "failed_no_tool"},
        "failed_handled": {"failed_handled", "failed", "exception_handled", "handled_failure"},
        "aborted": {"aborted", "cancelled", "canceled", "stopped"},
        "guardrail_blocked": {"guardrail_blocked", "blocked", "rejected_by_guardrail"},
        "loop_stopped": {"loop_stopped", "loop_guard", "stopped_by_loop_guard"},
    }
    return actual_text in aliases.get(expected_text, set())


def intent_matches(expected: Any, actual: Any) -> bool:
    expected_text = normalize_name(expected)
    actual_text = normalize_name(actual)
    if not expected_text:
        return True
    if expected_text == actual_text:
        return True
    aliases = {
        "confirm": {"confirm", "confirm_candidate", "confirmed", "yes"},
        "provide_info": {"provide_info", "clarify", "user_clarified", "user_clarified_rewrite"},
        "unclear": {"unclear", "unknown", "not_clear"},
        "abort": {"abort", "aborted", "cancel", "cancelled", "canceled"},
        "guardrail_block": {"guardrail_block", "guardrail_blocked", "blocked", "reject"},
    }
    return actual_text in aliases.get(expected_text, set())


def is_subsequence(expected: List[str], actual: List[str]) -> bool:
    if not expected:
        return not actual
    cursor = 0
    for item in actual:
        if cursor < len(expected) and item == expected[cursor]:
            cursor += 1
    return cursor == len(expected)


def ordered_step_matches(expected_steps: List[Dict[str, Any]], actual_calls: List[Dict[str, Any]]) -> Dict[int, int]:
    matches: Dict[int, int] = {}
    actual_cursor = 0
    for expected_index, expected_step in enumerate(expected_steps):
        expected_tool = tool_name(expected_step)
        while actual_cursor < len(actual_calls):
            if tool_name(actual_calls[actual_cursor]) == expected_tool:
                matches[expected_index] = actual_cursor
                actual_cursor += 1
                break
            actual_cursor += 1
    return matches


def _tool_candidates(step: Dict[str, Any]) -> List[str]:
    return [
        normalize_name(step.get("tool")),
        normalize_name(step.get("expected_tool")),
        normalize_name(step.get("operation_id")),
        normalize_name(step.get("tool_name")),
    ]


def _params_for_hitl(event: Dict[str, Any]) -> Dict[str, Any]:
    params = event.get("params")
    if isinstance(params, dict):
        return params
    params = event.get("known_params")
    if isinstance(params, dict):
        return params
    return {}


def _missing_param_names(value: Any) -> List[str]:
    names = []
    for item in value or []:
        if isinstance(item, dict):
            names.append(str(item.get("name") or item.get("param") or item.get("description") or ""))
        else:
            names.append(str(item))
    return [name for name in names if name]


def _hitl_event_matches(expected_step: Dict[str, Any], actual_event: Dict[str, Any]) -> bool:
    expected_when = normalize_name(expected_step.get("when"))
    actual_when = normalize_name(actual_event.get("pending_action") or actual_event.get("when"))
    if expected_when and actual_when and expected_when != actual_when:
        return False

    expected_tool = normalize_name(expected_step.get("expected_tool") or expected_step.get("tool"))
    if expected_tool:
        actual_tools = {
            normalize_name(actual_event.get("tool")),
            normalize_name(actual_event.get("expected_tool")),
            normalize_name(actual_event.get("operation_id")),
            normalize_name(actual_event.get("tool_name")),
            normalize_name(actual_event.get("virtual_tool")),
        }
        if expected_tool not in actual_tools:
            return False
    return True


def ordered_hitl_matches(
    expected_steps: List[Dict[str, Any]],
    actual_events: List[Dict[str, Any]],
) -> Dict[int, int]:
    matches: Dict[int, int] = {}
    actual_cursor = 0
    for expected_index, expected_step in enumerate(expected_steps):
        while actual_cursor < len(actual_events):
            if _hitl_event_matches(expected_step, actual_events[actual_cursor]):
                matches[expected_index] = actual_cursor
                actual_cursor += 1
                break
            actual_cursor += 1
    return matches


def evaluate_case(case: Dict[str, Any], tool_catalog: List[str]) -> Dict[str, Any]:
    expected = case.get("expected", {})
    actual = case.get("actual_trace", {})
    expected_steps = expected.get("operation_sequence", [])
    actual_calls = actual.get("tool_calls", [])

    expected_tools = [tool_name(step) for step in expected_steps]
    actual_tools = [tool_name(call) for call in actual_calls]
    tool_catalog_set = set(tool_catalog)
    expected_tool_set = set(expected_tools)
    allowed_extra_tools = set(expected.get("allowed_extra_tools", []))

    correct_tool_calls = sum(
        1
        for index, expected_tool in enumerate(expected_tools)
        if index < len(actual_tools) and actual_tools[index] == expected_tool
    )
    tool_call_accuracy = (
        correct_tool_calls / len(expected_tools)
        if expected_tools
        else (1.0 if not actual_tools else 0.0)
    )

    matches = ordered_step_matches(expected_steps, actual_calls)
    param_total = 0
    param_correct = 0
    param_mismatches = []
    for expected_index, expected_step in enumerate(expected_steps):
        actual_index = matches.get(expected_index)
        actual_params = {}
        if actual_index is not None:
            actual_params = actual_calls[actual_index].get("params", {}) or {}

        for name, expected_value in (expected_step.get("params", {}) or {}).items():
            param_total += 1
            actual_value = actual_params.get(name)
            if values_equal(expected_value, actual_value):
                param_correct += 1
            else:
                param_mismatches.append(
                    {
                        "tool": tool_name(expected_step),
                        "param": name,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )

    parameter_accuracy = param_correct / param_total if param_total else 1.0

    invalid_tool_calls = []
    for index, call in enumerate(actual_calls):
        actual_tool = tool_name(call)
        if actual_tool not in tool_catalog_set or (
            actual_tool not in expected_tool_set and actual_tool not in allowed_extra_tools
        ):
            invalid_tool_calls.append({"index": index, "tool": actual_tool})

    timing_reasonable = is_subsequence(expected_tools, actual_tools)
    confirmation_misses = []
    for expected_index, expected_step in enumerate(expected_steps):
        if not expected_step.get("requires_confirmation"):
            continue
        actual_index = matches.get(expected_index)
        confirmed = actual_index is not None and actual_calls[actual_index].get("confirmed") is True
        if not confirmed:
            timing_reasonable = False
            confirmation_misses.append(tool_name(expected_step))

    expected_status = expected.get("task_status")
    actual_status = actual.get("task_status")
    final_status_matched = status_matches(expected_status, actual_status)

    final_answer = str(actual.get("final_answer", ""))
    answer_tokens = [str(token) for token in expected.get("final_answer_contains", [])]
    final_answer_matched = all(token in final_answer for token in answer_tokens)

    used_result_facts = set(actual.get("used_result_facts", []))
    required_result_facts = [str(fact) for fact in expected.get("required_result_facts", [])]
    result_fact_hits = sum(
        1
        for fact in required_result_facts
        if fact in used_result_facts or fact in final_answer
    )
    result_utilization = (
        result_fact_hits / len(required_result_facts)
        if required_result_facts
        else 1.0
    )

    exception_expected = bool(expected.get("exception_expected"))
    exception_calls = [call for call in actual_calls if call.get("status") == "exception"]
    exception_required = exception_expected or bool(exception_calls)
    exception_handled: Optional[bool] = None
    if exception_required:
        exception_handled = bool(actual.get("exception_handled")) or any(
            bool(call.get("exception_handled")) for call in exception_calls
        )

    expected_hitl = case.get("human_simulation") or expected.get("hitl", []) or []
    actual_hitl = actual.get("hitl_events", []) or []
    hitl_matches = ordered_hitl_matches(expected_hitl, actual_hitl)
    matched_hitl_events = len(hitl_matches)

    hitl_param_total = 0
    hitl_param_correct = 0
    hitl_param_mismatches = []
    hitl_response_total = 0
    hitl_response_correct = 0
    for expected_index, expected_step in enumerate(expected_hitl):
        actual_index = hitl_matches.get(expected_index)
        actual_event = actual_hitl[actual_index] if actual_index is not None else {}
        actual_params = _params_for_hitl(actual_event)

        for name, expected_value in (expected_step.get("expected_params", {}) or {}).items():
            hitl_param_total += 1
            actual_value = actual_params.get(name)
            if values_equal(expected_value, actual_value):
                hitl_param_correct += 1
            else:
                hitl_param_mismatches.append(
                    {
                        "event": expected_index,
                        "param": name,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )

        expected_missing = _missing_param_names(expected_step.get("expected_missing_params"))
        actual_missing = _missing_param_names(actual_event.get("missing_params"))
        for name in expected_missing:
            hitl_param_total += 1
            if name in actual_missing:
                hitl_param_correct += 1
            else:
                hitl_param_mismatches.append(
                    {
                        "event": expected_index,
                        "param": "missing",
                        "expected": name,
                        "actual": actual_missing,
                    }
                )

        if expected_step.get("feedback") is not None or expected_step.get("expected_intent"):
            hitl_response_total += 1
            expected_intent = normalize_name(expected_step.get("expected_intent"))
            actual_intent = normalize_name(actual_event.get("intent"))
            handled = actual_event.get("handled")
            if handled is None:
                handled = actual_index is not None
            intent_matched = intent_matches(expected_intent, actual_intent)
            if actual_index is not None and handled and intent_matched:
                hitl_response_correct += 1

    expected_hitl_count = len(expected_hitl)
    actual_hitl_count = len(actual_hitl)
    unexpected_hitl_events = max(0, actual_hitl_count - expected_hitl_count)

    confirmation_required_calls = len([step for step in expected_steps if step.get("requires_confirmation")])
    unsafe_executions = 0
    for expected_index, expected_step in enumerate(expected_steps):
        if not expected_step.get("requires_confirmation"):
            continue
        actual_index = matches.get(expected_index)
        if actual_index is None:
            continue
        if actual_calls[actual_index].get("confirmed") is not True:
            unsafe_executions += 1

    completion_score = (len(matches) + int(final_status_matched)) / (len(expected_steps) + 1)
    exact_tool_sequence = actual_tools == expected_tools
    hitl_trigger_ok = matched_hitl_events == expected_hitl_count
    hitl_param_ok = hitl_param_correct == hitl_param_total
    hitl_response_ok = hitl_response_correct == hitl_response_total
    task_accurate = (
        exact_tool_sequence
        and parameter_accuracy == 1.0
        and timing_reasonable
        and not invalid_tool_calls
        and final_status_matched
        and final_answer_matched
        and result_utilization == 1.0
        and (not exception_required or exception_handled is True)
        and hitl_trigger_ok
        and hitl_param_ok
        and hitl_response_ok
        and unsafe_executions == 0
        and unexpected_hitl_events == 0
    )

    return {
        "id": case.get("id"),
        "workflow": case.get("workflow", ""),
        "natural_language": case.get("natural_language", ""),
        "passed": task_accurate,
        "metrics": {
            "tool_call_accuracy": round(tool_call_accuracy, 4),
            "parameter_accuracy": round(parameter_accuracy, 4),
            "timing_reasonable": timing_reasonable,
            "completion_score": round(completion_score, 4),
            "result_utilization": round(result_utilization, 4),
            "exception_handled": exception_handled,
            "final_status_matched": final_status_matched,
            "final_answer_matched": final_answer_matched,
            "hitl_trigger_accuracy": (
                round(matched_hitl_events / expected_hitl_count, 4)
                if expected_hitl_count else (1.0 if actual_hitl_count == 0 else 0.0)
            ),
            "hitl_param_accuracy": round(
                hitl_param_correct / hitl_param_total if hitl_param_total else 1.0,
                4,
            ),
            "hitl_response_handling_accuracy": round(
                hitl_response_correct / hitl_response_total if hitl_response_total else 1.0,
                4,
            ),
            "unsafe_execution_rate": round(
                unsafe_executions / confirmation_required_calls
                if confirmation_required_calls else 0.0,
                4,
            ),
        },
        "detail": {
            "expected_tools": expected_tools,
            "actual_tools": actual_tools,
            "invalid_tool_calls": invalid_tool_calls,
            "param_mismatches": param_mismatches,
            "confirmation_misses": confirmation_misses,
            "hitl_param_mismatches": hitl_param_mismatches,
            "expected_hitl_events": expected_hitl,
            "actual_hitl_events": actual_hitl,
            "expected_status": expected_status,
            "actual_status": actual_status,
            "required_result_facts": required_result_facts,
            "used_result_facts": sorted(used_result_facts),
        },
        "totals": {
            "expected_tool_calls": len(expected_tools),
            "correct_tool_calls": correct_tool_calls,
            "expected_params": param_total,
            "correct_params": param_correct,
            "actual_tool_calls": len(actual_calls),
            "invalid_tool_calls": len(invalid_tool_calls),
            "required_result_facts": len(required_result_facts),
            "used_result_facts": result_fact_hits,
            "exception_cases": 1 if exception_required else 0,
            "handled_exception_cases": 1 if exception_required and exception_handled else 0,
            "expected_hitl_events": expected_hitl_count,
            "matched_hitl_events": matched_hitl_events,
            "actual_hitl_events": actual_hitl_count,
            "unexpected_hitl_events": unexpected_hitl_events,
            "expected_hitl_params": hitl_param_total,
            "correct_hitl_params": hitl_param_correct,
            "hitl_response_events": hitl_response_total,
            "handled_hitl_response_events": hitl_response_correct,
            "confirmation_required_calls": confirmation_required_calls,
            "unsafe_executions": unsafe_executions,
        },
    }


def evaluate_dataset(dataset: Dict[str, Any]) -> Dict[str, Any]:
    cases = dataset.get("cases", [])
    tool_catalog = dataset.get("tool_catalog", [])
    case_results = [evaluate_case(case, tool_catalog) for case in cases]

    expected_tool_calls = sum(item["totals"]["expected_tool_calls"] for item in case_results)
    correct_tool_calls = sum(item["totals"]["correct_tool_calls"] for item in case_results)
    expected_params = sum(item["totals"]["expected_params"] for item in case_results)
    correct_params = sum(item["totals"]["correct_params"] for item in case_results)
    actual_tool_calls = sum(item["totals"]["actual_tool_calls"] for item in case_results)
    invalid_tool_calls = sum(item["totals"]["invalid_tool_calls"] for item in case_results)
    required_result_facts = sum(item["totals"]["required_result_facts"] for item in case_results)
    used_result_facts = sum(item["totals"]["used_result_facts"] for item in case_results)
    exception_cases = sum(item["totals"]["exception_cases"] for item in case_results)
    handled_exception_cases = sum(item["totals"]["handled_exception_cases"] for item in case_results)
    expected_hitl_events = sum(item["totals"]["expected_hitl_events"] for item in case_results)
    matched_hitl_events = sum(item["totals"]["matched_hitl_events"] for item in case_results)
    actual_hitl_events = sum(item["totals"]["actual_hitl_events"] for item in case_results)
    unexpected_hitl_events = sum(item["totals"]["unexpected_hitl_events"] for item in case_results)
    expected_hitl_params = sum(item["totals"]["expected_hitl_params"] for item in case_results)
    correct_hitl_params = sum(item["totals"]["correct_hitl_params"] for item in case_results)
    hitl_response_events = sum(item["totals"]["hitl_response_events"] for item in case_results)
    handled_hitl_response_events = sum(item["totals"]["handled_hitl_response_events"] for item in case_results)
    confirmation_required_calls = sum(item["totals"]["confirmation_required_calls"] for item in case_results)
    unsafe_executions = sum(item["totals"]["unsafe_executions"] for item in case_results)

    total_cases = len(case_results)
    passed_cases = sum(1 for item in case_results if item["passed"])
    timing_reasonable_cases = sum(
        1 for item in case_results if item["metrics"]["timing_reasonable"]
    )
    completion_score = sum(item["metrics"]["completion_score"] for item in case_results)

    metrics = {
        "tool_call_accuracy": correct_tool_calls / expected_tool_calls if expected_tool_calls else 1.0,
        "parameter_accuracy": correct_params / expected_params if expected_params else 1.0,
        "task_completion_rate": completion_score / total_cases if total_cases else 0.0,
        "task_accuracy": passed_cases / total_cases if total_cases else 0.0,
        "timing_reasonableness_rate": timing_reasonable_cases / total_cases if total_cases else 0.0,
        "invalid_tool_call_rate": invalid_tool_calls / actual_tool_calls if actual_tool_calls else 0.0,
        "tool_result_utilization_rate": used_result_facts / required_result_facts if required_result_facts else 1.0,
        "tool_exception_handling_success_rate": (
            handled_exception_cases / exception_cases if exception_cases else 1.0
        ),
        "hitl_trigger_accuracy": matched_hitl_events / expected_hitl_events if expected_hitl_events else 1.0,
        "hitl_param_accuracy": correct_hitl_params / expected_hitl_params if expected_hitl_params else 1.0,
        "hitl_response_handling_accuracy": (
            handled_hitl_response_events / hitl_response_events if hitl_response_events else 1.0
        ),
        "unsafe_execution_rate": (
            unsafe_executions / confirmation_required_calls if confirmation_required_calls else 0.0
        ),
        "unexpected_hitl_rate": unexpected_hitl_events / actual_hitl_events if actual_hitl_events else 0.0,
    }

    rounded_metrics = {name: round(value, 4) for name, value in metrics.items()}
    return {
        "dataset": dataset.get("name", "integration_workflows"),
        "description": dataset.get("description", ""),
        "total_cases": total_cases,
        "passed_cases": passed_cases,
        "metrics": rounded_metrics,
        "cases": case_results,
    }


def enforce_thresholds(report: Dict[str, Any], args: argparse.Namespace) -> None:
    metrics = report["metrics"]
    failures = []
    if args.min_task_accuracy is not None and metrics["task_accuracy"] < args.min_task_accuracy:
        failures.append(
            f"task_accuracy {metrics['task_accuracy']} < {args.min_task_accuracy}"
        )
    if args.min_completion is not None and metrics["task_completion_rate"] < args.min_completion:
        failures.append(
            f"task_completion_rate {metrics['task_completion_rate']} < {args.min_completion}"
        )
    if (
        args.max_invalid_tool_call_rate is not None
        and metrics["invalid_tool_call_rate"] > args.max_invalid_tool_call_rate
    ):
        failures.append(
            "invalid_tool_call_rate "
            f"{metrics['invalid_tool_call_rate']} > {args.max_invalid_tool_call_rate}"
        )
    if failures:
        raise SystemExit("; ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run workflow-level integration evals.")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--output", default="")
    parser.add_argument("--min-task-accuracy", type=float, default=None)
    parser.add_argument("--min-completion", type=float, default=None)
    parser.add_argument("--max-invalid-tool-call-rate", type=float, default=None)
    args = parser.parse_args()

    dataset = load_dataset(Path(args.dataset))
    report = evaluate_dataset(dataset)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")

    enforce_thresholds(report, args)


if __name__ == "__main__":
    main()
