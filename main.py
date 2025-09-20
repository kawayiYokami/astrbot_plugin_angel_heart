
"""
AngelHeart插件 - 天使心智能群聊/私聊交互插件

基于AngelHeart轻量级架构设计，实现两级AI协作体系。
采用"前台缓存，秘书定时处理"模式：
- 前台：接收并缓存所有合规消息
- 秘书：定时分析缓存内容，决定是否回复
"""

import asyncio
import time
from typing import Dict, List

from astrbot.api.star import Star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.core.star.context import Context
from astrbot.api import logger
from astrbot.core.message.components import Plain

from .core.config_manager import ConfigManager
from .models.analysis_result import SecretaryDecision
from .roles.front_desk import FrontDesk
from .roles.secretary import Secretary
from .core.utils import strip_markdown

class AngelHeartPlugin(Star):
    """AngelHeart插件 - 专注的智能回复员"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config_manager = ConfigManager(config or {})
        self.context = context

        # -- 角色实例 --
        # 先创建秘书，再创建前台，并将前台传递给秘书
        # 使用 None 作为占位符，以打破 Secretary 和 FrontDesk 在初始化时的循环依赖
        self.secretary = Secretary(self.config_manager, self.context, None) # 占位符，稍后设置
        self.front_desk = FrontDesk(self.config_manager, self.secretary)
        # 设置秘书的前台引用
        self.secretary.front_desk = self.front_desk

        logger.info("💖 AngelHeart智能回复员初始化完成 (同步轻量级架构)")

    # --- 核心事件处理 ---
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE, priority=200)
    async def smart_reply_handler(self, event: AstrMessageEvent, *args, **kwargs):
        """智能回复员 - 事件入口：将事件委托给前台处理"""
        # 前置检查
        if not self._should_process(event):
            return

        # 将事件处理完全委托给前台
        await self.front_desk.handle_event(event)

    # --- LLM Request Hook ---
    @filter.on_llm_request(priority=-100)
    async def inject_oneshot_decision_on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """在LLM请求时，一次性注入由秘书分析得出的决策上下文"""
        chat_id = event.unified_msg_origin

        # 1. 从秘书那里获取决策
        decision = self.secretary.get_decision(chat_id)

        # 2. 检查决策是否存在且有效
        if not decision or not decision.should_reply:
            # 如果没有决策或决策是不回复，则不进行任何操作
            return

        # 3. 严格检查参数合法性
        topic = getattr(decision, 'topic', None)
        strategy = getattr(decision, 'reply_strategy', None)
        reply_target = getattr(decision, 'reply_target', '')  # 获取回复目标，默认为空字符串
        persona_name = getattr(decision, 'persona_name', '')
        alias = getattr(decision, 'alias', 'AngelHeart')

        if not topic or not strategy:
            # 如果话题或策略为空，则不进行任何操作，防止污染
            logger.debug(f"AngelHeart[{chat_id}]: 决策参数不合法 (topic: {topic}, strategy: {strategy})，跳过决策注入。")
            return

        # 4. 构建补充提示词，包含人格信息和回复目标
        decision_context = f"\n\n---\n[经过我对聊天记录的分析，我得到了如下结论] 我现在的名字是 {persona_name} (别名: {alias})。我将围绕以下要点回复：\n- 核心话题: {topic}\n- 回复策略: {strategy}\n- 回复目标: {reply_target}"

        # 5. 注入到 req.system_prompt
        # 遵循 AstrBot 框架的设计，system_prompt 用于传递不会被存入历史记录的系统级指令
        if req.system_prompt:
            # 如果 system_prompt 已有内容，则追加
            req.system_prompt += f"\n{decision_context}"
        else:
            # 否则，直接赋值
            req.system_prompt = decision_context
        logger.debug(f"AngelHeart[{chat_id}]: 已将决策上下文注入到 system_prompt。")


    # --- 内部方法 ---
    def reload_config(self, new_config: dict):
        """重新加载配置"""
        self.config_manager = ConfigManager(new_config or {})
        # 更新角色实例的配置管理器
        self.secretary.config_manager = self.config_manager
        self.front_desk.config_manager = self.config_manager

        logger.info(f"AngelHeart: 配置已更新。分析间隔: {self.config_manager.analysis_interval}秒, 缓存过期时间: {self.config_manager.cache_expiry}秒")

    def _get_plain_chat_id(self, unified_id: str) -> str:
        """从 unified_msg_origin 中提取纯净的聊天ID (QQ号)"""
        parts = unified_id.split(':')
        return parts[-1] if parts else ""

    def _should_process(self, event: AstrMessageEvent) -> bool:
        """检查是否需要处理此消息"""
        chat_id = event.unified_msg_origin

        # 1. 忽略指令或@自己的消息
        if event.is_at_or_wake_command:
            # logger.info(f"AngelHeart[{chat_id}]: 消息是指令或@, 已忽略")
            return False
        if event.get_sender_id() == event.get_self_id():
            logger.info(f"AngelHeart[{chat_id}]: 消息由自己发出, 已忽略")
            return False

        # 2. 忽略空消息
        if not event.get_message_outline().strip():
            logger.info(f"AngelHeart[{chat_id}]: 消息内容为空, 已忽略")
            return False

        # 3. (可选) 检查白名单
        if self.config_manager.whitelist_enabled:
            plain_chat_id = self._get_plain_chat_id(chat_id)
            # 将配置中的ID列表转换为字符串以确保类型匹配
            whitelist = [str(cid) for cid in self.config_manager.chat_ids]

            if plain_chat_id not in whitelist:
                logger.info(f"AngelHeart[{chat_id}]: 会话未在白名单中, 已忽略")
                return False

        logger.info(f"AngelHeart[{chat_id}]: 消息通过所有前置检查, 准备处理...")
        return True

    @filter.on_decorating_result(priority=-200)
    async def strip_markdown_on_decorating_result(self, event: AstrMessageEvent, *args, **kwargs):
        """
        在消息发送前，对消息链中的文本内容进行Markdown清洗。
        """
        chat_id = event.unified_msg_origin
        logger.debug(f"AngelHeart[{chat_id}]: 开始清洗消息链中的Markdown格式...")

        # 从 event 对象中获取消息链
        message_chain = event.get_result().chain

        # 遍历消息链中的每个元素
        for component in message_chain:
            # 检查是否为 Plain 类型的消息组件
            if isinstance(component, Plain):
                original_text = component.text
                if original_text:
                    # 使用 strip_markdown 函数清洗文本
                    cleaned_text = strip_markdown(original_text)
                    # 更新消息组件中的文本内容
                    component.text = cleaned_text
                    logger.debug(f"AngelHeart[{chat_id}]: 已清洗文本组件: '{original_text}' -> '{cleaned_text}'")

        logger.debug(f"AngelHeart[{chat_id}]: 消息链中的Markdown格式清洗完成。")

    @filter.after_message_sent()
    async def clear_oneshot_decision_on_message_sent(self, event: AstrMessageEvent, *args, **kwargs):
        """在消息成功发送后，清理一次性决策缓存并更新计时器"""
        chat_id = event.unified_msg_origin
        # 让秘书清理决策缓存
        await self.secretary.clear_decision(chat_id)
        # 让秘书更新最后一次事件（回复）的时间戳
        await self.secretary.update_last_event_time(chat_id)

    async def on_destroy(self):
        """插件销毁时的清理工作"""
        logger.info("💖 AngelHeart 插件已销毁")
