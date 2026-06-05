import unittest

from prompt.evals.integration_runner import load_dataset
from prompt.evals.online_integration_runner import DEFAULT_DATASET


class OnlineIntegrationDatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = load_dataset(DEFAULT_DATASET)
        cls.cases = cls.dataset["cases"]
        cls.case_ids = {case["id"] for case in cls.cases}

    def test_dataset_contains_all_designed_case_groups(self):
        categories = {}
        for case in self.cases:
            categories[case.get("category", "")] = categories.get(case.get("category", ""), 0) + 1

        self.assertEqual(categories.get("happy"), 10)
        self.assertEqual(categories.get("bad"), 15)
        self.assertEqual(categories.get("hitl"), 7)
        self.assertEqual(len(self.cases), 32)

    def test_dataset_does_not_handwrite_actual_trace(self):
        for case in self.cases:
            self.assertNotIn("actual_trace", case)

    def test_representative_cases_are_present(self):
        expected_ids = {
            "hc_01_single_inventory_lookup",
            "hc_04_order_inventory_supplier_plan_update",
            "hc_07_confirm_stage_param_change_reconfirm",
            "bc_01_out_of_scope_no_tool",
            "bc_09_tool_exception_handled",
            "bc_10_loop_guard_stops_repeated_call",
            "hitl_04_fill_missing_param",
            "hitl_07_unauthorized_feedback_blocked",
        }
        self.assertTrue(expected_ids.issubset(self.case_ids))

    def test_every_case_has_expected_workflow_or_terminal_assertion(self):
        for case in self.cases:
            expected = case.get("expected", {})
            self.assertIn("task_status", expected, case.get("id"))
            self.assertIn("operation_sequence", expected, case.get("id"))
            self.assertIn("final_answer_contains", expected, case.get("id"))


if __name__ == "__main__":
    unittest.main()
