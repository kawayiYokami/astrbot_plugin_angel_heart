import unittest
import time
from unittest.mock import MagicMock, patch
from core.utils import partition_dialogue, format_final_prompt
from core.conversation_ledger import ConversationLedger
from models.analysis_result import SecretaryDecision


class TestUtils(unittest.TestCase):

    def setUp(self):
        """设置测试环境"""
        self.chat_id = "test_chat"
        self.ledger = ConversationLedger(cache_expiry=3600)

        # 添加一些测试消息
        base_time = time.time()
        self.ledger.add_message(self.chat_id, {
            "content": "historic_msg_1",
            "timestamp": base_time - 2,
            "role": "user",
            "sender_name": "user1"
        })
        self.ledger.add_message(self.chat_id, {
            "content": "historic_msg_2",
            "timestamp": base_time - 1,
            "role": "user",
            "sender_name": "user2"
        })
        self.ledger.add_message(self.chat_id, {
            "content": "recent_msg_1",
            "timestamp": base_time + 1,
            "role": "user",
            "sender_name": "user3"
        })
        self.ledger.add_message(self.chat_id, {
            "content": "recent_msg_2",
            "timestamp": base_time + 2,
            "role": "user",
            "sender_name": "user4"
        })

        # 设置 last_processed_timestamp 为 base_time
        self.ledger.mark_as_processed(self.chat_id, base_time)

    def test_partition_dialogue(self):
        """测试 partition_dialogue 函数"""
        historical, recent, boundary_ts = partition_dialogue(self.ledger, self.chat_id)

        # 验证历史消息
        self.assertEqual(len(historical), 2)
        self.assertEqual(historical[0]["content"], "historic_msg_1")
        self.assertEqual(historical[1]["content"], "historic_msg_2")

        # 验证最近消息
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["content"], "recent_msg_1")
        self.assertEqual(recent[1]["content"], "recent_msg_2")

        # 验证边界时间戳
        self.assertGreater(boundary_ts, 0)

    def test_format_final_prompt(self):
        """测试 format_final_prompt 函数"""
        # 创建模拟的最近对话
        recent_dialogue = [
            {
                "sender_name": "user3",
                "content": [{"type": "text", "text": "Hello"}]
            },
            {
                "sender_name": "user4",
                "content": [{"type": "text", "text": "World"}]
            }
        ]

        # 创建模拟的 SecretaryDecision
        decision = MagicMock(spec=SecretaryDecision)
        decision.topic = "test_topic"
        decision.reply_target = "user3"
        decision.reply_strategy = "test_strategy"

        prompt = format_final_prompt(recent_dialogue, decision)

        # 验证提示词结构
        self.assertIn("需要你分析的最新对话", prompt)
        self.assertIn("user3：Hello", prompt)
        self.assertIn("user4：World", prompt)
        self.assertIn("test_topic", prompt)
        self.assertIn("user3", prompt)
        self.assertIn("test_strategy", prompt)

    def test_format_final_prompt_empty_recent_dialogue(self):
        """测试 format_final_prompt 处理空最近对话"""
        decision = MagicMock(spec=SecretaryDecision)
        decision.topic = "empty_topic"
        decision.reply_target = "nobody"
        decision.reply_strategy = "silent"

        prompt = format_final_prompt([], decision)

        self.assertIn("需要你分析的最新对话", prompt)
        self.assertIn("empty_topic", prompt)
        self.assertIn("nobody", prompt)
        self.assertIn("silent", prompt)


if __name__ == '__main__':
    unittest.main()