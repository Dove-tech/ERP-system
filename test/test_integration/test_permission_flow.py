import unittest
from types import SimpleNamespace

from apis.api_planning_hub import ApiPlanningHub
from apis.api_selection_hub import ApiSelectionHub
from permissions import OperatorContext, ToolPermissionGuard
from utils import TASK_ERROR_CODE, TASK_SUCCESS_CODE


def make_tool(**kwargs):
    defaults = {
        "tool_id": 1,
        "operationId": "queryReconciliation",
        "name_for_human": "Query reconciliation statement",
        "required_permissions": ["finance.reconciliation.read"],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class FakeTraceManager:
    def __init__(self):
        self.events = []

    def add_event(self, trace_id, event_type, payload=None):
        self.events.append({
            "trace_id": trace_id,
            "event_type": event_type,
            "payload": payload or {},
        })


class FakeParamExtractionHub:
    def __init__(self, params, missing=None):
        self.params = params
        self.missing = missing or []

    def extraction_params(self, query, tool):
        return dict(self.params), list(self.missing)


class FakeGenerateTaskHub:
    def __init__(self):
        self.judge_called = False

    def gen_judge_task(self, task_desc, tool, params):
        self.judge_called = True
        return False, ""


class FakeHallucinationGuard:
    def classify_tool_risk(self, tool):
        return getattr(tool, "risk_level", "read")

    def validate_tool_call(self, tool, params, query=""):
        return {
            "allow": True,
            "guardrail_action": "allow",
            "risk_level": "read",
            "violations": [],
            "param_sources": {},
            "hallucination_type": "",
        }


class FakeCrossPromptRouteGuard:
    def __init__(self, result=None):
        self.result = result or {"allow": True, "guardrail_action": "allow", "skipped": True}
        self.called = False

    def validate(self, **kwargs):
        self.called = True
        return self.result


class FakeToolUseHub:
    def __init__(self):
        self.called = False

    def tool_use(self, tool, params):
        self.called = True
        return SimpleNamespace(status_code=200, text='{"ok": true}')


def make_planning_hub(params):
    hub = ApiPlanningHub.__new__(ApiPlanningHub)
    hub.permission_guard = ToolPermissionGuard()
    hub.trace_manager = FakeTraceManager()
    hub.param_extraction_hub = FakeParamExtractionHub(params)
    hub.generate_task_hub = FakeGenerateTaskHub()
    hub.hallucination_guard = FakeHallucinationGuard()
    hub.cross_prompt_route_guard = FakeCrossPromptRouteGuard()
    hub.tool_use_hub = FakeToolUseHub()
    return hub


def make_task(context):
    return SimpleNamespace(
        trace_id="trace-1",
        user_id=context.user_id,
        tenant_id=context.tenant_id,
        operator_context=context.to_dict(),
    )


class PermissionFlowIntegrationTest(unittest.TestCase):
    def test_selection_filters_denied_tool_before_llm_prompt(self):
        hub = ApiSelectionHub.__new__(ApiSelectionHub)
        guard = ToolPermissionGuard()
        context = OperatorContext(tool_permissions=["inventory.stock.read"])
        inventory_tool = make_tool(
            tool_id=1,
            operationId="queryInventory",
            required_permissions=["inventory.stock.read"],
        )
        finance_tool = make_tool(
            tool_id=2,
            operationId="queryReconciliation",
            required_permissions=["finance.reconciliation.read"],
        )

        allowed = hub._filter_tools_by_permission(
            [inventory_tool, finance_tool],
            context,
            guard,
        )

        self.assertEqual([tool.tool_id for tool in allowed], [1])

    def test_tool_check_blocks_missing_tool_permission_before_param_extraction(self):
        context = OperatorContext(tool_permissions=["inventory.stock.read"])
        task = make_task(context)
        hub = make_planning_hub({"region": "华东区"})
        tool = make_tool(required_permissions=["finance.reconciliation.read"])

        result = hub._tool_check(
            tool,
            "查询华东区对账单",
            "帮我查一下华东区对账单",
            operator_context=context,
            task=task,
        )

        self.assertEqual(result["code"], TASK_ERROR_CODE)
        self.assertEqual(result["result"], "permission_denied")
        self.assertFalse(hub.generate_task_hub.judge_called)
        self.assertIn(
            "permission_check_failed",
            [event["event_type"] for event in hub.trace_manager.events],
        )

    def test_tool_check_blocks_out_of_scope_region_after_param_extraction(self):
        context = OperatorContext(
            tool_permissions=["finance.reconciliation.read"],
            allowed_regions=["华东区"],
        )
        task = make_task(context)
        hub = make_planning_hub({"region": "华南区"})
        tool = make_tool(required_permissions=["finance.reconciliation.read"])

        result = hub._tool_check(
            tool,
            "查询华南区对账单",
            "帮我查一下华南区对账单",
            operator_context=context,
            task=task,
        )

        self.assertEqual(result["code"], TASK_ERROR_CODE)
        self.assertEqual(result["result"], "permission_denied")
        self.assertFalse(hub.generate_task_hub.judge_called)
        failed_events = [
            event for event in hub.trace_manager.events
            if event["event_type"] == "permission_check_failed"
        ]
        self.assertEqual(failed_events[-1]["payload"]["stage"], "params_extracted")

    def test_tool_check_allows_valid_tool_and_scope(self):
        context = OperatorContext(
            tool_permissions=["finance.reconciliation.read"],
            allowed_regions=["华东区"],
        )
        task = make_task(context)
        hub = make_planning_hub({"region": "华东区"})
        tool = make_tool(required_permissions=["finance.reconciliation.read"])

        result = hub._tool_check(
            tool,
            "查询华东区对账单",
            "帮我查一下华东区对账单",
            operator_context=context,
            task=task,
        )

        self.assertEqual(result["code"], TASK_SUCCESS_CODE)
        self.assertEqual(result["permission"]["action"], "permission_passed")
        self.assertTrue(hub.generate_task_hub.judge_called)

    def test_tool_check_blocks_high_risk_route_when_cross_prompt_rejects(self):
        context = OperatorContext(
            tool_permissions=["finance.reconciliation.export"],
            allowed_regions=["华东区"],
        )
        task = make_task(context)
        hub = make_planning_hub({"region": "华东区"})
        hub.cross_prompt_route_guard = FakeCrossPromptRouteGuard({
            "allow": False,
            "guardrail_action": "block",
            "skipped": False,
            "reason": "用户只要求导出，没有要求发送邮件",
            "votes": [
                {"prompt_id": "route_intent_explicit", "decision": "clarify", "reason": "缺少发送意图"},
                {"prompt_id": "route_tool_chain_auditor", "decision": "block", "reason": "工具扩大请求"},
            ],
            "vote_summary": {"allow": 0, "clarify": 1, "block": 1, "total": 2},
        })
        tool = make_tool(
            operationId="sendEmail",
            name_for_human="发送邮件",
            required_permissions=["finance.reconciliation.export"],
            risk_level="external_send",
        )

        result = hub._tool_check(
            tool,
            "发送对账单邮件",
            "帮我导出上个月华东区大客户的对账单",
            operator_context=context,
            task=task,
        )

        self.assertEqual(result["code"], TASK_ERROR_CODE)
        self.assertEqual(result["result"], "route_cross_validation")
        self.assertTrue(hub.cross_prompt_route_guard.called)
        self.assertIn(
            "route_cross_validation_decided",
            [event["event_type"] for event in hub.trace_manager.events],
        )

    def test_before_tool_invoke_rechecks_permission_and_blocks_tampered_params(self):
        context = OperatorContext(
            tool_permissions=["finance.reconciliation.read"],
            allowed_regions=["华东区"],
        )
        task = make_task(context)
        hub = make_planning_hub({})
        tool = make_tool(required_permissions=["finance.reconciliation.read"])

        result = hub._process_single_api_invoke(
            "查询华南区对账单",
            task,
            tool,
            {"region": "华南区"},
        )

        self.assertEqual(result["code"], TASK_ERROR_CODE)
        self.assertEqual(result["result"], "permission_denied")
        self.assertFalse(hub.tool_use_hub.called)
        failed_events = [
            event for event in hub.trace_manager.events
            if event["event_type"] == "permission_check_failed"
        ]
        self.assertEqual(failed_events[-1]["payload"]["stage"], "before_tool_invoke")


if __name__ == "__main__":
    unittest.main()
