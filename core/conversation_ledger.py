import time
import threading
import sqlite3
from pathlib import Path
from typing import List, Dict, Tuple
from . import utils

# 条件导入：当缺少astrbot依赖时使用Mock
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class ConversationLedger:
    """
    对话总账 - 插件内部权威的、唯一的对话记录中心。
    管理所有对话的完整历史，并以线程安全的方式处理状态。
    """
    def __init__(self, cache_expiry: int, data_dir: Path):
        import bisect
        self._lock = threading.Lock()
        # 专用于数据库操作的锁，保护并发访问 SQLite
        self._db_lock = threading.Lock()
        # 每个 chat_id 对应一个独立的账本
        self._ledgers: Dict[str, Dict] = {}
        self.cache_expiry = cache_expiry

        # 每个会话的最大消息数量
        self.PER_CHAT_LIMIT = 1000
        # 总消息数量上限
        self.TOTAL_MESSAGE_LIMIT = 100000
        # 最小保留消息数量（即使过期也保留）
        self.MIN_RETAIN_COUNT = 7

        # 缓存 bisect 模块
        self._bisect = bisect

        # 初始化 SQLite 数据库用于图片转述缓存
        db_path = data_dir / "caption_cache.db"
        self.db_conn = sqlite3.connect(db_path, check_same_thread=False)
        self.db_cursor = self.db_conn.cursor()

        # 创建缓存表（如果不存在）
        with self._db_lock:
            self.db_cursor.execute("""
                CREATE TABLE IF NOT EXISTS caption_cache (
                    url TEXT PRIMARY KEY,
                    caption TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            self.db_conn.commit()
        logger.info(f"AngelHeart: 图片转述缓存数据库已初始化于 {db_path}")

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
            # 添加一个字段标记消息是否已处理，如果未设置则默认为False
            # 这样可以避免覆盖外部预设的 is_processed 值（如 tool_call 消息）
            if "is_processed" not in message:
                message["is_processed"] = False

            # 使用 bisect.insort 在排序位置插入，避免全量排序
            self._bisect.insort(
                ledger["messages"],
                message,
                key=lambda m: m.get("timestamp", 0)
            )

            # 限制每个会话的消息数量
            if len(ledger["messages"]) > self.PER_CHAT_LIMIT:
                # 保留最新的PER_CHAT_LIMIT条消息
                ledger["messages"] = ledger["messages"][-self.PER_CHAT_LIMIT:]

        # 3. 检查并限制总消息数量
        self._enforce_total_message_limit()

    def get_all_messages(self, chat_id: str) -> List[Dict]:
        """
        获取指定会话的所有消息。

        Args:
            chat_id: 会话ID

        Returns:
            消息列表
        """
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            return ledger["messages"].copy()  # 返回副本避免外部修改

    def set_messages(self, chat_id: str, messages: List[Dict]):
        """
        设置指定会话的消息列表。
        注意：这会完全替换现有的消息列表。

        Args:
            chat_id: 会话ID
            messages: 新的消息列表
        """
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            ledger["messages"] = messages.copy()  # 保存副本避免外部修改

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
        """清理所有会话的过期消息，最小化锁争用"""
        current_time = time.time()

        # 收集需要处理的数据（快速持锁）
        with self._lock:
            chats_to_prune = [(cid, list(ld["messages"]))
                              for cid, ld in self._ledgers.items()]

        # 处理数据（释放锁）
        pruned_data = {}
        for chat_id, messages in chats_to_prune:
            pruned_data[chat_id] = self._prune_messages_list(messages, current_time)

        # 更新结果（再次持锁）
        with self._lock:
            for chat_id, pruned_messages in pruned_data.items():
                if chat_id in self._ledgers:
                    self._ledgers[chat_id]["messages"] = pruned_messages

    def _prune_messages_list(self, messages: List[Dict], current_time: float) -> List[Dict]:
        """处理单个消息列表的过期清理（无锁操作）"""
        expiry_threshold = current_time - self.cache_expiry

        # 将过期未处理的旧消息标记为已处理，避免干扰新消息分析
        for msg in messages:
            if not msg.get("is_processed", False) and msg["timestamp"] <= expiry_threshold:
                msg["is_processed"] = True

        # 如果总消息数不超过最小保留数量，跳过删除（但状态扭转已完成）
        if len(messages) <= self.MIN_RETAIN_COUNT:
            return messages

        # 按时间戳降序排序（最新的在前）
        sorted_messages = sorted(messages, key=lambda m: m["timestamp"], reverse=True)

        # 强制保留最新的 MIN_RETAIN_COUNT 条消息
        retained_latest = sorted_messages[:self.MIN_RETAIN_COUNT]

        # 对剩余消息进行正常的过期清理
        remaining_messages = sorted_messages[self.MIN_RETAIN_COUNT:]
        retained_remaining = [m for m in remaining_messages if m["timestamp"] > expiry_threshold]

        # 合并保留的消息并按时间戳重新排序
        new_messages = retained_latest + retained_remaining
        new_messages.sort(key=lambda m: m["timestamp"])

        return new_messages

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

    def add_caption_to_message(self, chat_id: str, message_timestamp: float, caption: str) -> bool:
        """
        为指定会话中的特定消息添加图片转述

        Args:
            chat_id: 会话ID
            message_timestamp: 消息时间戳
            caption: 图片转述文本

        Returns:
            bool: 是否成功添加转述
        """
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            # 查找对应时间戳的消息
            for message in ledger["messages"]:
                if abs(message.get("timestamp", 0) - message_timestamp) < 0.001:  # 处理浮点数精度
                    message["image_caption"] = caption

                    # 转述成功后，清空图片URL避免重复转述
                    if isinstance(message.get("content"), list):
                        # 移除所有 image_url 组件
                        message["content"] = [
                            item for item in message["content"]
                            if item.get("type") != "image_url"
                        ]
                        logger.debug(f"AngelHeart[{chat_id}]: 已清空图片URL，避免重复转述")

                    logger.debug(f"AngelHeart[{chat_id}]: 已为消息添加图片转述: {caption[:50]}...")
                    return True
            return False

    async def generate_captions_for_chat(self, chat_id: str, caption_provider_id: str, astr_context=None) -> int:
        """
        为指定会话中的所有未转述图片生成转述

        Args:
            chat_id: 会话ID
            caption_provider_id: 图片转述Provider ID
            astr_context: AstrBot上下文对象，用于获取Provider

        Returns:
            int: 成功转述的图片数量
        """
        if not astr_context:
            logger.warning(f"AngelHeart[{chat_id}]: astr_context 为空，无法进行图片转述")
            return 0

        # 获取转述Provider
        caption_provider = astr_context.get_provider_by_id(caption_provider_id)
        if not caption_provider:
            logger.error(f"AngelHeart[{chat_id}]: 无法找到图片转述Provider: {caption_provider_id}")
            return 0

        # 获取配置
        try:
            cfg = astr_context.get_config(umo=chat_id)["provider_settings"]
            img_cap_prompt = cfg.get("image_caption_prompt", "请准确描述图片内容")
        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 获取配置失败: {e}")
            return 0

        ledger = self._get_or_create_ledger(chat_id)
        processed_count = 0

        with self._lock:
            # 查找所有包含图片且未转述的消息
            messages_needing_caption = []
            for message in ledger["messages"]:
                if (message.get("role") in ["user", "assistant"] and
                    isinstance(message.get("content"), list) and
                    not message.get("image_caption")):  # 还没有转述

                    # 检查是否包含图片
                    has_image = any(item.get("type") == "image_url" for item in message["content"])
                    if has_image:
                        messages_needing_caption.append(message)

            logger.info(f"AngelHeart[{chat_id}]: 找到 {len(messages_needing_caption)} 条需要转述图片的消息")

        # 逐一处理需要转述的消息（在锁外进行异步操作）
        for message in messages_needing_caption:
            try:
                # 提取图片URL - 优先使用原始URL，避免base64数据过长
                image_urls = []
                for item in message["content"]:
                    if item.get("type") == "image_url":
                        # 优先使用原始URL
                        original_url = item.get("original_url")
                        if original_url and original_url != "[IMAGE_PLACEHOLDER]":
                            image_urls.append(original_url)
                            logger.debug(f"AngelHeart[{chat_id}]: 使用原始URL进行转述: {original_url[:100]}...")
                        else:
                            # 回退到base64数据URL
                            image_url = item.get("image_url", {}).get("url", "")
                            if image_url and not image_url.startswith("data:"):
                                image_urls.append(image_url)

                if image_urls:
                    # 我们只处理第一张图片的URL作为缓存键
                    target_url = image_urls[0]
                    final_caption = ""

                    # 1. 查询 SQLite 缓存（在锁保护下执行）
                    with self._db_lock:
                        self.db_cursor.execute("SELECT caption FROM caption_cache WHERE url = ?", (target_url,))
                        result = self.db_cursor.fetchone()

                    if result:
                        # 缓存命中
                        final_caption = result[0]
                        logger.info(f"AngelHeart[{chat_id}]: 图片转述缓存命中 (SQLite): {target_url[:100]}...")
                    else:
                        # 2. 缓存未命中，调用 LLM
                        logger.debug(f"AngelHeart[{chat_id}]: 缓存未命中，调用LLM转述URL: {target_url[:100]}...")
                        llm_resp = await caption_provider.text_chat(
                            prompt=img_cap_prompt,
                            image_urls=[target_url],
                        )

                        if llm_resp and llm_resp.completion_text:
                            final_caption = llm_resp.completion_text.strip()

                            # 3. 结果存入 SQLite 缓存（在锁保护下执行）
                            try:
                                with self._db_lock:
                                    self.db_cursor.execute(
                                        "INSERT INTO caption_cache (url, caption, timestamp) VALUES (?, ?, ?)",
                                        (target_url, final_caption, time.time())
                                    )
                                    self.db_conn.commit()
                                logger.info(f"AngelHeart[{chat_id}]: 新图片转述已缓存 (SQLite): {target_url[:100]}...")
                            except sqlite3.IntegrityError:
                                # 在极少数并发情况下，可能另一线程已插入，忽略即可
                                logger.debug(f"AngelHeart[{chat_id}]: 图片转述缓存写入时发生IntegrityError，可能为并发写入，已忽略。")
                        else:
                            logger.warning(f"AngelHeart[{chat_id}]: 图片转述返回空结果")

                    # 4. 将最终的转述结果（来自缓存或LLM）添加到消息中
                    if final_caption:
                        if self.add_caption_to_message(chat_id, message["timestamp"], final_caption):
                            processed_count += 1
                            logger.info(f"AngelHeart[{chat_id}]: 图片转述成功: {final_caption[:50]}...")
                        else:
                            logger.warning(f"AngelHeart[{chat_id}]: 无法为消息添加转述结果")

            except Exception as e:
                logger.error(f"AngelHeart[{chat_id}]: 图片转述失败: {e}")
                # 继续处理下一张图片
                continue

        if processed_count > 0:
            logger.info(f"AngelHeart[{chat_id}]: 图片转述完成，共处理 {processed_count} 张图片")

        return processed_count

    def should_process_images(self, chat_id: str, astr_context=None) -> bool:
        """
        判断是否需要为当前会话进行图片转述

        Args:
            chat_id: 会话ID
            astr_context: AstrBot上下文对象，用于获取Provider信息

        Returns:
            bool: 是否需要处理图片
        """
        try:
            # 1. 检查会话中是否有需要转述的图片
            historical_context, recent_dialogue, _ = self.get_context_snapshot(chat_id)
            all_messages = historical_context + recent_dialogue

            has_images_needing_caption = False
            for message in all_messages:
                if (message.get("role") in ["user", "assistant"] and
                    isinstance(message.get("content"), list) and
                    not message.get("image_caption")):  # 还没有转述

                    # 检查是否包含图片
                    has_image = any(item.get("type") == "image_url" for item in message["content"])
                    if has_image:
                        has_images_needing_caption = True
                        break

            if not has_images_needing_caption:
                logger.debug(f"AngelHeart[{chat_id}]: 会话中无需转述的图片")
                return False

            # 2. 检查当前使用的Provider是否支持图片
            if astr_context:
                try:
                    current_provider = astr_context.get_using_provider(chat_id)
                    if current_provider:
                        modalities = current_provider.provider_config.get("modalities", ["text"])
                        if "image" in modalities:
                            logger.debug(f"AngelHeart[{chat_id}]: 当前Provider支持图片，无需转述")
                            return False
                except Exception:
                    # 如果获取当前Provider失败，保守处理，继续进行转述
                    logger.debug(f"AngelHeart[{chat_id}]: 无法确定当前Provider能力，继续进行图片转述")

            # 3. 有图片且当前Provider不支持图片，需要转述
            logger.debug(f"AngelHeart[{chat_id}]: 发现需要转述的图片，准备进行图片转述")
            return True

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 检查图片转述条件时发生错误: {e}")
            # 出错时保守处理，不进行转述
            return False

    async def process_image_captions_if_needed(self, chat_id: str, caption_provider_id: str, astr_context=None) -> int:
        """
        如果需要，为指定会话中的图片生成转述（一步完成检查+处理）

        Args:
            chat_id: 会话ID
            caption_provider_id: 图片转述Provider ID
            astr_context: AstrBot上下文对象

        Returns:
            int: 成功转述的图片数量（如果不需要转述则返回0）
        """
        if not caption_provider_id:
            logger.debug(f"AngelHeart[{chat_id}]: 未配置图片转述Provider，跳过图片转述")
            return 0

        if self.should_process_images(chat_id, astr_context):
            return await self.generate_captions_for_chat(chat_id, caption_provider_id, astr_context)

        return 0