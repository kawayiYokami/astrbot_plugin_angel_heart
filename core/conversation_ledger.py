import time
import threading
import sqlite3
import aiohttp
import io
import base64
from PIL import Image
from pathlib import Path
from typing import List, Dict, Tuple, Optional
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
    def __init__(self, config_manager, data_dir: Path, astr_context=None):
        import bisect
        self._lock = threading.Lock()
        # 专用于数据库操作的锁，保护并发访问 SQLite
        self._db_lock = threading.Lock()
        # 每个 chat_id 对应一个独立的账本
        self._ledgers: Dict[str, Dict] = {}
        self.config_manager = config_manager
        self.astr_context = astr_context

        # 每个会话的最大消息数量
        self.PER_CHAT_LIMIT = 1000
        # 总消息数量上限
        self.TOTAL_MESSAGE_LIMIT = 100000
        # 最小保留消息数量（即使过期也保留）
        self.MIN_RETAIN_COUNT = 7

        # 缓存 bisect 模块
        self._bisect = bisect

        # 每个会话的最后压缩时间戳 {chat_id: timestamp}
        self._last_compression_time: Dict[str, float] = {}

        # 初始化 SQLite 数据库用于图片转述缓存
        db_path = data_dir / "caption_cache.db"
        self.db_conn = sqlite3.connect(db_path, check_same_thread=False)
        self.db_cursor = self.db_conn.cursor()

        # 创建缓存表（如果不存在）
        with self._db_lock:
            # 旧的 URL 缓存表 (保留但不使用)
            self.db_cursor.execute("""
                CREATE TABLE IF NOT EXISTS caption_cache (
                    url TEXT PRIMARY KEY,
                    caption TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            # 新的 内容哈希 缓存表 (dHash)
            # 重新建表以确保 Schema 匹配 dHash 格式
            self.db_cursor.execute("DROP TABLE IF EXISTS image_content_cache")
            self.db_cursor.execute("""
                CREATE TABLE IF NOT EXISTS image_content_cache (
                    dhash TEXT PRIMARY KEY,
                    caption TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            self.db_conn.commit()
        logger.info(f"AngelHeart: 图片转述缓存数据库已初始化于 {db_path}")

    def _compute_dhash(self, image_data: bytes) -> str:
        """计算图片的差值哈希 (dHash)"""
        try:
            # 1. 加载图片
            img = Image.open(io.BytesIO(image_data))

            # 2. 转为灰度图
            img = img.convert("L")

            # 3. 缩放到 9x8 (这样可以得到 8x8 的差值)
            img = img.resize((9, 8), Image.Resampling.LANCZOS)

            # 4. 计算差异值
            diff = []
            width, height = img.size
            pixels = list(img.getdata())

            for row in range(height):
                for col in range(width - 1):
                    # 获取当前像素索引和右侧像素索引
                    pixel_left_idx = row * width + col
                    pixel_right_idx = pixel_left_idx + 1
                    # 如果左边比右边亮，记录1，否则0
                    diff.append(pixels[pixel_left_idx] > pixels[pixel_right_idx])

            # 5. 转为十六进制字符串
            decimal_value = 0
            for index, value in enumerate(diff):
                if value:
                    decimal_value += 1 << index

            return hex(decimal_value)[2:]

        except Exception as e:
            logger.warning(f"dHash计算失败: {e}")
            return ""

    async def _load_image_bytes(self, url: str) -> bytes:
        """从本地文件、网络地址或 data URL 读取图片原始字节。"""
        try:
            if url.startswith("file:///"):
                import os
                path = url[8:]

                if '..' in path or path.startswith('/etc') or path.startswith('/sys'):
                    logger.warning(f"拒绝访问受限路径: {path}")
                    return b""

                if os.name == 'nt' and len(path) > 2 and path[1] == ':':
                    pass
                elif os.name == 'nt' and path.startswith('/'):
                    path = path[1:]

                if not os.path.exists(path):
                    logger.warning(f"本地文件不存在: {path}")
                    return b""

                if os.path.getsize(path) > 10 * 1024 * 1024:
                    logger.warning(f"文件过大，拒绝处理: {path}")
                    return b""

                with open(path, "rb") as f:
                    return f.read()

            if url.startswith("http"):
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10) as resp:
                        if resp.status == 200:
                            return await resp.read()
                        logger.warning(f"下载图片失败 status={resp.status}: {url}")
                        return b""

            if url.startswith("data:image"):
                try:
                    _, encoded = url.split(",", 1)
                    return base64.b64decode(encoded)
                except Exception as e:
                    logger.warning(f"Base64解码失败: {e}")
                    return b""

            logger.warning(f"不支持的URL协议: {url[:20]}...")
            return b""

        except Exception as e:
            logger.warning(f"读取图片异常: {e}, URL: {url}")
            return b""

    def _build_caption_image_data_url(
        self,
        image_data: bytes,
        max_side: int = 960,
        quality: int = 75,
    ) -> str:
        """将图片压缩为最长边不超过 max_side 的 webp data URL。"""
        try:
            img = Image.open(io.BytesIO(image_data))

            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            elif img.mode == "L":
                img = img.convert("RGB")

            width, height = img.size
            longest_side = max(width, height)
            if longest_side > max_side:
                scale = max_side / float(longest_side)
                resized = (
                    max(1, int(round(width * scale))),
                    max(1, int(round(height * scale))),
                )
                img = img.resize(resized, Image.Resampling.LANCZOS)

            output = io.BytesIO()
            img.save(output, format="WEBP", quality=quality, method=6)
            encoded = base64.b64encode(output.getvalue()).decode("utf-8")
            return f"data:image/webp;base64,{encoded}"

        except Exception as e:
            logger.warning(f"构建转述压缩图失败: {e}")
            return ""

    def _build_original_image_data_url(self, image_data: bytes) -> str:
        """将原始图片字节包装成 data URL，避免把外链继续传给转述模型。"""
        if not image_data:
            return ""

        try:
            img = Image.open(io.BytesIO(image_data))
            image_format = (img.format or "PNG").lower()
            if image_format == "jpg":
                image_format = "jpeg"
            encoded = base64.b64encode(image_data).decode("utf-8")
            return f"data:image/{image_format};base64,{encoded}"
        except Exception as e:
            logger.warning(f"构建原始图片 data URL 失败: {e}")
            return ""

    def _apply_broken_image_caption(
        self,
        chat_id: str,
        message_timestamp: float,
    ) -> bool:
        """图片不可用时写入统一降级转述，避免上下文出现空洞。"""
        return self.add_caption_to_message(
            chat_id,
            message_timestamp,
            self.BROKEN_IMAGE_CAPTION,
        )

    def _get_or_create_ledger(self, chat_id: str) -> Dict:
        """获取或创建指定会话的账本。"""
        with self._lock:
            if chat_id not in self._ledgers:
                self._ledgers[chat_id] = {
                    "messages": [],
                    "last_processed_timestamp": 0.0
                }
            return self._ledgers[chat_id]

    def add_message(self, chat_id: str, message: Dict, should_prune: bool = False):
        """
        向指定会话添加一条新消息。
        消息必须包含一个精确的 'timestamp' 字段。

        Args:
            chat_id: 会话ID
            message: 消息字典
            should_prune: 兼容旧参数，当前不再因离场状态强制压缩
        """
        # 1. 添加新消息
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            # 添加一个字段标记消息是否已处理，如果未设置则默认为False
            # 这样可以避免覆盖外部预设的 is_processed 值（如 tool_call 消息）
            if "is_processed" not in message:
                message["is_processed"] = False
            if "chat_id" not in message:
                message["chat_id"] = chat_id

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

        # 2. 判断是否需要压缩
        if self._should_compress(chat_id):
            self._compress_context(chat_id)

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
                    image_refs = self._extract_image_refs_from_content(message.get("content"))
                    if image_refs:
                        message["image_refs"] = image_refs

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

    def _extract_image_refs_from_content(self, content) -> List[str]:
        """从消息 content 中提取可用于展示的图片引用路径。"""
        if not isinstance(content, list):
            return []

        refs: List[str] = []
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "image_url":
                continue

            ref = (
                item.get("local_file_path")
                or item.get("original_file_url")
                or item.get("original_url")
            )
            if not ref:
                image_url = item.get("image_url", {})
                if isinstance(image_url, dict):
                    url = image_url.get("url", "")
                    if isinstance(url, str) and url and not url.startswith("data:"):
                        ref = url

            if isinstance(ref, str) and ref:
                refs.append(ref)

        return refs

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
            img_cap_prompt = "这是一张群聊图片，根据情景准确描述该图片"
        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 获取配置失败: {e}")
            return 0

        ledger = self._get_or_create_ledger(chat_id)
        processed_count = 0

        with self._lock:
            # 确定最近 7 条消息的时间戳边界
            all_messages = ledger["messages"]
            recent_7 = all_messages[-7:] if len(all_messages) > 7 else all_messages
            recent_cutoff_ts = recent_7[0].get("timestamp", 0) if recent_7 else 0

            # 查找所有包含图片且未转述的消息
            messages_needing_caption = []
            expired_messages = []
            for message in all_messages:
                if (message.get("role") == "user" and
                    isinstance(message.get("content"), list) and
                    not message.get("image_caption")):

                    has_image = any(item.get("type") == "image_url" for item in message["content"])
                    if has_image:
                        if message.get("timestamp", 0) >= recent_cutoff_ts:
                            messages_needing_caption.append(message)
                        else:
                            expired_messages.append(message)

            # 不在最近 7 条消息范围内的图片直接标记过期
            for msg in expired_messages:
                msg["image_caption"] = self.EXPIRED_IMAGE_CAPTION
                if isinstance(msg.get("content"), list):
                    msg["content"] = [
                        item for item in msg["content"]
                        if item.get("type") != "image_url"
                    ]
                processed_count += 1

            if expired_messages:
                logger.info(
                    f"AngelHeart[{chat_id}]: {len(expired_messages)} 条不在最近7条范围内的图片消息已标记过期"
                )

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
                    img_dhash = ""
                    raw_image_data = b""

                    # 1. 下载图片并计算 dHash
                    raw_image_data = await self._load_image_bytes(target_url)

                    if not raw_image_data:
                        logger.warning(
                            f"AngelHeart[{chat_id}]: 图片下载失败或内容为空，跳过转述: {target_url[:100]}..."
                        )
                        if not self._apply_broken_image_caption(
                            chat_id,
                            message["timestamp"],
                        ):
                            logger.warning(
                                f"AngelHeart[{chat_id}]: 无法为坏图写入降级转述"
                            )
                        else:
                            processed_count += 1
                        continue

                    img_dhash = self._compute_dhash(raw_image_data)

                    # 2. 查询 SQLite dHash 缓存（在锁保护下执行）
                    if img_dhash:
                        with self._db_lock:
                            self.db_cursor.execute("SELECT caption FROM image_content_cache WHERE dhash = ?", (img_dhash,))
                            result = self.db_cursor.fetchone()

                        if result:
                            final_caption = result[0]
                            logger.info(f"AngelHeart[{chat_id}]: 图片转述缓存命中 (dHash: {img_dhash}): {target_url[:50]}...")

                    if not final_caption:
                        # 3. 缓存未命中，调用 LLM
                        logger.debug(f"AngelHeart[{chat_id}]: 缓存未命中(dHash: {img_dhash})，调用LLM转述URL: {target_url[:50]}...")
                        caption_input_url = self._build_caption_image_data_url(raw_image_data)
                        if caption_input_url:
                            logger.debug(
                                f"AngelHeart[{chat_id}]: 转述图片已压缩为 WEBP(quality=75, max_side=960)"
                            )
                        else:
                            caption_input_url = self._build_original_image_data_url(raw_image_data)
                            if caption_input_url:
                                logger.debug(
                                    f"AngelHeart[{chat_id}]: 压缩图片失败，回退使用原始 data URL 进行转述"
                                )

                        if not caption_input_url:
                            logger.warning(
                                f"AngelHeart[{chat_id}]: 无法构建可用的图片 data URL，写入降级转述"
                            )
                            if not self._apply_broken_image_caption(
                                chat_id,
                                message["timestamp"],
                            ):
                                logger.warning(
                                    f"AngelHeart[{chat_id}]: 无法为不可编码图片写入降级转述"
                                )
                            else:
                                processed_count += 1
                            continue

                        llm_resp = await caption_provider.text_chat(
                            prompt=img_cap_prompt,
                            image_urls=[caption_input_url],
                        )

                        if llm_resp and llm_resp.completion_text:
                            final_caption = llm_resp.completion_text.strip()

                            # 4. 结果存入 SQLite dHash 缓存（在锁保护下执行）
                            if img_dhash:
                                try:
                                    with self._db_lock:
                                        self.db_cursor.execute(
                                            "INSERT OR REPLACE INTO image_content_cache (dhash, caption, timestamp) VALUES (?, ?, ?)",
                                            (img_dhash, final_caption, time.time())
                                        )
                                        self.db_conn.commit()
                                    logger.info(f"AngelHeart[{chat_id}]: 新图片转述已缓存 (dHash: {img_dhash}): {target_url[:50]}...")
                                except sqlite3.IntegrityError:
                                    logger.debug(f"AngelHeart[{chat_id}]: 缓存写入冲突，已忽略")
                            else:
                                logger.warning(f"AngelHeart[{chat_id}]: 图片dHash为空，无法写入缓存")
                        else:
                            logger.warning(f"AngelHeart[{chat_id}]: 图片转述返回空结果")
                            if not self._apply_broken_image_caption(
                                chat_id,
                                message["timestamp"],
                            ):
                                logger.warning(
                                    f"AngelHeart[{chat_id}]: 无法为空转述结果写入降级转述"
                                )
                            else:
                                processed_count += 1

                    # 5. 将最终的转述结果（来自缓存或LLM）添加到消息中
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
                if (message.get("role") == "user" and  # 只检查用户消息
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
                        modalities = current_provider.provider_config.get("modalities", None)
                        if not modalities or not isinstance(modalities, list) or "image" in modalities:
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

    def _should_compress(self, chat_id: str) -> bool:
        """
        判断指定会话是否需要进行上下文压缩。

        触发条件（满足任一即触发）：
        1. 当前Token数达到有效上限的配置阈值
        2. 距离上次压缩超过遗忘时间上限（默认1天）

        Args:
            chat_id: 会话ID

        Returns:
            bool: 是否需要压缩
        """
        max_tokens = self._get_effective_max_conversation_tokens(chat_id)
        if max_tokens <= 0:
            # 禁用了Token限制，仅检查时间条件
            return self._is_forgetting_timeout(chat_id)

        # 条件1：Token达到配置阈值
        current_tokens = self._estimate_tokens(chat_id)
        threshold_ratio = self.config_manager.context_compression_threshold
        threshold = int(max_tokens * threshold_ratio)
        if current_tokens >= threshold:
            return True

        # 条件2：遗忘时间超限
        return self._is_forgetting_timeout(chat_id)

    def _get_effective_max_conversation_tokens(self, chat_id: str) -> int:
        """
        获取当前会话的有效上下文上限。

        优先读取会话绑定模型的 max_context_tokens，并与插件配置的
        max_conversation_tokens 取较小正数。插件配置为 0 时表示不设置
        插件侧上限，仅使用模型上限；两者都不可用时禁用 Token 触发。
        """
        configured_limit = self.config_manager.max_conversation_tokens
        provider_limit = self._get_provider_max_context_tokens(chat_id)

        limits = [
            int(limit)
            for limit in (configured_limit, provider_limit)
            if isinstance(limit, (int, float)) and limit > 0
        ]
        if not limits:
            return 0

        effective_limit = min(limits)
        if provider_limit and configured_limit and provider_limit > 0 and configured_limit > 0:
            logger.debug(
                f"AngelHeart[{chat_id}]: 上下文上限取较小值 "
                f"(插件={configured_limit}, 模型={provider_limit}, 生效={effective_limit})"
            )
        return effective_limit

    def _get_provider_max_context_tokens(self, chat_id: str) -> int:
        """读取当前会话绑定模型的上下文上限，读取失败或未配置时返回 0。"""
        if not self.astr_context:
            return 0

        try:
            provider = self.astr_context.get_using_provider(chat_id)
            if not provider:
                return 0

            provider_config = getattr(provider, "provider_config", {}) or {}
            if not isinstance(provider_config, dict):
                return 0

            value = provider_config.get("max_context_tokens", 0)
            if isinstance(value, str):
                value = value.strip()
                if not value:
                    return 0
                return int(value)
            if isinstance(value, (int, float)):
                return int(value)
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 读取模型上下文上限失败: {e}")

        return 0

    def _is_forgetting_timeout(self, chat_id: str) -> bool:
        """
        检查是否超过遗忘时间上限。

        Args:
            chat_id: 会话ID

        Returns:
            bool: 是否超时需要强制压缩
        """
        forgetting_timeout = self.config_manager.context_forgetting_timeout
        if forgetting_timeout <= 0:
            return False

        last_time = self._last_compression_time.get(chat_id, 0.0)
        if last_time == 0.0:
            # 从未压缩过，检查会话中最早消息的时间
            ledger = self._get_or_create_ledger(chat_id)
            with self._lock:
                messages = ledger["messages"]
                if not messages:
                    return False
                earliest_ts = messages[0].get("timestamp", 0)
                # 如果最早消息距今超过遗忘时间，需要压缩
                return (time.time() - earliest_ts) > forgetting_timeout
        else:
            return (time.time() - last_time) > forgetting_timeout

    def _compress_context(self, chat_id: str):
        """
        执行分级上下文压缩算法。

        算法步骤：
        1. 从最后一条消息往前保留10K Token的消息正文（非工具消息）
        2. 剩余消息中，工具调用+工具结果只保留10K Token，超出部分成批删除
        3. 超出保留范围的普通消息全部丢弃

        这确保了最近的对话上下文始终可用，同时工具调用历史也有一定保留。
        """
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            messages = ledger["messages"]
            if not messages:
                return

            # 获取保留Token预算
            content_budget = self.config_manager.context_content_retain_tokens
            tool_budget = self.config_manager.context_tool_retain_tokens

            # === 第一步：从末尾往前保留 content_budget Token 的正文消息 ===
            retained_content_msgs = []
            content_tokens_used = 0

            # 从最新消息往前遍历
            for msg in reversed(messages):
                is_tool = msg.get("role") == "tool"
                has_tool_calls = bool(msg.get("tool_calls"))

                if is_tool or has_tool_calls:
                    # 工具消息暂时跳过，后续单独处理
                    continue

                msg_tokens = self._count_message_tokens(msg)
                if content_tokens_used + msg_tokens <= content_budget:
                    retained_content_msgs.append(msg)
                    content_tokens_used += msg_tokens
                else:
                    # 预算用尽，停止保留正文消息
                    break

            # 恢复时间顺序
            retained_content_msgs.reverse()

            # === 第二步：从末尾往前保留 tool_budget Token 的工具消息 ===
            retained_tool_msgs = []
            tool_tokens_used = 0

            for msg in reversed(messages):
                is_tool = msg.get("role") == "tool"
                has_tool_calls = bool(msg.get("tool_calls"))

                if not is_tool and not has_tool_calls:
                    continue

                msg_tokens = self._count_message_tokens(msg)
                if tool_tokens_used + msg_tokens <= tool_budget:
                    retained_tool_msgs.append(msg)
                    tool_tokens_used += msg_tokens
                else:
                    break

            # 恢复时间顺序
            retained_tool_msgs.reverse()

            # === 第三步：合并并按时间排序 ===
            all_retained = retained_content_msgs + retained_tool_msgs
            all_retained.sort(key=lambda m: m.get("timestamp", 0))

            # 确保至少保留 MIN_RETAIN_COUNT 条消息
            if len(all_retained) < self.MIN_RETAIN_COUNT and len(messages) >= self.MIN_RETAIN_COUNT:
                # 如果压缩后消息太少，回退到保留最新的 MIN_RETAIN_COUNT 条
                all_retained = messages[-self.MIN_RETAIN_COUNT:]
            elif len(all_retained) == 0 and messages:
                # 极端情况：至少保留最后一条
                all_retained = messages[-1:]

            original_count = len(messages)
            ledger["messages"] = all_retained

            # 更新压缩时间戳
            self._last_compression_time[chat_id] = time.time()

            logger.info(
                f"AngelHeart[{chat_id}]: 上下文压缩完成 "
                f"({original_count} -> {len(all_retained)} 条消息, "
                f"正文保留 {content_tokens_used} tokens, "
                f"工具保留 {tool_tokens_used} tokens)"
            )

    def _count_message_tokens(self, msg: Dict) -> int:
        """
        估算单条消息的Token数量。

        Args:
            msg: 消息字典

        Returns:
            int: 估算的Token数量
        """
        total = 0
        content = msg.get("content", "")

        if isinstance(content, str):
            total += self._count_tokens_in_text(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type", "")
                    if item_type == "text":
                        total += self._count_tokens_in_text(item.get("text", ""))
                    elif item_type == "image_url":
                        total += 85

        # 计算其他字符串字段
        for key, value in msg.items():
            if key not in ["content", "timestamp", "is_processed"] and isinstance(value, str):
                total += self._count_tokens_in_text(value)

        return total

    def _prune_to_essentials(self, chat_id: str):
        """
        精简会话消息，仅保留最新的7条非工具消息。
        这是一个兜底的极端清理方法，当 _compress_context 不足以控制内存时使用。

        Args:
            chat_id: 会话ID
        """
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            # 1. 获取当前会话的所有消息
            all_messages = ledger["messages"]

            # 2. 筛选出所有非工具消息（role不为tool且不含tool_calls）
            non_tool_messages = []
            for msg in all_messages:
                is_tool = msg.get("role") == "tool"
                has_tool_calls = bool(msg.get("tool_calls"))
                if not is_tool and not has_tool_calls:
                    non_tool_messages.append(msg)

            # 3. 如果非工具消息数量大于7，则只保留时间戳最新的7条
            if len(non_tool_messages) > 7:
                # 按时间戳降序排序（最新的在前）
                non_tool_messages.sort(key=lambda m: m.get("timestamp", 0), reverse=True)
                # 只保留最新的7条
                essential_messages = non_tool_messages[:7]
                # 按时间戳升序排序（恢复原始顺序）
                essential_messages.sort(key=lambda m: m.get("timestamp", 0))

                # 4. 用这批"精华消息"完全替换内存中该会话的整个消息列表
                ledger["messages"] = essential_messages
                logger.info(f"AngelHeart[{chat_id}]: 已精简会话消息，保留最新的7条非工具消息")

            # 更新压缩时间戳
            self._last_compression_time[chat_id] = time.time()

    def _estimate_tokens(self, chat_id: str) -> int:
        """
        估算当前会话的Token数量

        Args:
            chat_id: 会话ID

        Returns:
            int: 估算的Token数量
        """
        ledger = self._get_or_create_ledger(chat_id)
        with self._lock:
            total_tokens = 0
            messages = ledger["messages"]

            for msg in messages:
                # 获取消息内容
                content = msg.get("content", "")

                if isinstance(content, str):
                    # 如果是字符串，直接计算
                    total_tokens += self._count_tokens_in_text(content)
                elif isinstance(content, list):
                    # 如果是列表，遍历每个元素
                    for item in content:
                        if isinstance(item, dict):
                            item_type = item.get("type", "")
                            if item_type == "text":
                                text = item.get("text", "")
                                total_tokens += self._count_tokens_in_text(text)
                            elif item_type == "image_url":
                                # 图片内容估算为固定Token数
                                total_tokens += 85  # OpenAI的图片Token估算

                # 添加其他字段的Token估算
                for key, value in msg.items():
                    if key not in ["content", "timestamp", "is_processed"] and isinstance(value, str):
                        total_tokens += self._count_tokens_in_text(value)

            return total_tokens

    def _count_tokens_in_text(self, text: str) -> int:
        """
        计算文本中的Token数量

        Args:
            text: 要计算的文本

        Returns:
            int: Token数量
        """
        if not text:
            return 0

        # 基于中英文字符不同权重的Token估算逻辑
        chinese_chars = 0
        english_chars = 0

        for char in text:
            # 中文字符（包括中文标点）
            if '\u4e00' <= char <= '\u9fff' or char in '，。！？；：""''（）【】《》':
                chinese_chars += 1
            else:
                english_chars += 1

        # 估算规则（用户提供）：
        # 1. 中文字符：每个字符约0.6个Token
        # 2. 英文字符：每个字符约0.3个Token
        # 3. 总Token数向上取整
        tokens = chinese_chars * 0.6 + english_chars * 0.3

        return int(tokens) + (1 if tokens % 1 > 0 else 0)
    BROKEN_IMAGE_CAPTION = "图裂了，图片无法打开，可能是网络问题或者格式不支持"
    EXPIRED_IMAGE_CAPTION = "因为时间问题，图片缓存内容已经丢失"
