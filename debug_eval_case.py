from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models import LargeLanguageModel
from prompt.evals.runner import (
    DEFAULT_MODEL_API_KEY,
    DEFAULT_MODEL_BASE_URL,
    DEFAULT_MODEL_TEMPERATURE,
    DEFAULT_MODEL_TOP_P,
    RUNNERS,
    load_dataset,
)
from prompt.prompt_hub import create_prompt_hub


# Debug settings. Change these values directly in PyCharm.
MODEL = "qwen-max"
MODE = "replay"  # render | replay | online

# Set to a case id if you only want to debug one case.
# Example: ONLY_CASE_ID = "slot_fill_supplier"
ONLY_CASE_ID = "chain_shortage_plan_adjustment"

# Keep this list broad enough to cover the important eval paths.
DEBUG_CASES = [
    # Missing-parameter slot filling: provide info, update existing param, abort, unclear.
    ("slot_filling_intent", "slot_fill_supplier"),
    ("slot_filling_intent", "slot_update_existing_and_fill_missing"),
    ("slot_filling_intent", "slot_abort"),
    ("slot_filling_intent", "slot_unclear"),

    # Ambiguous-request feedback: confirm candidate, revise candidate, abort, unclear.
    ("ambiguity_feedback_intent", "ambiguity_confirm_candidate"),
    ("ambiguity_feedback_intent", "ambiguity_provide_info"),
    ("ambiguity_feedback_intent", "ambiguity_abort"),
    ("ambiguity_feedback_intent", "ambiguity_unclear"),

    # Classic HITL confirmation.
    ("human_feedback_intent", "intent_confirm_001"),
    ("human_feedback_intent", "intent_abort_001"),
    ("human_feedback_intent", "intent_unclear_002"),

    # Tool selection: valid tool and no valid tool.
    ("tool_selection", "tool_select_001"),
    ("tool_selection", "tool_select_003"),

    # Parameter extraction replay.
    ("param_extraction", "pe_order_entry_complete"),
    ("param_extraction", "pe_missing_delivery_date"),

    # Guardrail behavior.
    ("hallucination_guard", "hg_read_inventory_allow"),
    ("hallucination_guard", "hg_negative_quantity_clarify"),

    # Multi-step tool-chain replay.
    ("tool_chain", "chain_shortage_plan_adjustment"),
]


def get_case(dataset_name, case_id):
    dataset = load_dataset(dataset_name)
    for case in dataset["cases"]:
        if case["id"] == case_id:
            return case
    available = [case["id"] for case in dataset["cases"]]
    raise ValueError(f"Case {case_id} not found in {dataset_name}. Available: {available}")


def run_one_case(dataset_name, case_id, mode, hub, llm=None):
    case = get_case(dataset_name, case_id)
    runner = RUNNERS[dataset_name]

    # Put a breakpoint on the next line, then Step Into to enter the real eval runner.
    passed, detail = runner(case, mode, hub, llm)

    return {
        "dataset": dataset_name,
        "case_id": case_id,
        "mode": mode,
        "passed": passed,
        "detail": detail,
    }


def main():
    hub = create_prompt_hub(MODEL)
    llm = None
    if MODE == "online":
        print(
            "Online mode will call the model with:",
            {
                "model": MODEL,
                "base_url": DEFAULT_MODEL_BASE_URL,
                "temperature": DEFAULT_MODEL_TEMPERATURE,
                "top_p": DEFAULT_MODEL_TOP_P,
                "has_api_key": bool(DEFAULT_MODEL_API_KEY),
            },
        )
        llm = LargeLanguageModel(DEFAULT_MODEL_BASE_URL, DEFAULT_MODEL_API_KEY)

    results = []
    for dataset_name, case_id in DEBUG_CASES:
        if ONLY_CASE_ID and case_id != ONLY_CASE_ID:
            continue

        result = run_one_case(dataset_name, case_id, MODE, hub, llm)
        results.append(result)
        print(f"[{dataset_name}::{case_id}] passed={result['passed']}")
        print(result["detail"])
        print("-" * 80)

    passed_count = sum(1 for item in results if item["passed"])
    print(f"Summary: {passed_count}/{len(results)} passed")


if __name__ == "__main__":
    main()
