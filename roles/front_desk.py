"""
AngelHeart 插件 - 前台角色 (FrontDesk)
负责接收并缓存所有合规消息。
"""

import time
from typing import Dict, List
import copy

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image, Plain  # 导入 Image 和 Plain 组件
from .secretary import Secretary  # 用于类型提示
from typing import List, Dict, Any  # 导入类型提示

# 导入公共工具函数和 ConversationLedger
from ..core.utils import convert_content_to_string, format_message_for_llm, format_relative_time
from ..core.utils import partition_dialogue, format_final_prompt
from ..core.conversation_ledger import ConversationLedger
from ..core.image_processor import ImageProcessor


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

        # 初始化图片处理器
        self.image_processor = ImageProcessor()

    async def cache_message(self, chat_id: str, event: AstrMessageEvent):
        """
        前台职责：将 MessageChain 转换为标准多模态 content 列表并缓存。

        Args:
            chat_id (str): 会话ID。
            event (AstrMessageEvent): 消息事件对象。
        """
        # 1. 获取原始 MessageChain
        message_chain = event.get_messages()
        logger.debug(f"AngelHeart[{chat_id}]: 缓存消息，原始 MessageChain: {[repr(comp) for comp in message_chain]}")

        # 2. 转换为标准多模态 content 列表
        content_list = []
        for component in message_chain:
            if isinstance(component, Plain):
                content_list.append({
                    "type": "text",
                    "text": component.text
                })
            elif isinstance(component, Image):
                # 调用图片处理器异步转换URL为Data URL
                data_url = await self.image_processor.convert_url_to_data_url(component.url)
                content_list.append({
                    "type": "image_url",
                    "image_url": {"url": data_url}
                })
            else:
                # 处理其他类型的组件（At, Face 等）
                try:
                    fallback_text = str(component)
                    if fallback_text:
                        content_list.append({
                            "type": "text",
                            "text": fallback_text
                        })
                    logger.warning(f"AngelHeart[{chat_id}]: 遇到未支持的组件类型 {type(component).__name__}，已转换为文本: '{fallback_text}'")
                except Exception as e:
                    logger.error(f"AngelHeart[{chat_id}]: 转换未知组件 {type(component).__name__} 时出错: {e}")
                    content_list.append({
                        "type": "text",
                        "text": f"[{type(component).__name__}]"
                    })

        # 3. 构建完整的消息字典
        new_message = {
            "role": "user",
            "content": content_list,  # 标准多模态列表
            "sender_id": event.get_sender_id(),
            "sender_name": event.get_sender_name(),
            "timestamp": event.get_timestamp() if hasattr(event, 'get_timestamp') and event.get_timestamp() else time.time(),
        }

        # 4. 将消息添加到 Ledger
        self.conversation_ledger.add_message(chat_id, new_message)
        logger.info(f"AngelHeart[{chat_id}]: 标准多模态消息已缓存，包含 {len(content_list)} 个组件。")



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
        await self.cache_message(chat_id, event)

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


    async def rewrite_prompt_for_llm(self, chat_id: str, req: Any):
        """
        重构请求体，以实现将指令作为最后一条用户消息注入的精确调用方式。
        """
        logger.debug(f"AngelHeart[{chat_id}]: 开始重构LLM请求体...")

        # 1. 获取决策，如果不存在则无法继续
        decision = self.secretary.get_decision(chat_id)
        if not decision:
            logger.debug(f"AngelHeart[{chat_id}]: 私聊不参与重构。")
            return

        # 2. 使用新的通用函数分割对话
        historical_context, recent_dialogue, _ = utils.partition_dialogue(self.conversation_ledger, chat_id)

        # 3. 准备完整的对话历史 (Context)
        # 注意：我们不再修改原始消息，而是直接使用它们
        final_context_messages = historical_context + recent_dialogue

        # 4. 调用新的格式化函数，生成最终的用户指令字符串 (Prompt)
        final_prompt_str = utils.format_final_prompt(recent_dialogue, decision)

        # 5. 将 Prompt 字符串包装成最后一条 user 消息，并附加到 Context 后面
        final_context_messages.append({
            "role": "user",
            "content": final_prompt_str
        })

        # 6. 完全覆盖原有的 contexts 和 prompt
        req.contexts = final_context_messages
        req.prompt = ""  # 主 prompt 清空，因为所有指令都在最后一条消息里
        req.image_urls = [] # 确保图片URL为空

        # 7. (可选) 注入系统提示词
        persona_name = decision.persona_name if decision else 'AngelHeart'
        alias = self.config_manager.alias
        original_system_prompt = getattr(req, 'system_prompt', '')
        new_system_prompt = f"{original_system_prompt}\n\n你正在一个群聊中扮演 '{persona_name}' 的角色，你的别名是 '{alias}'。"
        req.system_prompt = new_system_prompt

        logger.info(f"AngelHeart[{chat_id}]: LLM请求体已重构，采用最终指令注入模式。")

    @config_manager.setter
    def config_manager(self, value):
        self._config_manager = value
