import unittest

from memory.context_manager import ContextManager
from memory.memory_manager import MemoryManager


class FakeMemoryManager:
    def __init__(self):
        self.recent_limit = None
        self.compact_calls = []
        self.summary = {
            "summary": "旧摘要：用户前面查询过苹果手机 A15。",
            "compacted_message_count": 4,
            "recent_window": 6,
            "max_summary_chars": 2000,
        }
        self.recent_messages = [
            {"role": "user", "content": f"历史消息 {index}", "message_type": "message"}
            for index in range(10)
        ]

    def get_recent_messages(self, user_id, session_id, limit=8):
        self.recent_limit = limit
        return self.recent_messages[-limit:]

    def get_summary(self, user_id, session_id):
        return self.summary

    def compact_session_summary(self, user_id, session_id, recent_window=6, max_summary_chars=2000,
                                summarizer=None):
        result = {
            "user_id": user_id,
            "session_id": session_id,
            "recent_window": recent_window,
            "max_summary_chars": max_summary_chars,
            "changed": True,
            "compacted_message_count": 8,
            "total_messages": 14,
        }
        self.compact_calls.append(result)
        return result


class ContextCompactionTest(unittest.TestCase):
    def test_build_context_state_uses_minimum_recent_window(self):
        fake_memory = FakeMemoryManager()
        manager = ContextManager(fake_memory)

        state = manager.build_context_state("u1", "s1", "查一下它的库存", context_number=1)

        self.assertEqual(state["recent_window"], 6)
        self.assertEqual(fake_memory.recent_limit, 6)
        self.assertEqual(len(state["recent_messages"]), 6)
        self.assertIn("苹果手机 A15", state["summary"])

    def test_update_after_turn_triggers_compaction_instead_of_overwriting_summary(self):
        fake_memory = FakeMemoryManager()
        manager = ContextManager(fake_memory)

        result = manager.update_after_turn("u1", "s1", "查询库存", "库存为 20")

        self.assertTrue(result["changed"])
        self.assertEqual(len(fake_memory.compact_calls), 1)
        self.assertEqual(fake_memory.compact_calls[0]["recent_window"], 6)

    def test_merge_compaction_summary_keeps_old_summary_and_new_compacted_messages(self):
        old_summary = "旧摘要：用户正在处理华东区供应商对账。"
        messages = [
            {"role": "user", "message_type": "user_query", "content": "查询 A 供应商上月对账单"},
            {"role": "assistant", "message_type": "system_output", "content": "A 供应商存在 2 条差异"},
        ]

        merged = MemoryManager.merge_compaction_summary(old_summary, messages, max_summary_chars=500)

        self.assertIn("旧摘要", merged)
        self.assertIn("新增压缩内容", merged)
        self.assertIn("查询 A 供应商上月对账单", merged)
        self.assertIn("A 供应商存在 2 条差异", merged)

    def test_merge_compaction_summary_trims_when_too_long(self):
        old_summary = "旧摘要：" + "很长" * 400
        messages = [
            {"role": "user", "message_type": "user_query", "content": "最后需要保留的消息"},
        ]

        merged = MemoryManager.merge_compaction_summary(old_summary, messages, max_summary_chars=520)

        self.assertLessEqual(len(merged), 520)
        self.assertIn("摘要已压缩", merged)
        self.assertIn("最后需要保留的消息", merged)

    def test_compaction_uses_llm_summarizer_when_available(self):
        manager = InMemoryMemoryManager()

        result = manager.compact_session_summary(
            "u1",
            "s1",
            recent_window=2,
            max_summary_chars=500,
            summarizer=lambda old, messages, limit: "LLM 摘要：用户查询过苹果手机 A15，库存为 20。",
        )

        self.assertTrue(result["changed"])
        self.assertFalse(result["fallback_used"])
        self.assertEqual(manager.saved_summary, "LLM 摘要：用户查询过苹果手机 A15，库存为 20。")
        self.assertEqual(manager.saved_compacted_count, 2)

    def test_compaction_falls_back_when_llm_summarizer_fails(self):
        manager = InMemoryMemoryManager()

        def broken_summarizer(old, messages, limit):
            raise RuntimeError("llm failed")

        result = manager.compact_session_summary(
            "u1",
            "s1",
            recent_window=2,
            max_summary_chars=500,
            summarizer=broken_summarizer,
        )

        self.assertTrue(result["changed"])
        self.assertTrue(result["fallback_used"])
        self.assertIn("新增压缩内容", manager.saved_summary)
        self.assertIn("查询苹果手机 A15", manager.saved_summary)


class InMemoryMemoryManager(MemoryManager):
    def __init__(self):
        self.messages = [
            {"role": "user", "content": "查询苹果手机 A15", "message_type": "user_query"},
            {"role": "assistant", "content": "苹果手机 A15 的产品 ID 是 P1001", "message_type": "system_output"},
            {"role": "user", "content": "查它的库存", "message_type": "user_query"},
            {"role": "assistant", "content": "库存为 20", "message_type": "system_output"},
            {"role": "user", "content": "把结果整理一下", "message_type": "user_query"},
            {"role": "assistant", "content": "已整理产品和库存信息", "message_type": "system_output"},
            {"role": "user", "content": "再查询供应商", "message_type": "user_query"},
            {"role": "assistant", "content": "供应商为华东电子", "message_type": "system_output"},
        ]
        self.saved_summary = ""
        self.saved_compacted_count = 0

    def count_messages(self, user_id, session_id):
        return len(self.messages)

    def get_summary(self, user_id, session_id):
        return {
            "summary": "旧摘要：用户正在查询产品信息。",
            "compacted_message_count": 0,
            "recent_window": 6,
            "max_summary_chars": 500,
        }

    def get_messages_slice(self, user_id, session_id, skip=0, limit=20):
        return self.messages[skip:skip + limit]

    def update_summary(self, user_id, session_id, summary,
                       compacted_message_count=None, recent_window=None, max_summary_chars=None):
        self.saved_summary = summary
        self.saved_compacted_count = compacted_message_count


if __name__ == "__main__":
    unittest.main()
