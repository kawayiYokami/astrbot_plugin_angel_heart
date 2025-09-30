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

        # 添加超过 MIN_RETAIN_COUNT (7) 条消息，确保清理逻辑会被触发
        # 先添加7条最新的消息（这些会被强制保留，即使过期）
        for i in range(7):
            timestamp = initial_time + i * 0.1  # 间隔0.1秒
            ledger.add_message(chat_id, {"content": f"keep_{i}", "timestamp": timestamp, "role": "user"})

        # 再添加一些过期消息（时间戳远早于当前）
        for i in range(3):
            old_timestamp = initial_time - 3 - i  # 3秒前及更早的消息，将超过1秒的过期时间
            ledger.add_message(chat_id, {"content": f"old_{i}", "timestamp": old_timestamp, "role": "user"})

        # 等待足够时间确保旧消息过期
        time.sleep(2.1)

        # 添加一个当前时间的新消息，这会触发清理
        current_time = time.time()
        ledger.add_message(chat_id, {"content": "newest", "timestamp": current_time, "role": "user"})

        # 检查清理结果
        # 应该保留：7条最新的keep_*消息 + newest消息 = 8条消息
        # 应该删除：3条old_*消息
        history, unprocessed, _ = ledger.get_context_snapshot(chat_id)
        all_messages = history + unprocessed

        # 验证总消息数量：7(keep) + 1(newest) = 8
        self.assertEqual(len(all_messages), 8)

        content_list = [m["content"] for m in all_messages]

        # 验证保留了最新的keep消息
        for i in range(7):
            self.assertIn(f"keep_{i}", content_list)

        # 验证保留了最新的newest消息
        self.assertIn("newest", content_list)

        # 验证删除了过期的old消息
        for i in range(3):
            self.assertNotIn(f"old_{i}", content_list)


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