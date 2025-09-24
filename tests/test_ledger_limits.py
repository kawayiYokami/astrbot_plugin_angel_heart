import unittest
import time
from core.conversation_ledger import ConversationLedger

class TestConversationLedgerLimits(unittest.TestCase):

    def test_per_chat_message_limit(self):
        """测试每个会话的消息数量限制。"""
        ledger = ConversationLedger(cache_expiry=3600)
        # 临时设置较小的限制以进行测试
        ledger.PER_CHAT_LIMIT = 5
        chat_id = "group_limit_test"

        # 添加超过限制的消息
        for i in range(8):
            timestamp = time.time() + i
            ledger.add_message(chat_id, {"content": f"msg_{i}", "timestamp": timestamp, "role": "user"})

        # 获取快照，确认只保留最新的5条消息
        history, unprocessed, _ = ledger.get_context_snapshot(chat_id)
        all_messages = history + unprocessed
        
        self.assertEqual(len(all_messages), 5)
        # 应该保留最后5条消息 (msg_3, msg_4, msg_5, msg_6, msg_7)
        contents = [m["content"] for m in all_messages]
        expected_contents = ["msg_3", "msg_4", "msg_5", "msg_6", "msg_7"]
        self.assertEqual(contents, expected_contents)

    def test_total_message_limit(self):
        """测试总消息数量限制。"""
        ledger = ConversationLedger(cache_expiry=3600)
        # 临时设置较小的限制以进行测试
        ledger.PER_CHAT_LIMIT = 10  # 每个会话的限制设得大一些
        ledger.TOTAL_MESSAGE_LIMIT = 6  # 总限制设为6
        

        # 创建两个会话，每个会话添加一些消息
        chat_id1 = "group_1"
        chat_id2 = "group_2"

        # 在时间上错开，确保chat_id1的消息时间戳更早
        base_time = time.time()
        
        # 添加会话1的消息 (时间戳较早)
        for i in range(3):
            ledger.add_message(chat_id1, {"content": f"chat1_msg_{i}", "timestamp": base_time + i, "role": "user"})
        
        # 添加会话2的消息 (时间戳较晚)
        for i in range(5):  # 总共会添加 3 + 5 = 8 条消息，超过总限制 6
            ledger.add_message(chat_id2, {"content": f"chat2_msg_{i}", "timestamp": base_time + 10 + i, "role": "user"})

        # 检查每个会话的消息
        history1, unprocessed1, _ = ledger.get_context_snapshot(chat_id1)
        history2, unprocessed2, _ = ledger.get_context_snapshot(chat_id2)
        
        all_messages1 = history1 + unprocessed1
        all_messages2 = history2 + unprocessed2
        
        total_messages = len(all_messages1) + len(all_messages2)
        
        # 总消息数应该等于或小于限制
        self.assertLessEqual(total_messages, ledger.TOTAL_MESSAGE_LIMIT)
        
        # 由于chat1的消息时间戳更早，应该被优先删除
        # 最终应该保留chat2的所有5条消息，以及chat1的1条消息（总共6条）
        self.assertEqual(len(all_messages2), 5)  # chat2的所有消息
        # chat1应该只剩下1条或0条消息
        self.assertLessEqual(len(all_messages1), 1)
        
        # 验证保留的消息确实是时间戳较晚的
        all_retained_messages = all_messages1 + all_messages2
        self.assertEqual(len(all_retained_messages), ledger.TOTAL_MESSAGE_LIMIT)
        
        # 检查保留的消息中是否包含chat2的所有消息
        retained_contents = [m["content"] for m in all_retained_messages]
        for i in range(5):
            self.assertIn(f"chat2_msg_{i}", retained_contents)

    def test_message_limit_with_processing_state(self):
        """测试在有已处理消息的情况下应用限制。"""
        ledger = ConversationLedger(cache_expiry=3600)
        ledger.PER_CHAT_LIMIT = 4
        chat_id = "group_processed_limit"

        base_time = time.time()
        
        # 添加4条消息
        for i in range(4):
            ledger.add_message(chat_id, {"content": f"msg_{i}", "timestamp": base_time + i, "role": "user"})
        
        # 标记前2条为已处理
        ledger.mark_as_processed(chat_id, base_time + 1)  # 标记到msg_1
        
        # 再添加2条新消息，现在总共有6条，超过限制4
        for i in range(4, 6):
            ledger.add_message(chat_id, {"content": f"msg_{i}", "timestamp": base_time + i, "role": "user"})
        
        # 获取快照
        history, unprocessed, _ = ledger.get_context_snapshot(chat_id)
        
        # 应该保留最新的4条消息 (msg_2, msg_3, msg_4, msg_5)
        all_messages = history + unprocessed
        self.assertEqual(len(all_messages), 4)
        
        contents = [m["content"] for m in all_messages]
        expected_contents = ["msg_2", "msg_3", "msg_4", "msg_5"]
        self.assertEqual(contents, expected_contents)
        
        # 历史消息应该是 [msg_2] (如果原来被处理的仍然保留)
        # 不，历史消息应该是根据last_processed_timestamp确定的
        history_contents = [m["content"] for m in history]
        unprocessed_contents = [m["content"] for m in unprocessed]
        
        # 历史消息应该是根据时间戳 <= last_processed_timestamp (base_time + 1) 来确定
        # 所以历史消息应该是 [msg_0, msg_1] (如果它们没有被限制删除)
        # 但由于限制，只有最新的4条被保留，即 [msg_2, msg_3, msg_4, msg_5]
        # 所以实际上没有消息的时间戳 <= base_time + 1
        self.assertEqual(history_contents, [])
        self.assertEqual(unprocessed_contents, ["msg_2", "msg_3", "msg_4", "msg_5"])

if __name__ == '__main__':
    unittest.main()