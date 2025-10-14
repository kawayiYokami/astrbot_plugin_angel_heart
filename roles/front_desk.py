"""
AngelHeart 插件 - 前台角色 (FrontDesk)
负责接收并缓存所有合规消息。
"""

import time
import os
from typing import Dict
import copy

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image  # 导入 Image 和 Plain 组件
from .secretary import Secretary  # 用于类型提示
from typing import Any  # 导入类型提示

# 导入公共工具函数和 ConversationLedger
from ..core.utils import convert_content_to_string, format_relative_time
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
        前台职责：使用消息概要作为主要正文，处理图片组件并缓存。

        Args:
            chat_id (str): 会话ID。
            event (AstrMessageEvent): 消息事件对象。
        """
        # 1. 获取消息概要作为主要正文
        outline = event.get_message_outline()
        text_content = outline if outline and outline.strip() else ""

        # 2. 获取 MessageChain 用于图片处理
        message_chain = event.get_messages()
        logger.debug(f"AngelHeart[{chat_id}]: 缓存消息，消息概要: '{text_content}'")

        # 3. 构建标准多模态 content 列表
        content_list = []
        if text_content:
            content_list.append({
                "type": "text",
                "text": text_content
            })

        # 4. 处理图片组件
        for component in message_chain:
            if isinstance(component, Image):
                # 首先尝试使用官方方法处理本地文件或可访问的URL
                try:
                    # 检查是否是本地文件或可访问的URL
                    url = component.url or component.file
                    if url and (url.startswith("file:///") or url.startswith("base64://") or os.path.exists(url or "")):
                        # 对于本地文件，直接使用官方方法
                        base64_data = await component.convert_to_base64()
                        if base64_data:
                            # 转换为 data URL 格式
                            if base64_data.startswith("base64://"):
                                image_data = base64_data.replace("base64://", "")
                            else:
                                image_data = base64_data
                            data_url = f"data:image/jpeg;base64,{image_data}"
                            content_list.append({
                                "type": "image_url",
                                "image_url": {"url": data_url}
                            })
                        else:
                            raise Exception("convert_to_base64 返回空值")
                    else:
                        # 对于网络URL，尝试下载，如果失败则跳过
                        base64_data = await component.convert_to_base64()
                        if base64_data:
                            # 转换为 data URL 格式
                            if base64_data.startswith("base64://"):
                                image_data = base64_data.replace("base64://", "")
                            else:
                                image_data = base64_data
                            data_url = f"data:image/jpeg;base64,{image_data}"
                            content_list.append({
                                "type": "image_url",
                                "image_url": {"url": data_url}
                            })
                        else:
                            raise Exception("网络图片下载失败")
                except Exception as e:
                    # 图片处理失败时，用文本占位符替换，避免传递空或无效URL
                    original_url = component.url or component.file or "未知URL"
                    logger.debug(f"AngelHeart[{chat_id}]: 图片处理跳过，URL: {original_url}, 原因: {str(e)[:100]}")
                    # 不添加任何内容，完全跳过图片，保持原有文本消息不变

        # 5. 如果没有内容，创建一个空文本
        if not content_list:
            content_list.append({
                "type": "text",
                "text": ""
            })

        # 6. 构建完整的消息字典
        new_message = {
            "role": "user",
            "content": content_list,  # 标准多模态列表
            "sender_id": event.get_sender_id(),
            "sender_name": event.get_sender_name(),
            "timestamp": event.get_timestamp() if hasattr(event, 'get_timestamp') and event.get_timestamp() else time.time(),
        }

        # 7. 将消息添加到 Ledger
        self.conversation_ledger.add_message(chat_id, new_message)
        logger.info(f"AngelHeart[{chat_id}]: 消息已缓存，使用概要作为正文，包含 {len(content_list)} 个组件。")



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
        重构请求体，实现完整的对话历史格式化和指令注入。
        """
        logger.debug(f"AngelHeart[{chat_id}]: 开始重构LLM请求体...")

        # 1. 获取决策，如果不存在则无法继续
        decision = self.secretary.get_decision(chat_id)
        if not decision:
            logger.debug(f"AngelHeart[{chat_id}]: 私聊不参与重构。")
            return

        # 2. 使用决策中保存的对话快照（而不是重新获取，避免被标记为已处理的消息丢失）
        recent_dialogue = decision.recent_dialogue

        # 获取历史对话用于构建完整上下文
        historical_context, _, _ = partition_dialogue(self.conversation_ledger, chat_id)

        # 生成聚焦指令
        final_prompt_str = format_final_prompt(recent_dialogue, decision)

        # 3. 准备完整的对话历史 (Context)
        full_history = historical_context + recent_dialogue

        # 4. 遍历 full_history 并动态注入元数据
        new_contexts = []
        for msg in full_history:
            if msg.get("role") == "user":
                # 对于 user 消息，生成 header 并注入到 content 中
                header = f"[群友: {msg.get('sender_name', '成员')}/{msg.get('sender_id', 'Unknown')}]{format_relative_time(msg.get('timestamp'))}: "

                # 使用深拷贝复制 content 列表以进行修改，确保不污染原始数据
                new_content = copy.deepcopy(msg.get("content", []))
                if isinstance(new_content, list) and new_content:
                    # 找到第一个 text 组件并注入 header
                    found_text = False
                    for item in new_content:
                        if item.get("type") == "text":
                            item["text"] = header + item.get("text", "")
                            found_text = True
                            break
                    # 如果没有 text 组件（纯图片），则在开头插入一个
                    if not found_text:
                        new_content.insert(0, {"type": "text", "text": header.strip()})

                new_contexts.append({
                    "role": "user",
                    "content": new_content
                })
            else:
                # assistant 消息保持不变
                new_contexts.append(msg)

        # 5. 完全覆盖原有的 contexts
        req.contexts = new_contexts

        # 6. 聚焦指令并赋值给 req.prompt
        req.prompt = final_prompt_str

        # 7. 清空 image_urls 并注入系统提示词
        req.image_urls = []  # 图片已在 contexts 中

        # 注入系统提示词
        persona_name = decision.persona_name if decision else 'AngelHeart'
        alias = self.config_manager.alias
        original_system_prompt = getattr(req, 'system_prompt', '')
        new_system_prompt = f"{original_system_prompt}\n\n你正在一个群聊中扮演 '{persona_name}' 的角色，你的别名是 '{alias}'。"
        req.system_prompt = new_system_prompt

        logger.info(f"AngelHeart[{chat_id}]: LLM请求体已重构，采用'完整上下文+聚焦指令'模式。")

    @config_manager.setter
    def config_manager(self, value):
        self._config_manager = value
