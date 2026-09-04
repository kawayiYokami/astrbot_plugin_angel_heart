"""
AngelHeart插件 - 天使心智能群聊/私聊交互插件

基于轻量级两级协作：
- 前台：接收并缓存消息
- 群聊双防抖：助理/秘书防抖（扣押实现）后激活最后边界事件
- 秘书：对激活事件重建上下文并决策是否回复
- 私聊：只缓存，主框架队列（无法向运行中子代理注入消息）
"""

import time
import json
import os
from typing import Any

from astrbot.api.star import Star, Context, register
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest, LLMResponse
from astrbot.core.star.register import register_on_agent_done
from astrbot.core.star.star_tools import StarTools
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter

try:
    from astrbot.api import logger
except ImportError:
    import logging

    logger = logging.getLogger(__name__)
from astrbot.core.message.components import Plain, At, AtAll, Reply

from .core.config_manager import ConfigManager
from .core.config_migration import run_migration
from .roles.front_desk import FrontDesk
from .roles.secretary import Secretary
from .core.utils import strip_markdown, strip_period_before_newline
from .core.utils.message_utils import (
    extract_completed_agent_messages,
    serialize_agent_run_message,
)
from .core.angel_heart_context import AngelHeartContext
from .core.chat_profile import ChatProfileStore
from .core.runtime_task_tracker import RuntimeTaskTracker, track_runtime_handler
from .tools.image_understanding import AngelDescribeImageTool

# 在框架加载 schema 之前执行配置迁移
run_migration()


def _plugin_version() -> str:
    """从 metadata.yaml 读取版本号，避免与 @register 双处维护。"""
    try:
        meta_path = os.path.join(os.path.dirname(__file__), "metadata.yaml")
        with open(meta_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip()
    except (OSError, ValueError):
        pass
    return "0.0.0"


@register("astrbot_plugin_angel_heart", "kawayiYokami", "天使心秘书，让astrbot拥有极其聪明，有分寸的群聊介入，和极其完备的群聊上下文管理", _plugin_version(), "https://github.com/kawayiYokami/astrbot_plugin_angel_heart")
class AngelHeartPlugin(Star):
    """AngelHeart插件 - 专注的智能回复员"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        # 全局配置对象（AstrBotConfig，dict 子类）。ConfigManager 与 WebUI 配置
        # 端点共享同一引用：原地更新键值即全插件热生效。
        self.config = config if isinstance(config, dict) else {}
        self.config_manager = ConfigManager(self.config)
        self.context = context
        self._whitelist_cache = self._prepare_whitelist()
        self._runtime_tasks = RuntimeTaskTracker()

        # -- 获取插件数据目录 --
        plugin_data_dir = StarTools.get_data_dir("astrbot_plugin_angel_heart")

        # -- 群聊独立配置模板存储 --
        self.profile_store = ChatProfileStore(plugin_data_dir)
        self.config_manager.attach_profile_store(self.profile_store)

        # -- 来源登记（见过的人群/私聊，供 WebUI 认群）--
        from .core.chat_sources import ChatSourcesStore
        self.chat_sources = ChatSourcesStore(plugin_data_dir)

        # -- 每群最近一次秘书决策（供 WebUI 状态栏）--
        from .core.last_decisions import LastDecisionStore
        self.last_decisions = LastDecisionStore(plugin_data_dir)

        # -- 创建 AngelHeartContext 全局上下文（包含 ConversationLedger）--
        self.angel_context = AngelHeartContext(self.config_manager, self.context, plugin_data_dir)
        self.context.add_llm_tools(
            AngelDescribeImageTool(
                conversation_ledger=self.angel_context.conversation_ledger,
                config_manager=self.config_manager,
                astr_context=self.context,
            )
        )

        # -- 角色实例 --
        # 创建秘书和前台，通过全局上下文传递依赖
        self.secretary = Secretary(
            self.config_manager, self.context, self.angel_context
        )
        self.front_desk = FrontDesk(self.config_manager, self.angel_context)
        self.front_desk.chat_sources = self.chat_sources
        self.front_desk.last_decisions = self.last_decisions

        # 建立必要的相互引用
        self.front_desk.secretary = self.secretary

        # -- 注册 WebUI API 路由（群聊独立配置管理页）--
        try:
            from .web_api import register_all_routes
            register_all_routes(
                self.context,
                self.profile_store,
                self.config_manager,
                self.angel_context.conversation_ledger,
                self.chat_sources,
                self.angel_context.status_transition_manager,
                self.angel_context.debounce_manager,
                self.last_decisions,
                config=self.config,
                plugin=self,
            )
            logger.info("AngelHeart: 已注册群聊配置 WebUI API 路由")
        except Exception as e:  # pragma: no cover - 兼容旧版 AstrBot
            logger.warning(f"AngelHeart: WebUI API 路由注册失败（不影响核心功能）: {e}")

        logger.info("💖 AngelHeart智能回复员初始化完成 (事件扣押机制 V2 已启用)")

    # --- 核心事件处理 ---
    @filter.event_message_type(
        filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE,
        priority=-10,
    )
    @track_runtime_handler
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

    @filter.on_llm_request(priority=0)
    @track_runtime_handler
    async def inject_oneshot_decision_on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """读取本事件 angelheart_context，供日志与后续钩子使用（不写回 req）"""
        chat_id = event.unified_msg_origin

        if hasattr(event, "angelheart_context"):
            try:
                context = json.loads(event.angelheart_context)
                if context.get("error"):
                    logger.warning(
                        f"AngelHeart[{chat_id}]: 上下文包含错误: {context['error']}"
                    )

                chat_records = context.get("chat_records", [])
                secretary_decision = context.get("secretary_decision", {})

                logger.debug(
                    f"AngelHeart[{chat_id}]: 读取到上下文 - 记录数: {len(chat_records)}, "
                    f"决策: {secretary_decision.get('reply_strategy', '未知')}"
                )
            except json.JSONDecodeError as e:
                logger.warning(
                    f"AngelHeart[{chat_id}]: 解析 angelheart_context JSON 失败: {e}"
                )
            except (AttributeError, KeyError, TypeError) as e:
                logger.warning(
                    f"AngelHeart[{chat_id}]: 处理 angelheart_context 时发生意外错误: {e}"
                )

    @filter.on_llm_request(priority=50)
    @track_runtime_handler
    async def delegate_prompt_rewriting(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """将 Prompt 重写任务委托给 FrontDesk 处理"""
        chat_id = event.unified_msg_origin

        # 白名单检查：如果启用了白名单，非白名单会话不接管上下文
        if self.config_manager.whitelist_enabled:
            plain_chat_id = self._get_plain_chat_id(chat_id)
            if plain_chat_id not in self._whitelist_cache:
                return

        if self._is_private_chat(chat_id):
            if not self.config_manager.takeover_private_chat_context:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 私聊上下文接管未启用，跳过请求体重写。"
                )
                return
        else:
            if not self.config_manager.group_chat_enhancement:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 群聊上下文接管未启用，跳过请求体重写。"
                )
                return

        await self.front_desk.rewrite_prompt_for_llm(chat_id, event, req)

    @register_on_agent_done()
    @track_runtime_handler
    async def capture_completed_agent_messages(
        self, event: AstrMessageEvent, run_context: Any, response: LLMResponse
    ):
        """只在 Agent 完成后一次性记录本事件新增的完整 assistant/tool 链。"""
        chat_id = event.unified_msg_origin
        try:
            completed_messages = extract_completed_agent_messages(
                getattr(run_context, "messages", None),
                event.get_extra("provider_request") if hasattr(event, "get_extra") else None,
            )
            if not completed_messages:
                return

            # 时间口径（有意设计，不是遗漏）：
            # 1. 整条工具链以「事件完结瞬间」为基准时间，不回填中途真实发生时刻。
            # 2. 链内用 +0.001 只保相对顺序，不表示真实间隔。
            # 3. 请求体正确性不依赖这些时间；时间只服务 Ledger 排序与内部提示词展示。
            # 4. 若改成工具调用的真实时间，并发用户消息可能插进 assistant/tool 中间，
            #    把闭合链拆开。完结瞬间整块落账，就是为了保住闭合性。
            base_timestamp = time.time()
            assistant_sender_id = "assistant"
            try:
                assistant_sender_id = str(event.get_self_id())
            except Exception:
                pass

            ledger_messages = []
            for index, message in enumerate(completed_messages):
                ledger_message = serialize_agent_run_message(
                    message,
                    timestamp=base_timestamp + index * 0.001,
                    assistant_sender_id=assistant_sender_id,
                )
                if ledger_message is None:
                    continue
                ledger_messages.append(ledger_message)

            if not ledger_messages:
                return

            # 整条闭合链一次原子入账，避免并发请求读到半截工具链。
            self.angel_context.conversation_ledger.add_messages(
                chat_id, ledger_messages
            )

            logger.debug(
                f"AngelHeart[{chat_id}]: 已在完成点记录 {len(ledger_messages)} 条完整 assistant/tool 消息"
            )
        except Exception as e:
            logger.error(
                f"AngelHeart[{chat_id}]: 完成点记录 assistant/tool 链失败: {e}",
                exc_info=True,
            )

    # --- 内部方法 ---
    def on_config_saved(self):
        """WebUI 保存全局配置后的热生效钩子。

        ConfigManager 持有 config dict 引用，其余组件动态读取自动生效；
        此处只刷新显式缓存：prompt 模板（依赖 is_reasoning_model）与白名单。
        """
        self.secretary.llm_analyzer.reload_config(self.config_manager)
        self._whitelist_cache = self._prepare_whitelist()
        logger.info(
            f"AngelHeart: 配置已保存并即时生效。助理休息: {self.config_manager.waiting_time}秒"
        )

    def reload_config(self, new_config: dict):
        """重新加载配置"""
        self.config_manager = ConfigManager(new_config or {})
        # 群聊独立配置模板存储保持同一实例，重新挂载
        self.config_manager.attach_profile_store(self.profile_store)
        # 更新角色与调度器的配置管理器
        self.secretary.config_manager = self.config_manager
        self.front_desk.config_manager = self.config_manager
        self.front_desk.status_checker.config_manager = self.config_manager
        self.angel_context.config_manager = self.config_manager
        self.angel_context.debounce_manager.config_manager = self.config_manager
        # 重新加载LLM分析器的配置
        self.secretary.llm_analyzer.reload_config(self.config_manager)
        self._whitelist_cache = self._prepare_whitelist()

        logger.info(
            f"AngelHeart: 配置已更新。助理休息: {self.config_manager.waiting_time}秒，"
            f"前台巡检: {self.config_manager.secretary_debounce_time}秒"
        )

    def _get_plain_chat_id(self, unified_id: str) -> str:
        """从 unified_msg_origin 中提取纯净的聊天ID (QQ号)"""
        parts = unified_id.split(":")
        return parts[-1] if parts else ""

    def _is_private_chat(self, unified_id: str) -> bool:
        """根据 unified_msg_origin 判断是否为私聊。"""
        parts = unified_id.split(":")
        return len(parts) >= 3 and parts[1] == "FriendMessage"

    def _is_upstream_command_event(self, event: AstrMessageEvent) -> bool:
        """判断当前事件是否已命中上游 command/skill 处理器。"""
        try:
            activated_handlers = event.get_extra("activated_handlers", []) or []
            for handler in activated_handlers:
                for event_filter in getattr(handler, "event_filters", []) or []:
                    if isinstance(event_filter, (CommandFilter, CommandGroupFilter)):
                        return True
            return False
        except Exception as e:
            logger.warning(
                f"AngelHeart[{event.unified_msg_origin}]: 判断上游指令事件失败: {e}"
            )
            return False

    def _is_blocked_by_provider_wake_prefix(self, event: AstrMessageEvent) -> bool:
        """判断当前事件是否会被上游 LLM 额外聊天唤醒前缀拦截。"""
        try:
            if not event.is_at_or_wake_command:
                return False

            chat_id = event.unified_msg_origin
            astrbot_conf = self.context.get_config(chat_id)
            provider_settings = astrbot_conf.get("provider_settings", {}) if astrbot_conf else {}
            provider_wake_prefix = (provider_settings.get("wake_prefix", "") or "").strip()
            if not provider_wake_prefix:
                return False

            message_outline = ""
            try:
                message_outline = (event.get_message_outline() or "").strip()
            except Exception:
                message_outline = ""
            return not message_outline.startswith(provider_wake_prefix)
        except Exception as e:
            logger.warning(
                f"AngelHeart[{event.unified_msg_origin}]: 判断额外聊天唤醒前缀拦截失败: {e}"
            )
            return False

    def _should_process(self, event: AstrMessageEvent) -> bool:
        """检查是否需要处理此消息"""
        chat_id = event.unified_msg_origin

        try:
            if self._is_upstream_command_event(event):
                logger.debug(
                    f"AngelHeart[{chat_id}]: 检测到上游 command/skill 事件，已跳过。"
                )
                return False

            blocked_by_provider_wake_prefix = self._is_blocked_by_provider_wake_prefix(event)
            event.set_extra(
                "angelheart_blocked_by_provider_wake_prefix",
                blocked_by_provider_wake_prefix,
            )
            if blocked_by_provider_wake_prefix:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 未命中上游额外聊天唤醒前缀，保留聊天记录但跳过分析。"
                )

            # 1. 检查是否为@消息，区分@自己和@全体成员
            if event.is_at_or_wake_command:
                # 私聊天然是直接对话场景，不需要经过@自己的判定分支
                if self._is_private_chat(chat_id):
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 检测到私聊唤醒消息，允许进入缓存流程。"
                    )
                    return True

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

                # 如果是@全体成员，不应该处理（返回False）
                if has_at_all:
                    logger.debug(f"AngelHeart[{chat_id}]: 检测到@全体成员消息，已忽略")
                    return False

                # @自己 / 引用自己 / 普通唤醒非命令消息，统一放行给后续规则处理
                if is_at_self:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 检测到@自己的消息，准备处理..."
                    )
                else:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 检测到普通唤醒非命令消息，交给后续规则处理。"
                    )
                return True

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
    @track_runtime_handler
    async def strip_markdown_on_decorating_result(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """
        在消息发送前，对消息链中的文本内容进行Markdown清洗，并检测错误信息。
        """
        chat_id = event.unified_msg_origin
        try:
            if self._is_upstream_command_event(event):
                logger.debug(
                    f"AngelHeart[{chat_id}]: 检测到是上游指令事件，跳过 Markdown 清洗。"
                )
                return

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

            # 3. 句末句号清理：换行符之前的中文句号清理掉
            if self.config_manager.strip_period_before_newline:
                for i, component in enumerate(message_chain):
                    if isinstance(component, Plain):
                        original_text = component.text
                        if original_text:
                            cleaned_text = strip_period_before_newline(original_text)
                            if cleaned_text != original_text:
                                message_chain[i] = Plain(text=cleaned_text)
                                logger.debug(
                                    f"AngelHeart[{chat_id}]: 已清理句末句号: '{original_text[:50]}...' -> '{cleaned_text[:50]}...'"
                                )

            await self.angel_context.debounce_manager.charge_reply_energy(
                event, message_chain
            )
            logger.debug(f"AngelHeart[{chat_id}]: 消息链中的Markdown格式清洗完成。")
        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: strip_markdown_on_decorating_result 处理异常: {e}", exc_info=True)
            # 不重新抛出异常，避免影响消息发送流程

    @filter.after_message_sent(priority=100)
    @track_runtime_handler
    async def handle_message_sent(self, event: AstrMessageEvent):
        """
        消息发送后处理：状态转换、完成工作账本、兜底收口

        比 on_decorating_result 更可靠，因为即使消息链为空也会触发。
        秘书单飞已在放行助理时释放；这里不再用发送完成去占用/释放秘书判断锁。
        """
        chat_id = event.unified_msg_origin
        try:
            logger.debug(f"AngelHeart[{chat_id}]: 消息发送完成，开始后处理...")

            # 状态转换：AI发送消息后转换到观测期。流式适配器可能已经
            # 完成实际投递，但 result.chain 仍为空；此时使用 AstrBot 的
            # 统一发送事实标记，避免状态滞留并在回复后误发耐心消息。
            result = event.get_result()
            has_sent_message = bool(
                (result and result.chain)
                or getattr(event, "_has_send_oper", False)
            )
            if has_sent_message:
                leave_reply_trigger = self.angel_context.debounce_manager.get_leave_reply_trigger(event)
                try:
                    await self.angel_context.handle_message_sent(
                        chat_id, keep_not_present=bool(leave_reply_trigger)
                    )
                except (AttributeError, RuntimeError) as e:
                    logger.warning(f"AngelHeart[{chat_id}]: 状态转换处理异常: {e}")

                # 秘书单飞已在放行助理时释放；助理休息已在主脑调用点启动。

                # 兼容兜底：若放行时未释放单飞，发送后仍尝试收口，但不附带休息。
                try:
                    await self._finish_secretary_dispatch(
                        event,
                        chat_id,
                        cooldown_seconds=0.0,
                        reason=(
                            "leave_reply_sent"
                            if leave_reply_trigger
                            else "reply_sent"
                        ),
                    )
                except Exception as e:
                    logger.warning(
                        f"AngelHeart[{chat_id}]: 回复后兜底收口秘书单飞失败: {e}"
                    )

                # 工作账本：本轮完成
                try:
                    work_id = ""
                    if hasattr(event, "get_extra"):
                        work_id = str(event.get_extra("angelheart_work_id", "") or "")
                    if not work_id:
                        work_id = self.front_desk._get_event_message_id(event)
                    preview = self._extract_sent_message_content(event)
                    if len(preview) > 80:
                        preview = preview[:80] + "…"
                    self.angel_context.work_ledger.complete_work(
                        chat_id,
                        work_id,
                        status="done",
                        result_summary=preview or "已回复",
                    )
                except Exception as e:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 更新工作账本完成状态失败: {e}"
                    )
            else:
                logger.debug(f"AngelHeart[{chat_id}]: 未检测到实际消息发送，跳过状态转换")
                try:
                    work_id = ""
                    if hasattr(event, "get_extra"):
                        work_id = str(event.get_extra("angelheart_work_id", "") or "")
                    if not work_id:
                        work_id = self.front_desk._get_event_message_id(event)
                    if work_id:
                        self.angel_context.work_ledger.complete_work(
                            chat_id,
                            work_id,
                            status="failed",
                            result_summary="空回复/未发送",
                        )
                except Exception:
                    pass
                try:
                    await self._finish_secretary_dispatch(
                        event,
                        chat_id,
                        cooldown_seconds=0.0,
                        reason="empty_reply",
                    )
                except Exception as e:
                    logger.warning(
                        f"AngelHeart[{chat_id}]: 空回复收口秘书调度失败: {e}"
                    )
        except Exception as e:
            logger.error(
                f"AngelHeart[{chat_id}]: after_message_sent处理异常: {e}",
                exc_info=True,
            )
            try:
                await self._finish_secretary_dispatch(
                    event,
                    chat_id,
                    cooldown_seconds=0.0,
                    reason="send_handler_error",
                )
            except Exception:
                pass
        # 旧单槽门锁已退役；发送后只做状态/工作账本/兜底收口，调度只认双防抖

    async def _finish_secretary_dispatch(
        self,
        event: AstrMessageEvent,
        chat_id: str,
        *,
        cooldown_seconds: float,
        reason: str,
    ) -> bool:
        """按事件持有的调度归属收口同会话秘书单飞。

        正常回复路径应在秘书放行时已释放；此处多为兜底或不回复/异常收口。
        """
        dispatch_id = ""
        if hasattr(event, "get_extra"):
            dispatch_id = str(
                event.get_extra("angelheart_secretary_dispatch_id", "") or ""
            )
        if not dispatch_id:
            return False
        return await self.angel_context.debounce_manager.finish_secretary_dispatch(
            chat_id,
            dispatch_id,
            cooldown_seconds=cooldown_seconds,
            reason=reason,
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
        """清理插件创建的全部后台任务、运行态内存与持久连接。"""
        try:
            # 先取消私聊摘要，确保整理锁释放且不再访问即将关闭的 ledger。
            await self.front_desk.cleanup_background_tasks()
        except Exception as e:
            logger.error(f"AngelHeart: 清理前台后台任务失败: {e}", exc_info=True)
        try:
            await self.angel_context.cleanup()
        except Exception as e:
            logger.error(f"AngelHeart: 清理全局运行态失败: {e}", exc_info=True)
        logger.info("AngelHeart: 全部后台任务、运行态内存与持久连接已清理")

    async def terminate(self):
        """插件被卸载/停用时调用"""
        await self._runtime_tasks.stop()
        await self._cleanup_all_waiting_resources()
        logger.info("💖 AngelHeart 插件已终止")
