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

    async def test_cache_message(self):
        """测试 cache_message 方法使用 outline 作为正文"""
        # 创建模拟事件
        event = MagicMock()
        event.get_message_outline.return_value = "Test message outline"
        event.get_messages.return_value = []  # 没有图片
        event.get_sender_id.return_value = "user123"
        event.get_sender_name.return_value = "TestUser"
        event.get_timestamp.return_value = time.time()

        # 调用缓存方法
        await self.front_desk.cache_message(self.chat_id, event)

        # 验证消息被添加到 ledger
        historical, recent, _ = self.front_desk.conversation_ledger.get_context_snapshot(self.chat_id)
        all_messages = historical + recent

        self.assertEqual(len(all_messages), 1)
        msg = all_messages[0]
        self.assertEqual(msg["sender_id"], "user123")
        self.assertEqual(msg["sender_name"], "TestUser")

        # 验证 content 使用 outline
        self.assertEqual(len(msg["content"]), 1)
        self.assertEqual(msg["content"][0]["type"], "text")
        self.assertEqual(msg["content"][0]["text"], "Test message outline")

    async def test_cache_message_with_image(self):
        """测试 cache_message 方法处理图片组件"""
        # 创建模拟事件
        event = MagicMock()
        event.get_message_outline.return_value = "Message with image"
        event.get_messages.return_value = [MagicMock(Image)]  # 模拟图片组件
        event.get_sender_id.return_value = "user123"
        event.get_sender_name.return_value = "TestUser"
        event.get_timestamp.return_value = time.time()

        # 模拟图片处理器
        self.front_desk.image_processor.convert_url_to_data_url = AsyncMock(return_value="data:image/png;base64,fake")

        # 调用缓存方法
        await self.front_desk.cache_message(self.chat_id, event)

        # 验证消息被添加到 ledger
        historical, recent, _ = self.front_desk.conversation_ledger.get_context_snapshot(self.chat_id)
        all_messages = historical + recent

        self.assertEqual(len(all_messages), 1)
        msg = all_messages[0]

        # 验证 content 包含文本和图片
        self.assertEqual(len(msg["content"]), 2)
        text_part = msg["content"][0]
        image_part = msg["content"][1]

        self.assertEqual(text_part["type"], "text")
        self.assertEqual(text_part["text"], "Message with image")

        self.assertEqual(image_part["type"], "image_url")
        self.assertEqual(image_part["image_url"]["url"], "data:image/png;base64,fake")

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