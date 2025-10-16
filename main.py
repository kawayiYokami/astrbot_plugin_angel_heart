
"""
AngelHeart插件 - 天使心智能群聊/私聊交互插件

基于AngelHeart轻量级架构设计，实现两级AI协作体系。
采用"前台缓存，秘书定时处理"模式：
- 前台：接收并缓存所有合规消息
- 秘书：定时分析缓存内容，决定是否回复
"""

import time
import json

from astrbot.api.star import Star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.core.star.context import Context
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
from astrbot.core.message.components import Plain, At, AtAll, Reply

from .core.config_manager import ConfigManager
from .roles.front_desk import FrontDesk
from .roles.secretary import Secretary
from .core.utils import strip_markdown
from .core.angel_heart_context import AngelHeartContext

class AngelHeartPlugin(Star):
    """AngelHeart插件 - 专注的智能回复员"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config_manager = ConfigManager(config or {})
        self.context = context
        self._whitelist_cache = self._prepare_whitelist()

        # -- 创建 AngelHeartContext 全局上下文（包含 ConversationLedger）--
        self.angel_context = AngelHeartContext(self.config_manager)

        # -- 角色实例 --
        # 创建秘书和前台，通过全局上下文传递依赖
        self.secretary = Secretary(
            self.config_manager,
            self.context,
            self.angel_context
        )
        self.front_desk = FrontDesk(
            self.config_manager,
            self.angel_context
        )

        # 建立必要的相互引用
        self.front_desk.secretary = self.secretary

        logger.info("💖 AngelHeart智能回复员初始化完成 (事件扣押机制 V2 已启用)")

    # --- 核心事件处理 ---
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE, priority=200)
    async def smart_reply_handler(self, event: AstrMessageEvent, *args, **kwargs):
        """智能回复员 - 事件入口：处理缓存或在唤醒时清空缓存"""

        # 使用 _should_process 方法来判断是否需要处理此消息
        if not self._should_process(event):
            # 如果 _should_process 返回 False，直接返回，不进行任何处理
            return

        # 如果是需要处理的消息，则委托给前台缓存
        await self.front_desk.handle_event(event)


    @filter.on_llm_request(priority=0) # 默认优先级
    async def inject_oneshot_decision_on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """在LLM请求时，一次性注入由秘书分析得出的决策上下文"""
        chat_id = event.unified_msg_origin

        # 示例：读取 angelheart_context（供其他插件参考）
        if hasattr(event, 'angelheart_context'):
            try:
                context = json.loads(event.angelheart_context)
                # 检查上下文是否包含错误信息
                if context.get('error'):
                    logger.warning(f"AngelHeart[{chat_id}]: 上下文包含错误: {context['error']}")

                # 安全地提取数据
                chat_records = context.get('chat_records', [])
                secretary_decision = context.get('secretary_decision', {})
                needs_search = context.get('needs_search', False)

                logger.debug(f"AngelHeart[{chat_id}]: 读取到上下文 - 记录数: {len(chat_records)}, 决策: {secretary_decision.get('reply_strategy', '未知')}, 需搜索: {needs_search}")
            except json.JSONDecodeError as e:
                logger.warning(f"AngelHeart[{chat_id}]: 解析 angelheart_context JSON 失败: {e}")
            except Exception as e:
                logger.warning(f"AngelHeart[{chat_id}]: 处理 angelheart_context 时发生意外错误: {e}")

        # 如果启用群聊上下文增强，则跳过此方法（使用新的 prompt 重写方式）
        if self.config_manager.group_chat_enhancement:
            return

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

        # 5. 根据是否启用增强模式，选择不同的注入方式
        if self.config_manager.group_chat_enhancement:
            # 增强模式：将决策上下文存储到临时属性中，由 rewrite_prompt_for_llm 处理
            req.angelheart_decision_context = decision_context
            logger.debug(f"AngelHeart[{chat_id}]: 已将决策上下文存储到临时属性中。")
        else:
            # 传统模式：注入到 req.system_prompt
            # 遵循 AstrBot 框架的设计，system_prompt 用于传递不会被存入历史记录的系统级指令
            if req.system_prompt:
                # 如果 system_prompt 已有内容，则追加
                req.system_prompt += f"\n{decision_context}"
            else:
                # 否则，直接赋值
                req.system_prompt = decision_context
            logger.debug(f"AngelHeart[{chat_id}]: 已将决策上下文注入到 system_prompt。")

    @filter.on_llm_request(priority=-50) # 在决策注入之后，日志之前执行
    async def delegate_prompt_rewriting(self, event: AstrMessageEvent, req: ProviderRequest):
        """将 Prompt 重写任务委托给 FrontDesk 处理"""
        chat_id = event.unified_msg_origin

        # 如果未启用群聊上下文增强，则跳过此方法（使用旧的 system_prompt 注入方式）
        if not self.config_manager.group_chat_enhancement:
            return

        await self.front_desk.rewrite_prompt_for_llm(chat_id, req)


    # --- 内部方法 ---
    def reload_config(self, new_config: dict):
        """重新加载配置"""
        self.config_manager = ConfigManager(new_config or {})
        # 更新角色实例的配置管理器
        self.secretary.config_manager = self.config_manager
        self.front_desk.config_manager = self.config_manager
        # 重新加载LLM分析器的配置
        self.secretary.llm_analyzer.reload_config(self.config_manager)
        self._whitelist_cache = self._prepare_whitelist()

        # 更新 ConversationLedger 的缓存过期时间
        # 注意：这里我们不能直接修改 ConversationLedger 的 cache_expiry
        # 因为它是初始化时设置的。我们可以考虑重新创建实例或添加一个更新方法
        # 为了简单，我们暂时只记录日志，实际更新需要更复杂的逻辑
        logger.info(f"AngelHeart: 配置已更新。分析间隔: {self.config_manager.analysis_interval}秒, 缓存过期时间: {self.config_manager.cache_expiry}秒")

    def _get_plain_chat_id(self, unified_id: str) -> str:
        """从 unified_msg_origin 中提取纯净的聊天ID (QQ号)"""
        parts = unified_id.split(':')
        return parts[-1] if parts else ""

    def _should_process(self, event: AstrMessageEvent) -> bool:
        """检查是否需要处理此消息"""
        chat_id = event.unified_msg_origin

        try:
            # 1. 检查是否为@消息，区分@自己和@全体成员
            if event.is_at_or_wake_command:
                # 预缓存ID以提高性能
                self_id = str(event.get_self_id())

                # 检查是否为需要特殊处理的@消息（At机器人或引用机器人消息）
                is_at_self = False
                has_at_all = False

                try:
                    messages = event.get_messages()
                    for message in messages:
                        if isinstance(message, AtAll):
                            has_at_all = True
                        elif isinstance(message, At) and str(message.qq) == self_id:
                            is_at_self = True
                        elif isinstance(message, Reply) and str(message.sender_id) == self_id:
                            is_at_self = True
                except Exception as e:
                    logger.warning(f"AngelHeart[{chat_id}]: 解析消息链异常: {e}")
                    # 异常时保守处理，视为非@自己消息
                    return False

                # 如果是@自己或引用自己，应该处理（返回True）
                if is_at_self:
                    logger.debug(f"AngelHeart[{chat_id}]: 检测到@自己的消息，准备处理...")
                    return True
                # 如果是@全体成员，不应该处理（返回False）
                elif has_at_all:
                    logger.debug(f"AngelHeart[{chat_id}]: 检测到@全体成员消息，已忽略")
                    return False
                # 如果是指令（非@），不应该处理（返回False）
                else:
                    logger.debug(f"AngelHeart[{chat_id}]: 检测到指令或@他人消息，已忽略")
                    return False

            if event.get_sender_id() == event.get_self_id():
                logger.debug(f"AngelHeart[{chat_id}]: 消息由自己发出, 已忽略")
                return False

            # 2. 忽略空消息
            if not event.get_message_outline().strip():
                logger.debug(f"AngelHeart[{chat_id}]: 消息内容为空, 已忽略")
                return False

            # 3. (可选) 检查白名单
            if self.config_manager.whitelist_enabled:
                plain_chat_id = self._get_plain_chat_id(chat_id)
                if plain_chat_id not in self._whitelist_cache:
                    logger.debug(f"AngelHeart[{chat_id}]: 会话未在白名单中, 已忽略")
                    return False

            logger.debug(f"AngelHeart[{chat_id}]: 消息通过所有前置检查, 准备处理...")
            return True

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: _should_process方法执行异常: {e}", exc_info=True)
            return False  # 异常时保守处理，不处理消息

    @filter.on_decorating_result(priority=-200)
    async def strip_markdown_on_decorating_result(self, event: AstrMessageEvent, *args, **kwargs):
        """
        在消息发送前，对消息链中的文本内容进行Markdown清洗，并检测错误信息。
        """
        chat_id = event.unified_msg_origin
        try:
            logger.debug(f"AngelHeart[{chat_id}]: 开始清洗消息链中的Markdown格式...")

            # 从 event 对象中获取消息链
            message_chain = event.get_result().chain

            # 1. 检测 AstrBot 错误信息，如果是错误信息则停止发送
            full_text_content = ""
            for component in message_chain:
                if isinstance(component, Plain):
                    if component.text:
                        full_text_content += component.text
                elif hasattr(component, 'data') and isinstance(component.data, dict):
                    text_content = component.data.get('text', '')
                    if text_content:
                        full_text_content += text_content

            if self._is_astrbot_error_message(full_text_content):
                logger.info(f"AngelHeart[{chat_id}]: 检测到 AstrBot 错误信息，清空消息链。")
                # 清空消息链，这样 RespondStage 就会跳过发送
                result = event.get_result()
                if result:
                    result.chain = []  # 清空消息链
                return

            # 2. 遍历消息链中的每个元素，进行 Markdown 清洗
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
        finally:
            # 在消息发送前，无论成功或失败，都释放处理锁
            await self.angel_context.release_chat_processing(chat_id)
            logger.info(f"AngelHeart[{chat_id}]: 任务处理完成，已在消息发送前释放处理锁。")

    def _prepare_whitelist(self) -> set:
        """预处理白名单，将其转换为 set 以获得 O(1) 的查找性能。"""
        return {str(cid) for cid in self.config_manager.chat_ids}

    @filter.after_message_sent()
    async def clear_oneshot_decision_on_message_sent(self, event: AstrMessageEvent, *args, **kwargs):
        """在消息成功发送后，清理一次性决策缓存并更新计时器"""
        chat_id = event.unified_msg_origin

        # 1. 从秘书缓存中获取决策
        decision = self.secretary.get_decision(chat_id)

        # 2. 如果决策有效，使用其边界时间戳来推进 Ledger 状态
        if decision and hasattr(decision, 'boundary_timestamp') and decision.boundary_timestamp > 0:
            self.angel_context.conversation_ledger.mark_as_processed(chat_id, decision.boundary_timestamp)

            # 3. 将AI的回复加入到对话总账中
            # 获取发送的消息内容
            sent_message = self._extract_sent_message_content(event)
            if sent_message:
                ai_message = {
                    "role": "assistant",
                    "content": sent_message,
                    "sender_id": str(event.get_self_id()),
                    "sender_name": decision.alias if decision else "AngelHeart",
                    "timestamp": time.time(),
                }
                self.angel_context.conversation_ledger.add_message(chat_id, ai_message)
                logger.debug(f"AngelHeart[{chat_id}]: AI回复已加入对话总账")

        # 5. 让秘书清理决策缓存
        await self.secretary.clear_decision(chat_id)
        # 6. 让秘书更新最后一次事件（回复）的时间戳
        await self.secretary.update_last_event_time(chat_id)

    def _extract_sent_message_content(self, event: AstrMessageEvent) -> str:
        """从事件中提取发送的消息内容"""
        try:
            # 从event的result中获取发送的消息内容
            if hasattr(event, 'get_result') and event.get_result():
                result = event.get_result()
                if hasattr(result, 'chain') and result.chain:
                    # 提取chain中的文本内容
                    text_parts = []
                    for component in result.chain:
                        if hasattr(component, 'text'):
                            text_parts.append(component.text)
                        elif hasattr(component, 'data') and isinstance(component.data, dict):
                            # 处理其他类型的组件
                            text_parts.append(str(component.data.get('text', '')))
                    return ''.join(text_parts).strip()

            # 如果上面的方法失败，尝试从event的message中获取
            if hasattr(event, 'get_message_outline'):
                return event.get_message_outline()

        except Exception as e:
            logger.warning(f"AngelHeart[{event.unified_msg_origin}]: 提取发送消息内容时出错: {e}")

        return ""

    def _is_astrbot_error_message(self, text_content: str) -> bool:
        """
        检测文本内容是否为 AstrBot 的错误信息。

        Args:
            text_content (str): 要检测的文本内容。

        Returns:
            bool: 如果是错误信息则返回 True，否则返回 False。
        """
        if not text_content:
            return False

        # 检测 AstrBot 错误信息的特征
        text_lower = text_content.lower()
        return (
            "astrbot 请求失败" in text_lower and
            "错误类型:" in text_lower and
            "错误信息:" in text_lower
        )


    async def on_destroy(self):
        """插件销毁时的清理工作"""
        logger.info("💖 AngelHeart 插件已销毁")
