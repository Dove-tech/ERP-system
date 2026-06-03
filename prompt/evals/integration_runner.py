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
    final_status_matched = expected_status == actual_status

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

    completion_score = (len(matches) + int(final_status_matched)) / (len(expected_steps) + 1)
    exact_tool_sequence = actual_tools == expected_tools
    task_accurate = (
        exact_tool_sequence
        and parameter_accuracy == 1.0
        and timing_reasonable
        and not invalid_tool_calls
        and final_status_matched
        and final_answer_matched
        and result_utilization == 1.0
        and (not exception_required or exception_handled is True)
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
        },
        "detail": {
            "expected_tools": expected_tools,
            "actual_tools": actual_tools,
            "invalid_tool_calls": invalid_tool_calls,
            "param_mismatches": param_mismatches,
            "confirmation_misses": confirmation_misses,
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
