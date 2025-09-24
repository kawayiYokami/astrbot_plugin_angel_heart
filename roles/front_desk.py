"""
AngelHeart 插件 - 前台角色 (FrontDesk)
负责接收并缓存所有合规消息。
"""

import time
from typing import Dict, List
import copy

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from .secretary import Secretary  # 用于类型提示

# 导入公共工具函数和 ConversationLedger
from ..core.utils import convert_content_to_string, format_message_for_llm
from ..core.conversation_ledger import ConversationLedger


class FrontDesk:
    """
    前台角色 - 专注的消息接收与缓存员
    """

    def __init__(self, config_manager, secretary: Secretary, conversation_ledger: ConversationLedger):
        """
        初始化前台角色。

        Args:
            config_manager: 配置管理器实例。
            secretary (Secretary): 关联的秘书实例，用于处理通知。
            conversation_ledger (ConversationLedger): 对话总账实例，用于存储消息。
        """
        self._config_manager = config_manager
        self.secretary = secretary
        self.conversation_ledger = conversation_ledger

        # 移除本地缓存：存储每个会话的未处理用户消息
        # self.unprocessed_messages: Dict[str, List[Dict]] = {}

        # 新增状态：记录每个会话的闭嘴截止时间戳
        self.silenced_until: Dict[str, float] = {}

    def cache_message(self, chat_id: str, event):
        """
        前台职责：缓存新消息

        Args:
            chat_id (str): 会话ID。
            event: 消息事件对象。
        """
        # 直接将消息添加到 ConversationLedger
        # 不再需要手动清理过期消息，因为 ConversationLedger.add_message 会自动处理
        new_message = {
            "role": "user",
            "content": event.get_message_outline(),
            "sender_id": event.get_sender_id(),
            "sender_name": event.get_sender_name(),
            "timestamp": event.get_timestamp() if hasattr(event, 'get_timestamp') and event.get_timestamp() else time.time(), # 使用事件自带的时间戳，如果不可用则使用当前时间
        }
        self.conversation_ledger.add_message(chat_id, new_message)
        logger.debug(
            f"AngelHeart[{chat_id}]: 消息已添加到 Ledger。"
        )
        # 增强日志：打印最新缓存的消息内容（截取前100个字符）
        latest_content = new_message.get("content", "")[:100]
        logger.debug(
            f"AngelHeart[{chat_id}]: 最新添加消息内容: {latest_content}"
        )



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
        # 设计意图：此处的缓存模拟了 AI 的"看群时间"或"短期记忆"。
        # 在 'analysis_on_mention_only' 模式下，只要在这段记忆中出现过一次别名，
        # AI 就会认为当前对话值得关注，从而触发后续的分析。
        # 这并非要求每条消息都包含别名，而是模拟一种"被唤醒后持续观察"的行为。
        if self.config_manager.analysis_on_mention_only:
            alias_str = self.config_manager.alias
            if not alias_str:
                return

            aliases = [name.strip() for name in alias_str.split('|') if name.strip()]
            # 从 ConversationLedger 获取所有消息（包括历史消息），因为AI应该记住被呼唤的状态
            historical_context, unprocessed_dialogue, _ = self.conversation_ledger.get_context_snapshot(chat_id)
            all_messages = historical_context + unprocessed_dialogue

            found_mention = any(
                alias in convert_content_to_string(msg.get("content", ""))
                for msg in all_messages
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


    async def rewrite_prompt_for_llm(self, chat_id: str, req):
        """
        为 LLM 重写 ProviderRequest 的 prompt，将对话上下文整合为单一的格式化字符串。

        Args:
            chat_id (str): 会话ID。
            req: ProviderRequest 对象。
        """
        # 1. 获取对话快照
        historical_context, unprocessed_dialogue, _ = self.conversation_ledger.get_context_snapshot(chat_id)

        # 2. 获取决策信息（用于确定人格名称）
        decision = self.secretary.get_decision(chat_id)
        persona_name = decision.persona_name if decision else 'AngelHeart'

        # 3. 构建新的 prompt 列表
        new_prompt_parts = [f"你正在一个群聊中聊天，你是助理。你的名字是 {persona_name}。"]

        # 添加历史记录
        if historical_context:
            new_prompt_parts.append("\n--- 对话历史记录 (已处理) ---")
            for msg in historical_context:
                formatted_msg = format_message_for_llm(msg, persona_name)
                new_prompt_parts.append(formatted_msg)

        # 添加未回应的对话
        if unprocessed_dialogue:
            new_prompt_parts.append("\n--- 未回应的对话 ---")
            for msg in unprocessed_dialogue:
                formatted_msg = format_message_for_llm(msg, persona_name)
                new_prompt_parts.append(formatted_msg)

        # 合并决策上下文（回复策略）到 prompt
        if hasattr(req, 'angelheart_decision_context') and req.angelheart_decision_context:
            new_prompt_parts.append(f"\n{req.angelheart_decision_context}")

        # 添加最终指令
        new_prompt_parts.append("\n请根据以上对话内容，结合你的回答策略，做出回应。")

        # 4. 更新 ProviderRequest 对象
        req.prompt = "\n".join(new_prompt_parts)
        req.contexts = []  # 清空 contexts
        # req.system_prompt 保持不变，不进行任何操作

        logger.debug(f"AngelHeart[{chat_id}]: LLM Prompt 已重写，决策上下文已合并到 prompt。")

    @config_manager.setter
    def config_manager(self, value):
        self._config_manager = value
