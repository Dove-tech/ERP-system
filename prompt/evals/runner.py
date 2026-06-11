import argparse
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models import LargeLanguageModel
from guardrails import CrossPromptRouteGuard, HallucinationGuard
from prompt.prompt_engineering import PromptRegistry, StructuredOutputParser
from prompt.prompt_hub import create_prompt_hub


DATASET_DIR = Path(__file__).resolve().parent / "datasets"
DEFAULT_MODEL_NAME = os.getenv("model_name", "qwen-max")
DEFAULT_MODEL_TEMPERATURE = float(os.getenv("model_temperature", "0.01"))
DEFAULT_MODEL_TOP_P = float(os.getenv("model_top_p", "0.01"))
DEFAULT_MODEL_API_KEY = os.getenv("model_api_key") or os.getenv("DASHSCOPE_API_KEY", "")
DEFAULT_MODEL_BASE_URL = os.getenv("model_base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")


def load_dataset(name: str) -> Dict[str, Any]:
    path = DATASET_DIR / f"{name}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def iter_dataset_names(task: str) -> Iterable[str]:
    if task == "all":
        for path in sorted(DATASET_DIR.glob("*.json")):
            yield path.stem
    else:
        yield task


def build_tools(raw_tools: List[Dict[str, str]]) -> List[Any]:
    return [
        SimpleNamespace(
            name_for_model=item["name_for_model"],
            name_for_human=item["name_for_human"],
            description=item["description"],
        )
        for item in raw_tools
    ]


def expected_action_to_tool_name(case: Dict[str, Any]) -> str:
    action = case["expected"]["action"]
    if action == "None":
        return "None"
    for tool in case["tools"]:
        if tool["name_for_model"] == action:
            return tool["name_for_human"]
    return action


def run_human_feedback_intent(case: Dict[str, Any], mode: str, hub, llm=None) -> Tuple[bool, Dict[str, Any]]:
    prompt = hub.gen_human_feedback_intent_prompt(
        case["feedback"],
        case["tool_name"],
        case.get("tool_params", {}),
    )
    if mode == "render":
        passed = all(token in prompt for token in [case["feedback"], case["tool_name"]])
        return passed, {"prompt": prompt[:300]}

    output = case.get("model_output", "")
    if mode == "online":
        output = llm.chat_completions(prompt, hub.model_name, DEFAULT_MODEL_TEMPERATURE, DEFAULT_MODEL_TOP_P)
    parsed = hub.post_process_human_feedback_intent_result(output)
    expected = case["expected"]["intent"]
    return parsed["intent"] == expected, {"output": output, "parsed": parsed, "expected": expected}


def run_tool_selection(case: Dict[str, Any], mode: str, hub, llm=None) -> Tuple[bool, Dict[str, Any]]:
    tools = build_tools(case["tools"])
    prompt = hub.gen_tool_selection_prompt(case["query"], tools)
    if mode == "render":
        passed = case["query"] in prompt and all(t["name_for_model"] in prompt for t in case["tools"])
        return passed, {"prompt": prompt[:300]}

    output = case.get("model_output", "")
    if mode == "online":
        output = llm.chat_completions(prompt, hub.model_name, DEFAULT_MODEL_TEMPERATURE, DEFAULT_MODEL_TOP_P)
    selected_tool = hub.post_process_tool_selection_result(output, tools)
    expected_action = case["expected"]["action"]
    if expected_action == "None":
        passed = selected_tool is None
        actual = "None" if selected_tool is None else selected_tool.name_for_model
    else:
        passed = selected_tool is not None and selected_tool.name_for_model == expected_action
        actual = selected_tool.name_for_model if selected_tool is not None else "None"
    return passed, {"output": output, "actual": actual, "expected": expected_action}


def run_tool_summary(case: Dict[str, Any], mode: str, hub, llm=None) -> Tuple[bool, Dict[str, Any]]:
    prompt = hub.gen_tool_summary_prompt(case["query"], case["apis"])
    if mode == "render":
        expected_tokens = case["expected"].get("prompt_contains", [])
        return all(token in prompt for token in expected_tokens), {"prompt": prompt[:500]}

    if mode != "online":
        return True, {"skipped": "tool_summary has no replay model_output; use --mode online for answer checks"}

    output = llm.chat_completions(prompt, hub.model_name, DEFAULT_MODEL_TEMPERATURE, DEFAULT_MODEL_TOP_P)
    keywords = case["expected"].get("answer_keywords", [])
    passed = all(keyword in output for keyword in keywords)
    return passed, {"output": output, "expected_keywords": keywords}


def run_task_classification(case: Dict[str, Any], mode: str, hub, llm=None) -> Tuple[bool, Dict[str, Any]]:
    output = case.get("model_output", "")
    expected = case["expected"]["task_type"]
    normalized = str(output).strip().lower()
    passed = normalized == expected
    return passed, {"actual": normalized, "expected": expected}


def run_param_extraction(case: Dict[str, Any], mode: str, hub, llm=None) -> Tuple[bool, Dict[str, Any]]:
    actual_params = case.get("model_params", {})
    actual_missing = sorted(case.get("model_missing", []))
    expected_params = case["expected"].get("params", {})
    expected_missing = sorted(case["expected"].get("missing", []))
    passed = actual_params == expected_params and actual_missing == expected_missing
    return passed, {
        "actual_params": actual_params,
        "expected_params": expected_params,
        "actual_missing": actual_missing,
        "expected_missing": expected_missing,
    }


def run_hallucination_guard(case: Dict[str, Any], mode: str, hub, llm=None) -> Tuple[bool, Dict[str, Any]]:
    tool = SimpleNamespace(**case["tool"])
    result = HallucinationGuard().validate_tool_call(
        tool,
        case.get("params", {}),
        case.get("query", ""),
    )
    expected = case["expected"]
    passed = (
        result["guardrail_action"] == expected["guardrail_action"]
        and result["risk_level"] == expected["risk_level"]
        and bool(result["violations"]) == expected.get("has_violations", False)
    )
    return passed, {"actual": result, "expected": expected}


class ReplayRouteValidationLLM:
    def __init__(self, outputs: Dict[str, str]):
        self.outputs = outputs
        self.queue = [outputs[prompt_id] for prompt_id in CrossPromptRouteGuard.PROMPT_IDS if prompt_id in outputs]

    def chat_completions(self, prompt, model, temperature, top_p):
        if self.queue:
            return self.queue.pop(0)
        if len(self.outputs) == 1:
            return next(iter(self.outputs.values()))
        return '{"decision":"clarify","reason":"no replay output matched prompt"}'


def run_route_cross_validation(case: Dict[str, Any], mode: str, hub, llm=None) -> Tuple[bool, Dict[str, Any]]:
    registry = PromptRegistry()
    tool = SimpleNamespace(**case["tool"])
    variables = {
        "raw_query": case.get("query", ""),
        "task_description": case.get("task_description", ""),
        "tool_json": json.dumps(case.get("tool", {}), ensure_ascii=False, sort_keys=True),
        "params_json": json.dumps(case.get("params", {}), ensure_ascii=False, sort_keys=True),
    }
    if mode == "render":
        prompts = {
            prompt_id: registry.render(prompt_id, getattr(hub, "model_name", DEFAULT_MODEL_NAME), variables)
            for prompt_id in CrossPromptRouteGuard.PROMPT_IDS
        }
        expected_tokens = [case.get("query", ""), case["tool"].get("operationId", "")]
        passed = all(
            all(token in prompt for token in expected_tokens if token)
            for prompt in prompts.values()
        )
        return passed, {"prompts": {key: value[:300] for key, value in prompts.items()}}

    route_guard = CrossPromptRouteGuard(prompt_registry=registry)
    llm_client = llm if mode == "online" else ReplayRouteValidationLLM(case.get("model_outputs", {}))
    result = route_guard.validate(
        tool=tool,
        params=case.get("params", {}),
        raw_query=case.get("query", ""),
        task_desc=case.get("task_description", ""),
        llm=llm_client,
        model=getattr(hub, "model_name", DEFAULT_MODEL_NAME),
        temperature=DEFAULT_MODEL_TEMPERATURE,
        top_p=DEFAULT_MODEL_TOP_P,
        risk_level=case.get("risk_level", ""),
    )
    expected = case["expected"]
    passed = (
        result.get("guardrail_action") == expected.get("guardrail_action")
        and result.get("allow") == expected.get("allow")
    )
    return passed, {"actual": result, "expected": expected}


def run_tool_chain(case: Dict[str, Any], mode: str, hub, llm=None) -> Tuple[bool, Dict[str, Any]]:
    actual = case.get("model_steps", [])
    expected = case["expected"].get("steps", [])
    passed = actual == expected
    return passed, {"actual": actual, "expected": expected}


def run_slot_filling_intent(case: Dict[str, Any], mode: str, hub, llm=None) -> Tuple[bool, Dict[str, Any]]:
    registry = PromptRegistry()
    prompt = registry.render("slot_filling_intent", getattr(hub, "model_name", DEFAULT_MODEL_NAME), {
        "tool_name": case.get("tool_name", ""),
        "task_description": case.get("task_description", ""),
        "known_params": json.dumps(case.get("known_params", {}), ensure_ascii=False),
        "missing_params": json.dumps(case.get("missing_params", []), ensure_ascii=False),
        "user_feedback": case.get("feedback", ""),
    })
    if mode == "render":
        expected_tokens = [case.get("tool_name", ""), case.get("feedback", "")]
        return all(token in prompt for token in expected_tokens if token), {"prompt": prompt[:500]}

    output = case.get("model_output", "")
    if mode == "online":
        output = llm.chat_completions(prompt, hub.model_name, DEFAULT_MODEL_TEMPERATURE, DEFAULT_MODEL_TOP_P)
    parsed = StructuredOutputParser.parse_slot_filling_intent(output)
    expected = case["expected"]
    expected_params = expected.get("filled_params", {})
    actual_params = parsed.get("filled_params", {})
    passed = (
        parsed.get("intent") == expected.get("intent")
        and all(actual_params.get(key) == value for key, value in expected_params.items())
    )
    return passed, {"output": output, "parsed": parsed, "expected": expected}


RUNNERS = {
    "human_feedback_intent": run_human_feedback_intent,
    "tool_selection": run_tool_selection,
    "tool_summary": run_tool_summary,
    "task_classification": run_task_classification,
    "param_extraction": run_param_extraction,
    "hallucination_guard": run_hallucination_guard,
    "route_cross_validation": run_route_cross_validation,
    "tool_chain": run_tool_chain,
    "slot_filling_intent": run_slot_filling_intent,
}


def run_dataset(name: str, mode: str, hub, llm=None) -> Dict[str, Any]:
    dataset = load_dataset(name)
    runner = RUNNERS[name]
    results = []
    for case in dataset["cases"]:
        try:
            passed, detail = runner(case, mode, hub, llm)
        except Exception as exc:
            passed, detail = False, {"error": repr(exc)}
        results.append({"id": case["id"], "passed": passed, "detail": detail})
    passed_count = sum(1 for item in results if item["passed"])
    return {
        "dataset": name,
        "mode": mode,
        "passed": passed_count,
        "total": len(results),
        "accuracy": passed_count / len(results) if results else 0,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Run prompt engineering eval datasets.")
    parser.add_argument("--task", choices=["all", *RUNNERS.keys()], default="all")
    parser.add_argument("--mode", choices=["render", "replay", "online"], default="replay")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    hub = create_prompt_hub(args.model)
    llm = None
    if args.mode == "online":
        llm = LargeLanguageModel(DEFAULT_MODEL_BASE_URL, DEFAULT_MODEL_API_KEY)

    summaries = [run_dataset(name, args.mode, hub, llm) for name in iter_dataset_names(args.task)]
    report = {
        "model": args.model,
        "mode": args.mode,
        "summaries": summaries,
    }

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text, encoding="utf-8")

    if any(summary["passed"] != summary["total"] for summary in summaries):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
