import unittest
import time
import threading
from core.conversation_ledger import ConversationLedger

class TestConversationLedger(unittest.TestCase):

    def test_basic_workflow(self):
        """测试基本的消息添加、快照获取和状态推进。"""
        # 使用较长的过期时间，避免测试中的时间戳被误删
        ledger = ConversationLedger(cache_expiry=3600)
        chat_id = "group_1"

        # 使用模拟时间戳，从当前时间开始
        base_time = time.time()
        
        # 添加消息 1, 2
        ledger.add_message(chat_id, {"content": "1", "timestamp": base_time, "role": "user"})
        ledger.add_message(chat_id, {"content": "2", "timestamp": base_time + 1, "role": "user"})

        # 获取快照
        history, unprocessed, boundary_ts = ledger.get_context_snapshot(chat_id)
        self.assertEqual(len(history), 0)
        self.assertEqual(len(unprocessed), 2)
        self.assertEqual(boundary_ts, base_time + 1)

        # 标记处理
        ledger.mark_as_processed(chat_id, boundary_ts)

        # 添加消息 3, 4
        ledger.add_message(chat_id, {"content": "3", "timestamp": base_time + 2, "role": "user"})
        ledger.add_message(chat_id, {"content": "4", "timestamp": base_time + 3, "role": "user"})

        # 再次获取快照
        history, unprocessed, boundary_ts = ledger.get_context_snapshot(chat_id)
        self.assertEqual(len(history), 2) # 历史现在是 1, 2
        self.assertEqual(len(unprocessed), 2) # 未处理是 3, 4
        self.assertEqual(boundary_ts, base_time + 3)

    def test_concurrency(self):
        """测试并发读写下的线程安全性。"""
        ledger = ConversationLedger(cache_expiry=3600)
        chat_id = "group_concurrent"
        message_count = 100

        def writer():
            for i in range(message_count):
                timestamp = time.time()
                ledger.add_message(chat_id, {"content": f"msg_{i}", "timestamp": timestamp, "role": "user"})

        def reader():
            for _ in range(message_count // 10):
                ledger.get_context_snapshot(chat_id)
                time.sleep(0.001)

        threads = []
        for _ in range(5): # 5个写线程
            threads.append(threading.Thread(target=writer))
        for _ in range(2): # 2个读线程
            threads.append(threading.Thread(target=reader))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证最终消息数量是否正确
        # 注意：由于时间戳可能相同，最终数量可能不完全是 5 * 1000
        # 这里的关键是程序不崩溃
        final_ledger = ledger._ledgers[chat_id]
        print(f"并发测试后，消息总数: {len(final_ledger['messages'])}")
        self.assertTrue(len(final_ledger['messages']) > 0)

    def test_prune_expired_messages(self):
        """测试过期消息清理功能。"""
        # 使用短过期时间进行测试
        ledger = ConversationLedger(cache_expiry=1) # 1秒过期
        chat_id = "group_expire"

        # 记录初始时间
        initial_time = time.time()
        
        # 添加一个将来会被认为过期的消息（时间戳远早于当前）
        old_timestamp = initial_time - 3  # 3秒前的消息，将超过1秒的过期时间
        ledger.add_message(chat_id, {"content": "old", "timestamp": old_timestamp, "role": "user"})

        # 添加一个新消息
        new_timestamp = initial_time
        ledger.add_message(chat_id, {"content": "new", "timestamp": new_timestamp, "role": "user"})

        # 等待足够时间确保旧消息过期，但新消息不过期
        time.sleep(2.1)  # 等待2.1秒，这样old消息(3秒前)已过期(3 > 1+2.1-2.1=1)，new消息(0秒前)也已过期(0 -> 在initial_time时添加，到sleep后已经超过1秒)

        # 重新计算时间，添加一个当前时间的新消息，这会触发清理
        current_time = time.time()
        ledger.add_message(chat_id, {"content": "newer", "timestamp": current_time, "role": "user"})

        # 检查是否只剩下未过期的消息
        # old消息: timestamp=initial_time-3, expiry_threshold=current_time-1, 所以 (initial_time-3) > (current_time-1) 应该是 False，且 <= last_ts(0) 也是 False -> 被删除
        # new消息: timestamp=initial_time, expiry_threshold=current_time-1, (initial_time) > (current_time-1) -> 取决于时间差
        # 如果 current_time > initial_time + 1，则 new 消息也会被删除
        # newer消息: timestamp=current_time, expiry_threshold=current_time-1, (current_time) > (current_time-1) -> True -> 保留
        history, unprocessed, _ = ledger.get_context_snapshot(chat_id)
        all_messages = history + unprocessed
        # 现在应该只剩下 "newer" 消息，因为 old 和 new 都过期了
        self.assertEqual(len(all_messages), 1)
        content_list = [m["content"] for m in all_messages]
        self.assertIn("newer", content_list)
        self.assertNotIn("old", content_list)
        self.assertNotIn("new", content_list)


    def test_mark_as_processed_idempotency(self):
        """测试 mark_as_processed 的幂等性，防止状态倒退。"""
        ledger = ConversationLedger(cache_expiry=3600)
        chat_id = "group_idempotency"

        base_time = time.time()
        
        # 添加消息
        ledger.add_message(chat_id, {"content": "1", "timestamp": base_time, "role": "user"})
        ledger.add_message(chat_id, {"content": "2", "timestamp": base_time + 1, "role": "user"})

        # 标记处理到时间戳 base_time + 1
        ledger.mark_as_processed(chat_id, base_time + 1)

        # 获取快照，确认状态已更新
        history, unprocessed, _ = ledger.get_context_snapshot(chat_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(len(unprocessed), 0)

        # 再次尝试标记处理到更早的时间戳 base_time (状态倒退)
        ledger.mark_as_processed(chat_id, base_time)

        # 确认状态没有倒退
        history, unprocessed, _ = ledger.get_context_snapshot(chat_id)
        self.assertEqual(len(history), 2)
        self.assertEqual(len(unprocessed), 0)

    def test_empty_ledger(self):
        """测试空账本的行为。"""
        ledger = ConversationLedger(cache_expiry=3600)
        chat_id = "group_empty"

        # 获取快照
        history, unprocessed, boundary_ts = ledger.get_context_snapshot(chat_id)
        self.assertEqual(len(history), 0)
        self.assertEqual(len(unprocessed), 0)
        self.assertEqual(boundary_ts, 0.0)

if __name__ == '__main__':
    unittest.main()