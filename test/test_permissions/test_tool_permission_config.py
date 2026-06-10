import unittest

from tools.tool_manager import normalize_required_permissions


class ToolPermissionConfigTest(unittest.TestCase):
    def test_normalize_required_permissions_accepts_string(self):
        self.assertEqual(
            normalize_required_permissions("finance.reconciliation.read"),
            ["finance.reconciliation.read"],
        )

    def test_normalize_required_permissions_accepts_list(self):
        self.assertEqual(
            normalize_required_permissions(["inventory.stock.read", ""]),
            ["inventory.stock.read"],
        )

    def test_normalize_required_permissions_accepts_empty_value(self):
        self.assertEqual(normalize_required_permissions(None), [])
        self.assertEqual(normalize_required_permissions(""), [])


if __name__ == "__main__":
    unittest.main()
