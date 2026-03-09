"""
AngelHeart插件 - 天使心智能群聊/私聊交互插件

基于AngelHeart轻量级架构设计，实现两级AI协作体系。
采用"前台缓存，秘书定时处理"模式：
- 前台：接收并缓存所有合规消息
- 秘书：定时分析缓存内容，决定是否回复
"""

import asyncio
import time
import json
from concurrent.futures import InvalidStateError
from typing import Any

from astrbot.api.star import Star, Context, register
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.core.star.register import register_on_llm_response
from astrbot.core.star.star_tools import StarTools

try:
    from astrbot.api import logger
except ImportError:
    import logging

    logger = logging.getLogger(__name__)
from astrbot.core.message.components import Plain, At, AtAll, Reply
from astrbot.core.agent.message import TextPart

from .core.config_manager import ConfigManager
from .roles.front_desk import FrontDesk
from .roles.secretary import Secretary
from .core.utils import strip_markdown
from .core.utils.message_utils import serialize_message_chain
from .core.angel_heart_context import AngelHeartContext
from .core.utils.context_utils import format_decision_xml


@register("astrbot_plugin_angel_heart", "kawayiYokami", "天使心秘书，让astrbot拥有极其聪明，有分寸的群聊介入，和极其完备的群聊上下文管理", "0.8.11", "https://github.com/kawayiYokami/astrbot_plugin_angel_heart")
class AngelHeartPlugin(Star):
    """AngelHeart插件 - 专注的智能回复员"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config_manager = ConfigManager(config or {})
        self.context = context
        self._whitelist_cache = self._prepare_whitelist()

        # -- 获取插件数据目录 --
        plugin_data_dir = StarTools.get_data_dir()

        # -- 创建 AngelHeartContext 全局上下文（包含 ConversationLedger）--
        self.angel_context = AngelHeartContext(self.config_manager, self.context, plugin_data_dir)

        # -- 角色实例 --
        # 创建秘书和前台，通过全局上下文传递依赖
        self.secretary = Secretary(
            self.config_manager, self.context, self.angel_context
        )
        self.front_desk = FrontDesk(self.config_manager, self.angel_context)

        # 建立必要的相互引用
        self.front_desk.secretary = self.secretary

        # -- 工具修饰冷却记录 --
        self._tool_decoration_last_sent = {}  # {chat_id: timestamp}

        logger.info("💖 AngelHeart智能回复员初始化完成 (事件扣押机制 V2 已启用)")

    # --- 核心事件处理 ---
    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE,
        priority=-10,
    )
    async def smart_reply_handler(
        self, event: AstrMessageEvent, *args: Any, **kwargs: Any
    ) -> None:
        """智能回复员 - 事件入口：处理缓存或在唤醒时清空缓存"""

        # 使用 _should_process 方法来判断是否需要处理此消息
        if not self._should_process(event):
            # 如果 _should_process 返回 False，直接返回，不进行任何处理
            return

        # 如果是需要处理的消息，则委托给前台缓存
        await self.front_desk.handle_event(event)

    @filter.on_llm_request(priority=0)  # 默认优先级
    async def inject_oneshot_decision_on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """在LLM请求时，一次性注入由秘书分析得出的决策上下文"""
        chat_id = event.unified_msg_origin

        # 示例：读取 angelheart_context（供其他插件参考）
        if hasattr(event, "angelheart_context"):
            try:
                context = json.loads(event.angelheart_context)
                # 检查上下文是否包含错误信息
                if context.get("error"):
                    logger.warning(
                        f"AngelHeart[{chat_id}]: 上下文包含错误: {context['error']}"
                    )

                # 安全地提取数据
                chat_records = context.get("chat_records", [])
                secretary_decision = context.get("secretary_decision", {})
                needs_search = context.get("needs_search", False)

                logger.debug(
                    f"AngelHeart[{chat_id}]: 读取到上下文 - 记录数: {len(chat_records)}, 决策: {secretary_decision.get('reply_strategy', '未知')}, 需搜索: {needs_search}"
                )
            except json.JSONDecodeError as e:
                logger.warning(
                    f"AngelHeart[{chat_id}]: 解析 angelheart_context JSON 失败: {e}"
                )
            except (AttributeError, KeyError, TypeError) as e:
                logger.warning(
                    f"AngelHeart[{chat_id}]: 处理 angelheart_context 时发生意外错误: {e}"
                )

        # 1. 检查是否存在未执行的工具调用反馈
        # (这部分逻辑通常在 AstrBot 框架层面处理，但我们需要在这里确保拟人化反馈)
        # 注意：这里主要处理 on_llm_request，工具反馈通常在 on_llm_response

        # 2. 从秘书那里获取决策
        decision = self.secretary.get_decision(chat_id)

        # 2. 检查决策是否存在且有效
        if not decision or not decision.should_reply:
            # 如果没有决策或决策是不回复，则不进行任何操作
            return

        # 3. 严格检查参数合法性
        topic = getattr(decision, "topic", None)
        strategy = getattr(decision, "reply_strategy", None)

        if not topic or not strategy:
            # 如果话题或策略为空，则不进行任何操作，防止污染
            logger.debug(
                f"AngelHeart[{chat_id}]: 决策参数不合法 (topic: {topic}, strategy: {strategy})，跳过决策注入。"
            )
            return

        # 4. 构建系统决策 XML
        decision_xml = format_decision_xml(decision)

        # 5. 注入到 extra_user_content_parts（所有模式统一）
        if not hasattr(req, 'extra_user_content_parts'):
            req.extra_user_content_parts = []

        req.extra_user_content_parts.append(TextPart(text=decision_xml))
        logger.debug(f"AngelHeart[{chat_id}]: 已将决策注入到 extra_user_content_parts。")

    @filter.on_llm_request(priority=50)  # 在决策注入之后，日志之前执行
    async def delegate_prompt_rewriting(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """将 Prompt 重写任务委托给 FrontDesk 处理"""
        chat_id = event.unified_msg_origin

        # 如果未启用群聊上下文增强，则跳过此方法（使用旧的 system_prompt 注入方式）
        if not self.config_manager.group_chat_enhancement:
            return

        await self.front_desk.rewrite_prompt_for_llm(chat_id, event, req)

    # 捕获工具调用结果
    @register_on_llm_response()
    async def capture_tool_results(
        self, event: AstrMessageEvent, response: LLMResponse
    ):
        """捕获工具调用和结果，存储到天使之心对话总账，并处理拟人化反馈"""
        chat_id = event.unified_msg_origin

        # --- 原有逻辑：捕获工具结果 ---
        # 获取 ProviderRequest 中的 tool_calls_result
        provider_request = event.get_extra("provider_request")

        if provider_request and hasattr(provider_request, "tool_calls_result"):
            tool_results = provider_request.tool_calls_result

            if tool_results:
                # 确保 tool_results 是列表格式
                if isinstance(tool_results, list):
                    tool_results_list = tool_results
                else:
                    tool_results_list = [tool_results]

                # 收集工具调用信息，用于生成用户提示
                tool_names = []

                # 存储每轮工具调用
                for tool_result in tool_results_list:
                    # 1. 存储助手的工具调用消息（保持完整的toolcall结构）
                    tool_calls_info = tool_result.tool_calls_info

                    # 提取工具名称
                    if tool_calls_info.tool_calls:
                        for tool_call in tool_calls_info.tool_calls:
                            # tool_call 是对象，不是字典，直接访问属性
                            if hasattr(tool_call, 'function') and tool_call.function:
                                tool_name = tool_call.function.name if hasattr(tool_call.function, 'name') else '未知工具'
                                tool_names.append(tool_name)

                    # --- 新增：拟人化反馈逻辑 ---
                    assistant_tool_msg = {
                        "role": tool_calls_info.role,  # "assistant"
                        "content": tool_calls_info.content,  # 可能为None
                        "tool_calls": tool_calls_info.tool_calls,  # 保持原始tool_calls结构
                        "timestamp": time.time(),
                        "sender_id": "assistant",
                        "sender_name": "assistant",
                        "is_processed": True,  # 工具调用消息应标记为已处理
                        # 新增：标记这是结构化的toolcall记录，便于后续处理
                        "is_structured_toolcall": True,
                    }
                    self.angel_context.conversation_ledger.add_message(
                        chat_id, assistant_tool_msg
                    )

                    # 2. 存储工具执行结果（使用标准的tool角色格式）
                    for tool_result_msg in tool_result.tool_calls_result:
                        tool_msg = {
                            "role": tool_result_msg.role,  # "tool"
                            "tool_call_id": tool_result_msg.tool_call_id,  # 关键：保持ID关联
                            "content": tool_result_msg.content,  # 工具执行的实际结果
                            "timestamp": time.time(),
                            "sender_id": "tool",
                            "sender_name": "tool_result",
                            "is_processed": True,  # 工具结果消息应标记为已处理
                            # 新增：标记这是结构化的toolcall记录
                            "is_structured_toolcall": True,
                        }
                        self.angel_context.conversation_ledger.add_message(
                            chat_id, tool_msg
                        )

                logger.info(f"AngelHeart[{chat_id}]: 已记录结构化工具调用和结果")

                # 工具修饰消息发送（带冷却机制）
                if self.config_manager.tool_decoration_enabled and tool_names:
                    # 检查冷却时间
                    current_time = time.time()
                    last_sent_time = self._tool_decoration_last_sent.get(chat_id, 0)
                    cooldown = self.config_manager.tool_decoration_cooldown
                    time_since_last_sent = current_time - last_sent_time

                    if time_since_last_sent < cooldown:
                        # 还在冷却期，跳过发送
                        logger.debug(f"AngelHeart[{chat_id}]: 工具修饰消息在冷却中（距上次 {time_since_last_sent:.1f}s < {cooldown}s），跳过")
                    else:
                        # 可以发送，为每个工具查找修饰语
                        decorations = []
                        for tool_name in tool_names:
                            decoration = self._get_tool_decoration(tool_name)
                            if decoration:  # 只添加非空的修饰语
                                decorations.append(decoration)

                        # 只有当有修饰语时才发送消息
                        if decorations:
                            import random
                            # 多个工具时，随机选择一个修饰语
                            selected_decoration = random.choice(decorations)

                            try:
                                from astrbot.api.event import MessageChain
                                message_chain = MessageChain().message(selected_decoration)
                                await self.context.send_message(event.unified_msg_origin, message_chain)
                                # 更新最后发送时间
                                self._tool_decoration_last_sent[chat_id] = current_time
                                logger.info(f"AngelHeart[{chat_id}]: 已发送工具修饰消息: {selected_decoration}")
                            except Exception as e:
                                logger.error(f"AngelHeart[{chat_id}]: 发送工具修饰消息失败: {e}")

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
        logger.info(
            f"AngelHeart: 配置已更新。等待时间: {self.config_manager.waiting_time}秒, 缓存过期时间: {self.config_manager.cache_expiry}秒"
        )

    def _get_tool_decoration(self, tool_name: str) -> str:
        """
        根据工具名获取修饰语（支持模糊匹配）

        Args:
            tool_name: 工具名称，如 "web_search", "get_news" 等

        Returns:
            str: 随机选择的修饰语，如果未匹配到则返回空字符串

        匹配规则：
            - 从配置字典中从上往下遍历
            - 只要工具名包含配置的关键词，就匹配成功
            - 返回第一个匹配项的随机修饰语

        示例：
            配置: {"search": "我搜索一下|我搜一下"}
            工具名: "web_search" -> 匹配成功，返回 "我搜索一下" 或 "我搜一下"
            工具名: "get_news" -> 不匹配，返回 ""
        """
        import random

        decorations_config = self.config_manager.tool_decorations

        # 从上往下遍历配置，第一个匹配的就返回
        for keyword, decoration_str in decorations_config.items():
            # 检查工具名是否包含关键词（不区分大小写）
            if keyword.lower() in tool_name.lower():
                # 分割修饰语并随机选择一个
                options = [opt.strip() for opt in decoration_str.split('|') if opt.strip()]
                if options:
                    return random.choice(options)

        # 未匹配到任何配置
        return ""

    def _get_plain_chat_id(self, unified_id: str) -> str:
        """从 unified_msg_origin 中提取纯净的聊天ID (QQ号)"""
        parts = unified_id.split(":")
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
                        elif (
                            isinstance(message, Reply)
                            and str(message.sender_id) == self_id
                        ):
                            is_at_self = True
                except (AttributeError, ValueError, KeyError) as e:
                    logger.warning(f"AngelHeart[{chat_id}]: 解析消息链异常: {e}")
                    # 异常时保守处理，视为非@自己消息
                    return False

                # 如果是@自己或引用自己，应该处理（返回True）
                if is_at_self:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 检测到@自己的消息，准备处理..."
                    )
                    return True
                # 如果是@全体成员，不应该处理（返回False）
                elif has_at_all:
                    logger.debug(f"AngelHeart[{chat_id}]: 检测到@全体成员消息，已忽略")
                    return False
                # 如果是指令（非@），不应该处理（返回False）
                else:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 检测到指令或@他人消息，已忽略"
                    )
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

        except (AttributeError, ValueError, KeyError, IndexError) as e:
            logger.error(
                f"AngelHeart[{chat_id}]: _should_process方法执行异常: {e}",
                exc_info=True,
            )
            return False  # 异常时保守处理，不处理消息

    @filter.on_decorating_result(priority=200)
    async def strip_markdown_on_decorating_result(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
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
                elif hasattr(component, "data") and isinstance(component.data, dict):
                    text_content = component.data.get("text", "")
                    if text_content:
                        full_text_content += text_content

            if self._is_astrbot_error_message(full_text_content):
                logger.info(
                    f"AngelHeart[{chat_id}]: 检测到 AstrBot 错误信息，清空消息链。"
                )
                # 清空消息链，这样 RespondStage 就会跳过发送
                result = event.get_result()
                if result:
                    result.chain = []  # 清空消息链
                return

            # 2. 遍历消息链中的每个元素，进行 Markdown 清洗
            # 只处理 Plain 文本组件，保持其他组件不变
            if self.config_manager.strip_markdown_enabled:
                for i, component in enumerate(message_chain):
                    if isinstance(component, Plain):
                        original_text = component.text
                        if original_text:
                            try:
                                cleaned_text = strip_markdown(original_text)

                                # 只有在清洗结果有效且真正改变了内容时才替换
                                if (
                                    cleaned_text
                                    and cleaned_text.strip()
                                    and cleaned_text != original_text
                                ):
                                    # 替换整个 Plain 组件对象，但保持其他组件不变
                                    message_chain[i] = Plain(text=cleaned_text)
                                    logger.debug(
                                        f"AngelHeart[{chat_id}]: 已清洗文本组件: '{original_text[:50]}...' -> '{cleaned_text[:50]}...'"
                                    )
                                # 如果清洗结果相同或为空，保持原组件不变
                            except (AttributeError, ValueError) as e:
                                logger.warning(
                                    f"AngelHeart[{chat_id}]: 文本清洗失败: {e}，保持原文本"
                                )
            else:
                logger.debug(f"AngelHeart[{chat_id}]: Markdown清洗已禁用，跳过清洗步骤。")

            # 3. 将完整的消息链（包含文本和图片）序列化并缓存
            if message_chain:
                try:
                    serialized_content = serialize_message_chain(message_chain)
                    ai_message = {
                        "role": "assistant",
                        "content": serialized_content,
                        "sender_id": str(event.get_self_id()),
                        "sender_name": "assistant",
                        "timestamp": time.time(),
                        "is_processed": True,  # 助理回复应标记为已处理
                    }
                    self.angel_context.conversation_ledger.add_message(chat_id, ai_message)
                    logger.debug(f"AngelHeart[{chat_id}]: AI多模态回复已加入对话总账")
                except Exception as e:
                    # 序列化失败时的降级处理：至少缓存文本内容
                    logger.error(f"AngelHeart[{chat_id}]: 消息链序列化失败，回退到文本缓存。错误: {e}", exc_info=True)
                    logger.debug(f"AngelHeart[{chat_id}]: 失败的消息链: {repr(message_chain)}")

                    # 提取纯文本内容作为降级方案
                    fallback_text = ""
                    for component in message_chain:
                        if isinstance(component, Plain):
                            if component.text:
                                fallback_text += component.text

                    if fallback_text:
                        ai_message = {
                            "role": "assistant",
                            "content": fallback_text,
                            "sender_id": str(event.get_self_id()),
                            "sender_name": "assistant",
                            "timestamp": time.time(),
                            "is_processed": True,  # 助理回复应标记为已处理
                        }
                        self.angel_context.conversation_ledger.add_message(chat_id, ai_message)
                        logger.info(f"AngelHeart[{chat_id}]: AI回复（仅文本）已在降级处理后加入对话总账")
                    else:
                        logger.warning(f"AngelHeart[{chat_id}]: 无法提取任何文本内容，AI回复未被缓存")

            logger.debug(f"AngelHeart[{chat_id}]: 消息链中的Markdown格式清洗完成。")
        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: strip_markdown_on_decorating_result 处理异常: {e}", exc_info=True)
            # 不重新抛出异常，避免影响消息发送流程

    @filter.after_message_sent(priority=100)
    async def handle_message_sent(self, event: AstrMessageEvent):
        """
        消息发送后处理：取消耐心计时器、状态转换、释放处理锁

        比 on_decorating_result 更可靠，因为即使消息链为空也会触发
        """
        chat_id = event.unified_msg_origin
        try:
            logger.debug(f"AngelHeart[{chat_id}]: 消息发送完成，开始后处理...")

            # 1. 取消耐心计时器
            await self.angel_context.cancel_patience_timer(chat_id)

            # 2. 状态转换：AI发送消息后转换到观测期
            # 仅在消息链非空时才执行状态转换
            result = event.get_result()
            if result and result.chain:
                try:
                    await self.angel_context.handle_message_sent(chat_id)
                except (AttributeError, RuntimeError) as e:
                    logger.warning(f"AngelHeart[{chat_id}]: 状态转换处理异常: {e}")
            else:
                logger.debug(f"AngelHeart[{chat_id}]: 消息链为空，跳过状态转换")
        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: after_message_sent处理异常: {e}", exc_info=True)
        finally:
            try:
                # 3. 释放处理锁（设置冷却期）
                await self.angel_context.release_chat_processing(chat_id, set_cooldown=True)
                logger.info(f"AngelHeart[{chat_id}]: 任务处理完成，已在消息发送后释放处理锁。")
            except Exception as release_error:
                logger.error(
                    f"AngelHeart[{chat_id}]: after_message_sent 释放处理锁异常: {release_error}",
                    exc_info=True,
                )

    def _prepare_whitelist(self) -> set:
        """预处理白名单，将其转换为 set 以获得 O(1) 的查找性能。"""
        return {str(cid) for cid in self.config_manager.chat_ids}

    def _extract_sent_message_content(self, event: AstrMessageEvent) -> str:
        """从事件中提取发送的消息内容"""
        try:
            # 从event的result中获取发送的消息内容
            if hasattr(event, "get_result") and event.get_result():
                result = event.get_result()
                if hasattr(result, "chain") and result.chain:
                    # 提取chain中的文本内容
                    text_parts = []
                    for component in result.chain:
                        if hasattr(component, "text"):
                            text_parts.append(component.text)
                        elif hasattr(component, "data") and isinstance(
                            component.data, dict
                        ):
                            # 处理其他类型的组件
                            text_parts.append(str(component.data.get("text", "")))
                    return "".join(text_parts).strip()

            # 如果上面的方法失败，尝试从event的message中获取
            if hasattr(event, "get_message_outline"):
                return event.get_message_outline()

        except (AttributeError, KeyError) as e:
            logger.warning(
                f"AngelHeart[{event.unified_msg_origin}]: 提取发送消息内容时出错: {e}"
            )

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
            "astrbot 请求失败" in text_lower
            and "错误类型:" in text_lower
            and "错误信息:" in text_lower
        )

    async def _cleanup_all_waiting_resources(self):
        """清理所有等待中的资源和任务"""
        try:
            # 清理所有 pending_futures
            for chat_id, future in self.angel_context.pending_futures.items():
                if not future.done():
                    try:
                        future.set_result("KILL")  # 设置结果以释放等待
                        logger.debug(f"AngelHeart[{chat_id}]: 已在terminate时清理Future")
                    except (InvalidStateError, asyncio.InvalidStateError) as e:
                        # Future 状态可能在检查 done() 后立即改变（竞态条件）
                        # 尝试取消 Future 作为备选方案
                        logger.debug(f"AngelHeart[{chat_id}]: Future状态异常 ({type(e).__name__})，尝试取消")
                        try:
                            future.cancel()
                        except Exception as cancel_err:
                            logger.debug(f"AngelHeart[{chat_id}]: 取消Future失败: {type(cancel_err).__name__}: {cancel_err}")
                    except Exception as e:
                        # 捕获任何其他异常，防止停止清理流程
                        logger.debug(f"AngelHeart[{chat_id}]: 清理Future时发生异常: {type(e).__name__}: {e}")
            self.angel_context.pending_futures.clear()

            # 清理所有 pending_events
            self.angel_context.pending_events.clear()
            logger.debug("AngelHeart: 已在terminate时清理所有pending_events")

            # 取消所有扣押超时计时器
            for chat_id, timer in self.angel_context.detention_timeout_timers.items():
                if not timer.done():
                    timer.cancel()
                    logger.debug(f"AngelHeart[{chat_id}]: 已在terminate时取消扣押超时计时器")
            self.angel_context.detention_timeout_timers.clear()

            # 取消所有耐心计时器
            for chat_id, timer in self.angel_context.patience_timers.items():
                if not timer.done():
                    timer.cancel()
                    logger.debug(f"AngelHeart[{chat_id}]: 已在terminate时取消耐心计时器")
            self.angel_context.patience_timers.clear()

            # 清理门牌占用记录
            self.angel_context.processing_chats.clear()
            logger.debug("AngelHeart: 已在terminate时清理所有门牌占用记录")

            # 清理冷却期记录
            self.angel_context.lock_cooldown_until.clear()
            logger.debug("AngelHeart: 已在terminate时清理所有冷却期记录")

            logger.info("AngelHeart: 所有等待资源已清理完成")

        except Exception as e:
            logger.error(f"AngelHeart: terminate时清理资源异常: {e}", exc_info=True)

    async def terminate(self):
        """插件被卸载/停用时调用"""
        # 清理主动应答任务
        await self.angel_context.proactive_manager.cleanup()

        # 清理所有等待中的事件和任务
        await self._cleanup_all_waiting_resources()

        logger.info("💖 AngelHeart 插件已终止")
