import unittest
from types import SimpleNamespace

from guardrails import CrossPromptRouteGuard


class FakePromptRegistry:
    def render(self, prompt_id, model_name, variables):
        return f"{prompt_id}\n{variables.get('raw_query', '')}\n{variables.get('tool_json', '')}"


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def chat_completions(self, prompt, model, temperature, top_p):
        self.calls += 1
        return self.outputs.pop(0)


def make_tool(**kwargs):
    defaults = {
        "tool_id": 1,
        "operationId": "queryInventory",
        "name_for_human": "查询库存",
        "description": "查询库存",
        "method": "GET",
        "risk_level": "read",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class CrossPromptRouteGuardTest(unittest.TestCase):
    def make_guard(self):
        return CrossPromptRouteGuard(prompt_registry=FakePromptRegistry())

    def test_low_risk_read_route_is_skipped(self):
        guard = self.make_guard()
        llm = FakeLLM([])
        result = guard.validate(
            tool=make_tool(),
            params={"productId": 1001},
            raw_query="查询产品 1001 的库存",
            task_desc="查询库存",
            llm=llm,
            model="qwen-max",
            temperature=0.01,
            top_p=0.01,
        )

        self.assertTrue(result["allow"])
        self.assertTrue(result["skipped"])
        self.assertEqual(llm.calls, 0)

    def test_write_route_allows_two_of_three_votes(self):
        guard = self.make_guard()
        llm = FakeLLM([
            '{"decision":"allow","reason":"用户明确创建订单"}',
            '{"decision":"allow","reason":"工具与意图一致"}',
            '{"risk_decision":"clarify","reason":"写操作仍需 HITL"}',
        ])
        result = guard.validate(
            tool=make_tool(operationId="createOrder", name_for_human="创建订单", method="POST", risk_level="write"),
            params={"productId": 1001, "quantity": 20},
            raw_query="创建产品 1001 的订单，数量 20",
            task_desc="创建订单",
            llm=llm,
            model="qwen-max",
            temperature=0.01,
            top_p=0.01,
            risk_level="write",
        )

        self.assertTrue(result["allow"])
        self.assertEqual(result["guardrail_action"], "allow")
        self.assertEqual(result["vote_summary"]["allow"], 2)

    def test_external_send_without_user_intent_is_blocked(self):
        guard = self.make_guard()
        llm = FakeLLM([
            '{"decision":"clarify","reason":"用户只要求导出"}',
            '{"decision":"block","reason":"缺少发送邮件意图"}',
            '{"risk_decision":"block","reason":"外发数据缺少授权"}',
        ])
        result = guard.validate(
            tool=make_tool(operationId="sendEmail", name_for_human="发送邮件", method="POST", risk_level="external_send"),
            params={"fileType": "reconciliation"},
            raw_query="帮我导出上个月华东区大客户的对账单",
            task_desc="发送对账单邮件",
            llm=llm,
            model="qwen-max",
            temperature=0.01,
            top_p=0.01,
            risk_level="external_send",
        )

        self.assertFalse(result["allow"])
        self.assertEqual(result["guardrail_action"], "block")

    def test_strict_delete_requires_unanimous_allow(self):
        guard = self.make_guard()
        llm = FakeLLM([
            '{"decision":"allow","reason":"用户提到删除"}',
            '{"decision":"allow","reason":"工具匹配"}',
            '{"risk_decision":"clarify","reason":"批量删除需要更明确授权"}',
        ])
        result = guard.validate(
            tool=make_tool(operationId="deleteAllOrders", name_for_human="删除所有订单", method="DELETE", risk_level="delete"),
            params={},
            raw_query="删除所有订单",
            task_desc="删除所有订单",
            llm=llm,
            model="qwen-max",
            temperature=0.01,
            top_p=0.01,
            risk_level="delete",
        )

        self.assertFalse(result["allow"])
        self.assertEqual(result["guardrail_action"], "clarify")


if __name__ == "__main__":
    unittest.main()
