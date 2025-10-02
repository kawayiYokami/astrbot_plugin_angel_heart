import unittest
from unittest.mock import MagicMock, AsyncMock, patch
from roles.front_desk import FrontDesk
from core.conversation_ledger import ConversationLedger
from models.analysis_result import SecretaryDecision
import time


class TestFrontDesk(unittest.TestCase):

    def setUp(self):
        """设置测试环境"""
        self.chat_id = "test_chat"
        self.ledger = ConversationLedger(cache_expiry=3600)

        # 添加一些测试消息
        base_time = time.time()
        self.ledger.add_message(self.chat_id, {
            "content": "historic_msg",
            "timestamp": base_time - 1,
            "role": "user",
            "sender_name": "user1"
        })
        self.ledger.add_message(self.chat_id, {
            "content": "recent_msg",
            "timestamp": base_time + 1,
            "role": "user",
            "sender_name": "user2"
        })

        # 设置 last_processed_timestamp
        self.ledger.mark_as_processed(self.chat_id, base_time)

        # 模拟 ConfigManager
        self.config_manager = MagicMock()
        self.config_manager.alias = "TestAlias"

        # 模拟 Secretary
        self.secretary = MagicMock()
        decision = MagicMock(spec=SecretaryDecision)
        decision.topic = "test_topic"
        decision.reply_target = "user2"
        decision.reply_strategy = "test_strategy"
        decision.persona_name = "TestAI"
        self.secretary.get_decision.return_value = decision

        # 创建 FrontDesk 实例
        self.front_desk = FrontDesk(self.config_manager, self.secretary, self.ledger)

    async def test_rewrite_prompt_for_llm_success(self):
        """测试 rewrite_prompt_for_llm 方法成功重构请求"""
        # 创建模拟的 req 对象
        req = MagicMock()
        req.contexts = []
        req.prompt = "original_prompt"
        req.image_urls = ["some_url"]
        req.system_prompt = "original_system"

        # 调用方法
        await self.front_desk.rewrite_prompt_for_llm(self.chat_id, req)

        # 验证 contexts 被正确设置
        self.assertEqual(len(req.contexts), 3)  # 历史消息 + 最近消息 + 最终提示

        # 验证历史消息
        self.assertEqual(req.contexts[0]["role"], "user")
        self.assertIn("historic_msg", req.contexts[0]["content"])

        # 验证最近消息
        self.assertEqual(req.contexts[1]["role"], "user")
        self.assertIn("recent_msg", req.contexts[1]["content"])

        # 验证最终提示消息
        self.assertEqual(req.contexts[2]["role"], "user")
        self.assertIsInstance(req.contexts[2]["content"], str)
        self.assertIn("需要你分析的最新对话", req.contexts[2]["content"])
        self.assertIn("test_topic", req.contexts[2]["content"])

        # 验证 prompt 被清空
        self.assertEqual(req.prompt, "")

        # 验证 image_urls 被清空
        self.assertEqual(req.image_urls, [])

        # 验证 system_prompt 被更新
        self.assertIn("TestAI", req.system_prompt)
        self.assertIn("TestAlias", req.system_prompt)

    async def test_rewrite_prompt_for_llm_no_decision(self):
        """测试 rewrite_prompt_for_llm 方法在没有决策时的情况"""
        # 模拟没有决策
        self.secretary.get_decision.return_value = None

        req = MagicMock()
        req.prompt = "original"

        await self.front_desk.rewrite_prompt_for_llm(self.chat_id, req)

        # 验证 prompt 被设置为错误消息
        self.assertIn("内部决策丢失了", req.prompt)


if __name__ == '__main__':
    unittest.main()