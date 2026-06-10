import unittest
from types import SimpleNamespace

from permissions import OperatorContext, ToolPermissionGuard, build_operator_context


def make_tool(**kwargs):
    defaults = {
        "tool_id": 1,
        "operationId": "queryInventory",
        "name_for_human": "Query inventory",
        "required_permissions": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class ToolPermissionGuardTest(unittest.TestCase):
    def setUp(self):
        self.guard = ToolPermissionGuard()

    def test_tool_without_required_permissions_is_allowed(self):
        result = self.guard.validate_tool_access(make_tool(), OperatorContext())
        self.assertTrue(result["allow"])
        self.assertEqual(result["action"], "tool_permission_passed")

    def test_tool_permission_passes_when_user_has_required_permission(self):
        tool = make_tool(required_permissions=["inventory.stock.read"])
        context = OperatorContext(tool_permissions=["inventory.stock.read"])

        result = self.guard.validate_tool_access(tool, context)

        self.assertTrue(result["allow"])
        self.assertEqual(result["details"]["missing_permissions"], [])

    def test_tool_permission_blocks_missing_permission(self):
        tool = make_tool(required_permissions=["finance.reconciliation.read"])
        context = OperatorContext(tool_permissions=["inventory.stock.read"])

        result = self.guard.validate_tool_access(tool, context)

        self.assertFalse(result["allow"])
        self.assertEqual(result["action"], "tool_permission_denied")
        self.assertEqual(result["details"]["missing_permissions"], ["finance.reconciliation.read"])

    def test_filter_allowed_tools_removes_denied_tools(self):
        allowed_tool = make_tool(tool_id=1, required_permissions=["inventory.stock.read"])
        denied_tool = make_tool(tool_id=2, required_permissions=["finance.reconciliation.read"])
        context = OperatorContext(tool_permissions=["inventory.stock.read"])

        allowed, denied = self.guard.filter_allowed_tools([allowed_tool, denied_tool], context)

        self.assertEqual([tool.tool_id for tool in allowed], [1])
        self.assertEqual([item["tool_id"] for item in denied], [2])

    def test_region_scope_blocks_out_of_scope_region(self):
        context = OperatorContext(
            tool_permissions=["finance.reconciliation.read"],
            allowed_regions=["华东区"],
        )

        result = self.guard.validate_parameter_scope({"region": "华南区"}, context)

        self.assertFalse(result["allow"])
        self.assertEqual(result["details"]["violations"][0]["type"], "region_scope_denied")

    def test_tenant_scope_blocks_cross_tenant_param(self):
        context = OperatorContext(tenant_id="tenant_a")

        result = self.guard.validate_parameter_scope({"tenant_id": "tenant_b"}, context)

        self.assertFalse(result["allow"])
        self.assertEqual(result["details"]["violations"][0]["type"], "tenant_scope_denied")

    def test_build_operator_context_uses_server_side_user_context(self):
        context = build_operator_context(
            {
                "user_id": 1001,
                "user_authority": ["mesh_query"],
                "tool_permissions": ["inventory.stock.read"],
                "allowed_regions": ["华东区"],
                "data_scope": {"customer_levels": ["大客户"]},
            },
            {
                "tenantId": "tenant_a",
                "tool_permissions": ["finance.reconciliation.export"],
            },
        )

        self.assertEqual(context.user_id, "1001")
        self.assertIn("mesh_query", context.endpoint_permissions)
        self.assertEqual(context.tool_permissions, ["inventory.stock.read"])
        self.assertNotIn("finance.reconciliation.export", context.tool_permissions)
        self.assertEqual(context.allowed_regions, ["华东区"])
        self.assertEqual(context.customer_scope, ["大客户"])


if __name__ == "__main__":
    unittest.main()
