"""
AngelHeart 插件 - 前台角色 (FrontDesk)
负责接收并缓存所有合规消息。
"""

import time
from typing import Dict, List

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from .secretary import Secretary  # 用于类型提示

# 导入公共工具函数
from ..core.utils import convert_content_to_string


class FrontDesk:
    """
    前台角色 - 专注的消息接收与缓存员
    """

    def __init__(self, config_manager, secretary: Secretary):
        """
        初始化前台角色。

        Args:
            config_manager: 配置管理器实例。
            secretary (Secretary): 关联的秘书实例，用于处理通知。
        """
        self._config_manager = config_manager
        self.secretary = secretary

        # 前台缓存：存储每个会话的未处理用户消息
        self.unprocessed_messages: Dict[str, List[Dict]] = {}

        # 新增状态：记录每个会话的闭嘴截止时间戳
        self.silenced_until: Dict[str, float] = {}

    def cache_message(self, chat_id: str, event):
        """
        前台职责：缓存新消息

        Args:
            chat_id (str): 会话ID。
            event: 消息事件对象。
        """
        # 在缓存新消息前，先清理该会话的过期消息
        self.clean_expired_messages(chat_id)

        if chat_id not in self.unprocessed_messages:
            self.unprocessed_messages[chat_id] = []

        new_message = {
            "role": "user",
            "content": event.get_message_outline(),
            "sender_id": event.get_sender_id(),
            "sender_name": event.get_sender_name(),
            "timestamp": time.time(),
        }
        self.unprocessed_messages[chat_id].append(new_message)
        logger.debug(
            f"AngelHeart[{chat_id}]: 前台已缓存消息。当前缓存数: {len(self.unprocessed_messages[chat_id])}"
        )
        # 增强日志：打印最新缓存的消息内容（截取前100个字符）
        latest_content = new_message.get("content", "")[:100]
        logger.debug(
            f"AngelHeart[{chat_id}]: 最新缓存消息内容: {latest_content}"
        )

    def get_messages(self, chat_id: str) -> List[Dict]:
        """
        获取指定会话的缓存消息副本。

        Args:
            chat_id (str): 会话ID。

        Returns:
            List[Dict]: 该会话的缓存消息列表副本。
        """
        # 清理操作已移至 cache_message，此处不再需要
        # self.clean_expired_messages(chat_id)

        # 返回消息列表的深拷贝，防止外部修改影响缓存
        import copy
        return copy.deepcopy(self.unprocessed_messages.get(chat_id, []))

    def clean_expired_messages(self, chat_id: str):
        """
        清理过期的缓存消息。

        Args:
            chat_id (str): 会话ID。
        """
        if chat_id not in self.unprocessed_messages:
            return

        current_time = time.time()
        original_count = len(self.unprocessed_messages[chat_id])

        # 使用列表推导式过滤掉过期的消息
        self.unprocessed_messages[chat_id] = [
            msg
            for msg in self.unprocessed_messages[chat_id]
            if "timestamp" not in msg
            or current_time - msg["timestamp"] <= self.config_manager.cache_expiry
        ]

        new_count = len(self.unprocessed_messages[chat_id])
        expired_count = original_count - new_count

        if expired_count > 0:
            logger.debug(
                f"AngelHeart[{chat_id}]: 前台清理了 {expired_count} 条过期消息，剩余 {new_count} 条"
            )

        # 如果会话消息列表为空，删除该会话的键
        if not self.unprocessed_messages[chat_id]:
            self.unprocessed_messages.pop(chat_id, None)

    async def handle_event(self, event: AstrMessageEvent):
        """
        处理新消息事件。这是前台的主要入口点。
        它会根据配置进行闭嘴、掌嘴、唤醒词检测，然后决定是否缓存和通知秘书。
        """
        chat_id = event.unified_msg_origin
        current_time = time.time()
        message_content = event.get_message_outline()

        # 1. 闭嘴状态检查 (最高优先级)
        if chat_id in self.silenced_until and current_time < self.silenced_until[chat_id]:
            remaining = self.silenced_until[chat_id] - current_time
            logger.info(f"AngelHeart[{chat_id}]: 处于闭嘴状态 (剩余 {remaining:.1f} 秒)，事件已终止。")
            event.stop_event() # 彻底中断
            return

        # 2. 消息合法性检查
        if not message_content.strip():
            # 对于空消息，我们只跳过自己的逻辑，不影响其他插件
            return

        # 3. 掌嘴词检测
        slap_words_str = self.config_manager.slap_words
        if slap_words_str:
            slap_words = [word.strip() for word in slap_words_str.split('|') if word.strip()]
            for word in slap_words:
                if word in message_content:
                    silence_duration = self.config_manager.silence_duration
                    self.silenced_until[chat_id] = current_time + silence_duration
                    logger.info(f"AngelHeart[{chat_id}]: 检测到掌嘴词 '{word}'，启动闭嘴模式 {silence_duration} 秒，事件已终止。")
                    event.stop_event() # 彻底中断
                    return

        # 4. 缓存消息
        self.cache_message(chat_id, event)

        # 5. 唤醒词检测
        # 设计意图：此处的缓存 (unprocessed_messages) 模拟了 AI 的“看群时间”或“短期记忆”。
        # 在 'analysis_on_mention_only' 模式下，只要在这段记忆中出现过一次别名，
        # AI 就会认为当前对话值得关注，从而触发后续的分析。
        # 这并非要求每条消息都包含别名，而是模拟一种“被唤醒后持续观察”的行为。
        if self.config_manager.analysis_on_mention_only:
            alias_str = self.config_manager.alias
            if not alias_str:
                return

            aliases = [name.strip() for name in alias_str.split('|') if name.strip()]
            current_cache = self.unprocessed_messages.get(chat_id, [])

            found_mention = any(
                alias in convert_content_to_string(msg.get("content", ""))
                for msg in current_cache
                for alias in aliases
            )

            if not found_mention:
                logger.info(f"AngelHeart[{chat_id}]: '仅呼唤'模式开启，但缓存中未检测到别名，不通知秘书。")
                return

        # 6. 通知秘书
        await self.secretary.process_notification(event)

    @property
    def config_manager(self):
        return self._config_manager

    @config_manager.setter
    def config_manager(self, value):
        self._config_manager = value
