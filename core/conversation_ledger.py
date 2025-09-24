import time
import threading
from typing import List, Dict, Tuple

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
        返回: (历史会话, 未处理会话, 快照边界时间戳)
        """
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            last_ts = ledger["last_processed_timestamp"]
            all_messages = ledger["messages"]

            historical_context = [m for m in all_messages if m["timestamp"] <= last_ts]
            unprocessed_dialogue = [m for m in all_messages if m["timestamp"] > last_ts]

            snapshot_boundary_ts = 0.0
            if unprocessed_dialogue:
                snapshot_boundary_ts = unprocessed_dialogue[-1].get("timestamp", 0.0)

            return historical_context, unprocessed_dialogue, snapshot_boundary_ts

    def mark_as_processed(self, chat_id: str, boundary_timestamp: float):
        """
        将指定时间戳之前的所有消息标记为已处理。
        这是解决竞态条件的关键。
        """
        if boundary_timestamp <= 0:
            return

        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            # 只在新的时间戳大于旧的情况下更新，防止状态倒退
            if boundary_timestamp > ledger["last_processed_timestamp"]:
                ledger["last_processed_timestamp"] = boundary_timestamp

    def _prune_expired_messages(self, chat_id: str):
        """清理指定会话的过期消息。
        清理所有已过期的消息，不管是否已处理。
        """
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            current_time = time.time()
            expiry_threshold = current_time - self.cache_expiry

            # 只保留未过期的消息（不管是否已处理）
            ledger["messages"] = [
                m for m in ledger["messages"]
                if m["timestamp"] > expiry_threshold
            ]

    def _prune_all_expired_messages(self):
        """清理所有会话的过期消息。"""
        with self._lock:
            current_time = time.time()
            expiry_threshold = current_time - self.cache_expiry
            
            for ledger_data in self._ledgers.values():
                ledger_data["messages"] = [
                    m for m in ledger_data["messages"]
                    if m["timestamp"] > expiry_threshold
                ]

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