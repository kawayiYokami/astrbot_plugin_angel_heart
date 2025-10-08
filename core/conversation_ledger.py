import time
import threading
from typing import List, Dict, Tuple
from . import utils

# 条件导入：当缺少astrbot依赖时使用Mock
try:
    from astrbot.api import logger
except ImportError:
    # 创建Mock logger用于测试
    class MockLogger:
        def debug(self, msg): pass
        def info(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass
    logger = MockLogger()

class ConversationLedger:
    """
    对话总账 - 插件内部权威的、唯一的对话记录中心。
    管理所有对话的完整历史，并以线程安全的方式处理状态。
    """
    def __init__(self, cache_expiry: int):
        self._lock = threading.Lock()
        # 每个 chat_id 对应一个独立的账本
        self._ledgers: Dict[str, Dict] = {}
        self.cache_expiry = cache_expiry

        # 每个会话的最大消息数量
        self.PER_CHAT_LIMIT = 1000
        # 总消息数量上限
        self.TOTAL_MESSAGE_LIMIT = 100000
        # 最小保留消息数量（即使过期也保留）
        self.MIN_RETAIN_COUNT = 7

    def _get_or_create_ledger(self, chat_id: str) -> Dict:
        """获取或创建指定会话的账本。"""
        with self._lock:
            if chat_id not in self._ledgers:
                self._ledgers[chat_id] = {
                    "messages": [],
                    "last_processed_timestamp": 0.0
                }
            return self._ledgers[chat_id]

    def add_message(self, chat_id: str, message: Dict):
        """
        向指定会话添加一条新消息。
        消息必须包含一个精确的 'timestamp' 字段。
        """
        # 1. 清理所有会话的过期消息
        self._prune_all_expired_messages()

        # 2. 添加新消息
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            # 添加一个字段标记消息是否已处理，默认为False
            message["is_processed"] = False

            # 为了保证顺序，可以考虑在插入前排序或使用bisect.insort
            ledger["messages"].append(message)
            # 确保消息列表始终按时间戳排序
            ledger["messages"].sort(key=lambda m: m.get("timestamp", 0))

            # 限制每个会话的消息数量
            if len(ledger["messages"]) > self.PER_CHAT_LIMIT:
                # 保留最新的PER_CHAT_LIMIT条消息
                ledger["messages"] = ledger["messages"][-self.PER_CHAT_LIMIT:]

        # 3. 检查并限制总消息数量
        self._enforce_total_message_limit()

    def get_context_snapshot(self, chat_id: str) -> Tuple[List[Dict], List[Dict], float]:
        """
        获取用于分析的上下文快照。
        现在调用外部工具函数来实现逻辑分离。
        """
        # 直接调用新的、独立的工具函数
        return utils.partition_dialogue(self, chat_id)

    def mark_as_processed(self, chat_id: str, boundary_timestamp: float):
        """
        将指定时间戳之前的所有未处理消息标记为已处理，并原子化地更新处理边界。
        此操作通过检查 last_processed_timestamp 来处理并发，确保处理状态不倒退。
        """
        if boundary_timestamp <= 0:
            return

        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            # 关键并发控制：只有当新的边界时间戳大于当前记录时，才进行处理。
            # 这可以防止旧的或乱序的调用覆盖新的状态。
            if boundary_timestamp > ledger["last_processed_timestamp"]:

                # 遍历所有消息，更新 is_processed 标志
                for message in ledger["messages"]:
                    if not message.get("is_processed") and message.get("timestamp", 0) <= boundary_timestamp:
                        message["is_processed"] = True

                # 在完成所有标记后，更新“高水位标记”
                ledger["last_processed_timestamp"] = boundary_timestamp

    def _prune_expired_messages(self, chat_id: str):
        """清理指定会话的过期消息。
        确保至少保留最新的 MIN_RETAIN_COUNT 条消息，即使过期。
        """
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            current_time = time.time()
            expiry_threshold = current_time - self.cache_expiry
            all_messages = ledger["messages"]

            # 如果总消息数不超过最小保留数量，跳过清理
            if len(all_messages) <= self.MIN_RETAIN_COUNT:
                return

            # 按时间戳降序排序（最新的在前）
            sorted_messages = sorted(all_messages, key=lambda m: m["timestamp"], reverse=True)

            # 强制保留最新的 MIN_RETAIN_COUNT 条消息
            retained_latest = sorted_messages[:self.MIN_RETAIN_COUNT]

            # 对剩余消息进行正常的过期清理
            remaining_messages = sorted_messages[self.MIN_RETAIN_COUNT:]
            retained_remaining = [m for m in remaining_messages if m["timestamp"] > expiry_threshold]

            # 合并保留的消息并按时间戳重新排序
            new_messages = retained_latest + retained_remaining
            new_messages.sort(key=lambda m: m["timestamp"])

            ledger["messages"] = new_messages

    def _prune_all_expired_messages(self):
        """清理所有会话的过期消息，确保每个会话至少保留最新的 MIN_RETAIN_COUNT 条消息。"""
        with self._lock:
            current_time = time.time()
            expiry_threshold = current_time - self.cache_expiry

            for chat_id, ledger_data in self._ledgers.items():
                all_messages = ledger_data["messages"]

                # 如果总消息数不超过最小保留数量，跳过清理
                if len(all_messages) <= self.MIN_RETAIN_COUNT:
                    continue

                # 按时间戳降序排序（最新的在前）
                sorted_messages = sorted(all_messages, key=lambda m: m["timestamp"], reverse=True)

                # 强制保留最新的 MIN_RETAIN_COUNT 条消息
                retained_latest = sorted_messages[:self.MIN_RETAIN_COUNT]

                # 对剩余消息进行正常的过期清理
                remaining_messages = sorted_messages[self.MIN_RETAIN_COUNT:]
                retained_remaining = [m for m in remaining_messages if m["timestamp"] > expiry_threshold]

                # 合并保留的消息并按时间戳重新排序
                new_messages = retained_latest + retained_remaining
                new_messages.sort(key=lambda m: m["timestamp"])

                ledger_data["messages"] = new_messages

    def _enforce_total_message_limit(self):
        """强制执行总消息数量限制。
        如果超过限制，从最旧的消息开始删除。
        """
        with self._lock:
            # 计算当前总消息数
            total_messages = 0
            all_messages_with_info = []

            for chat_id, ledger_data in self._ledgers.items():
                for msg in ledger_data["messages"]:
                    all_messages_with_info.append((msg["timestamp"], chat_id, msg))
                    total_messages += 1

            # 如果超过总限制，删除最旧的消息
            if total_messages > self.TOTAL_MESSAGE_LIMIT:
                # 按时间戳排序（升序，最旧的在前）
                all_messages_with_info.sort(key=lambda x: x[0])

                # 计算需要删除多少条消息
                excess_count = total_messages - self.TOTAL_MESSAGE_LIMIT

                # 创建一个字典来跟踪每个会话需要删除的消息
                messages_to_remove = {}
                for i in range(excess_count):
                    timestamp, chat_id, msg = all_messages_with_info[i]
                    if chat_id not in messages_to_remove:
                        messages_to_remove[chat_id] = []
                    messages_to_remove[chat_id].append(msg)

                # 从每个会话中删除对应的消息
                for chat_id, msgs_to_remove in messages_to_remove.items():
                    if chat_id in self._ledgers:
                        ledger_data = self._ledgers[chat_id]
                        # 从消息列表中删除需要移除的消息
                        original_messages = ledger_data["messages"]
                        # 使用消息的内存id或其他唯一标识来删除特定消息
                        # 由于消息是字典，我们基于时间戳和内容来识别
                        new_messages = []
                        msgs_to_remove_copy = msgs_to_remove.copy()

                        for msg in original_messages:
                            # 检查是否是要删除的消息
                            msg_to_remove_idx = -1
                            for i, msg_to_remove in enumerate(msgs_to_remove_copy):
                                # 比较时间戳和内容来确定是否是同一消息
                                if (msg["timestamp"] == msg_to_remove["timestamp"] and
                                    msg.get("content") == msg_to_remove.get("content") and
                                    msg.get("role") == msg_to_remove.get("role")):
                                    msg_to_remove_idx = i
                                    break

                            if msg_to_remove_idx != -1:
                                # 这是要删除的消息，从待删除列表中移除
                                msgs_to_remove_copy.pop(msg_to_remove_idx)
                            else:
                                # 保留这条消息
                                new_messages.append(msg)

                        ledger_data["messages"] = new_messages