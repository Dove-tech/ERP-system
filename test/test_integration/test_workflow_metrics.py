import unittest

from prompt.evals.integration_runner import DEFAULT_DATASET, evaluate_dataset, load_dataset


class WorkflowIntegrationMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = evaluate_dataset(load_dataset(DEFAULT_DATASET))

    def test_dataset_contains_representative_workflows(self):
        case_ids = {case["id"] for case in self.report["cases"]}
        self.assertIn("wf_inventory_lookup", case_ids)
        self.assertIn("wf_create_order_full_params", case_ids)
        self.assertIn("wf_create_order_missing_supplier", case_ids)
        self.assertIn("wf_plan_adjustment_multi_tool", case_ids)
        self.assertIn("wf_irrelevant_request_no_tool", case_ids)
        self.assertIn("wf_inventory_api_exception", case_ids)

    def test_workflow_metrics_meet_release_thresholds(self):
        metrics = self.report["metrics"]
        self.assertGreaterEqual(metrics["tool_call_accuracy"], 0.95)
        self.assertGreaterEqual(metrics["parameter_accuracy"], 0.95)
        self.assertGreaterEqual(metrics["task_completion_rate"], 0.98)
        self.assertGreaterEqual(metrics["task_accuracy"], 0.95)
        self.assertGreaterEqual(metrics["timing_reasonableness_rate"], 0.95)
        self.assertLessEqual(metrics["invalid_tool_call_rate"], 0.0)
        self.assertGreaterEqual(metrics["tool_result_utilization_rate"], 0.95)
        self.assertGreaterEqual(metrics["tool_exception_handling_success_rate"], 0.95)

    def test_every_case_has_diagnostic_detail(self):
        for case in self.report["cases"]:
            self.assertIn("expected_tools", case["detail"])
            self.assertIn("actual_tools", case["detail"])
            self.assertIn("param_mismatches", case["detail"])
            self.assertIn("invalid_tool_calls", case["detail"])


if __name__ == "__main__":
    unittest.main()
