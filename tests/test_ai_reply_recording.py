import unittest
import time
from core.conversation_ledger import ConversationLedger


class TestAIReplyRecording(unittest.TestCase):

    def test_ai_reply_is_recorded_in_ledger(self):
        """测试AI回复会被正确记录到ConversationLedger中"""
        ledger = ConversationLedger(cache_expiry=3600)
        chat_id = "test_group_123"

        # 添加一些用户消息
        ledger.add_message(chat_id, {
            "role": "user",
            "content": "你好",
            "sender_id": "user123",
            "sender_name": "小明",
            "timestamp": time.time() - 30  # 30秒前
        })

        ledger.add_message(chat_id, {
            "role": "user",
            "content": "今天怎么样？",
            "sender_id": "user123",
            "sender_name": "小明",
            "timestamp": time.time() - 10  # 10秒前
        })

        # 标记第一条消息为已处理
        ledger.mark_as_processed(chat_id, time.time() - 30)

        # 模拟AI回复内容
        ai_reply_content = "我很好，谢谢！"

        # 手动调用回复记录逻辑（模拟after_message_sent钩子）
        ai_message = {
            "role": "assistant",
            "content": ai_reply_content,
            "sender_id": "bot123",
            "sender_name": "AngelHeart",
            "timestamp": time.time() - 5,  # AI回复在第二条消息之后
        }
        ledger.add_message(chat_id, ai_message)

        # 推进状态到AI回复时间戳
        ledger.mark_as_processed(chat_id, time.time() - 5)

        # 验证结果
        historical_context, unprocessed_dialogue, boundary_ts = ledger.get_context_snapshot(chat_id)

        # 历史消息应该包含：第一条用户消息 + 第二条用户消息 + AI回复
        self.assertEqual(len(historical_context), 3)

        # 验证AI回复在历史消息中
        ai_messages_in_history = [msg for msg in historical_context if msg.get("role") == "assistant"]
        self.assertEqual(len(ai_messages_in_history), 1)
        self.assertEqual(ai_messages_in_history[0]["content"], ai_reply_content)

        # 未处理消息应该为空（因为所有消息都在边界内）
        self.assertEqual(len(unprocessed_dialogue), 0)

        print(f"✅ 测试通过：AI回复 '{ai_reply_content}' 已正确记录到对话总账")

    def test_conversation_flow_with_ai_replies(self):
        """测试完整的对话流程，包括AI回复"""
        ledger = ConversationLedger(cache_expiry=3600)
        chat_id = "test_conversation"

        # 模拟一轮对话
        # 用户1说的话
        ledger.add_message(chat_id, {
            "role": "user", "content": "今天天气真好",
            "sender_id": "user1", "sender_name": "小明",
            "timestamp": time.time() - 60
        })

        # AI回复
        ledger.add_message(chat_id, {
            "role": "assistant", "content": "是的，阳光明媚",
            "sender_id": "bot", "sender_name": "AngelHeart",
            "timestamp": time.time() - 50
        })

        # 用户2说的话
        ledger.add_message(chat_id, {
            "role": "user", "content": "我想出去玩",
            "sender_id": "user2", "sender_name": "小红",
            "timestamp": time.time() - 40
        })

        # AI再次回复
        ledger.add_message(chat_id, {
            "role": "assistant", "content": "那听起来很不错！",
            "sender_id": "bot", "sender_name": "AngelHeart",
            "timestamp": time.time() - 30
        })

        # 标记到第一个AI回复为止
        ledger.mark_as_processed(chat_id, time.time() - 50)

        # 验证状态
        historical_context, unprocessed_dialogue, _ = ledger.get_context_snapshot(chat_id)

        # 历史消息：用户1 + AI回复1
        self.assertEqual(len(historical_context), 2)
        # 未处理消息：用户2 + AI回复2
        self.assertEqual(len(unprocessed_dialogue), 2)

        # 验证消息顺序和内容
        self.assertEqual(historical_context[0]["content"], "今天天气真好")
        self.assertEqual(historical_context[1]["content"], "是的，阳光明媚")

        self.assertEqual(unprocessed_dialogue[0]["content"], "我想出去玩")
        self.assertEqual(unprocessed_dialogue[1]["content"], "那听起来很不错！")

        print("✅ 测试通过：完整的对话流程（包括AI回复）工作正常")


if __name__ == '__main__':
    unittest.main()