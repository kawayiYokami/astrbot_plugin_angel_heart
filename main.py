"""
AngelHeart插件 - 天使心智能群聊/私聊交互插件

基于轻量级两级协作：
- 前台：接收并缓存消息
- 群聊双防抖：助理/秘书防抖（扣押实现）后激活最后边界事件
- 秘书：对激活事件重建上下文并决策是否回复
- 私聊：只缓存，主框架队列（无法向运行中子代理注入消息）
"""

import asyncio
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

from astrbot.api import logger
from astrbot.core.message.components import Plain, At, AtAll, Reply, Poke, Image

from .core.config_manager import ConfigManager
from .core.config_migration import run_migration
from .core.quote_target import parse_prefix_marker, strip_prefix_marker
from .roles.front_desk import FrontDesk
from .roles.secretary import Secretary
from .core.utils import clean_text_for_send
from .core.utils.message_utils import (
    extract_completed_agent_messages,
    serialize_agent_run_message,
)
from .core.angel_heart_context import AngelHeartContext
from .core.chat_profile import ChatProfileStore
from .core.runtime_task_tracker import RuntimeTaskTracker, track_runtime_handler
from .tools.image_understanding import AngelDescribeImageTool
from .core.long_image_render import render_long_text_image
from .core.font_manager import ensure_fonts, shutdown_fonts

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
        # 白名单派生集合：按配置版本懒重建，热保存后下一轮自动跟上
        self._whitelist_cache: set = set()
        self._whitelist_token = None
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

        # -- 系统提示词设置（插件自有 JSON，不进 AstrBot 自带设置页，只由本插件 WebUI 读写）--
        from .core.prompt_settings import PromptSettingsStore
        self.prompt_settings = PromptSettingsStore(plugin_data_dir)
        self.config_manager.attach_prompt_settings(self.prompt_settings)

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
                prompt_settings=self.prompt_settings,
                request_ring=self.angel_context.request_ring,
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
    @track_runtime_handler(owns_pipeline=True)
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
            if plain_chat_id not in self._ensure_whitelist():
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

        await self.front_desk.rewrite_prompt_for_llm(
            chat_id,
            event,
            req,
            allow_quote_target=not self._is_upstream_command_event(event),
        )

    @register_on_agent_done()
    @track_runtime_handler
    async def capture_completed_agent_messages(
        self, event: AstrMessageEvent, run_context: Any, response: LLMResponse
    ):
        """只在 Agent 完成后一次性记录本事件新增的完整 assistant/tool 链。"""
        chat_id = event.unified_msg_origin

        # 本轮没有可发送内容时立刻收口：空内容会被框架跳过发送，
        # after_message_sent 不再触发，账本会一直挂在 running。
        try:
            response_text = str(
                getattr(response, "completion_text", "") or ""
            ).strip()
            if not response_text and not self._has_sendable_content(
                getattr(response, "result_chain", None)
            ):
                self._close_work_on_empty_result(
                    event, chat_id, "Agent 结束但无可发送内容"
                )
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 完成点空内容判定失败: {e}")

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

        配置值一律动态读取，热保存即生效；这里只递增配置版本，让按版本懒
        重建的派生状态（分析器输出契约、白名单集合）在下一轮自动刷新。
        """
        self.config_manager.mark_changed()
        self.secretary.llm_analyzer.reload_config(self.config_manager)
        self._whitelist_cache = self._prepare_whitelist()

        logger.info(
            f"AngelHeart: 配置已保存并即时生效。助理休息: {self.config_manager.waiting_time}秒"
        )

    def reload_config(self, new_config: dict):
        """重新加载配置：原地更新共享配置对象，不新造配置管理器。

        各组件（媒体缓存、钓鱼回复、工作账本等）持有的都是同一个
        ConfigManager 引用，换实例会让没逐个改引用的组件继续读旧配置。
        原地更新键值后，所有读取点下一轮就吃到新值。
        """
        data = new_config if isinstance(new_config, dict) else {}
        if data is not self.config:
            self.config.clear()
            self.config.update(data)
        self.on_config_saved()

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

    def _is_poke_event(self, event: AstrMessageEvent) -> bool:
        """检查消息链是否含戳一戳组件（无文字互动通知）。"""
        return any(isinstance(comp, Poke) for comp in event.get_messages())

    def _should_process(self, event: AstrMessageEvent) -> bool:
        """检查是否需要处理此消息"""
        chat_id = event.unified_msg_origin

        try:
            # 戳一戳为无文字互动通知，不属于对话内容：入口直接无视。
            # 不缓存、不分析、不登记工作；不停事件，专门的戳一戳回应插件仍可处理。
            if self._is_poke_event(event):
                logger.debug(f"AngelHeart[{chat_id}]: 戳一戳事件已无视。")
                return False

            if self._is_upstream_command_event(event):
                logger.debug(
                    f"AngelHeart[{chat_id}]: 检测到上游 command/skill 事件，已跳过。"
                )
                return False

            if (
                not self._is_private_chat(chat_id)
                and str(event.get_sender_id()).strip()
                in self.config_manager.for_chat(chat_id).ignored_sender_ids
            ):
                logger.debug(f"AngelHeart[{chat_id}]: 发送者命中忽略名单，停止本次群聊事件。")
                event.stop_event()
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
                if plain_chat_id not in self._ensure_whitelist():
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

    def _clean_outgoing_text(self, text: str) -> str:
        """按当前开关清洗一条外发纯文本。

        常规回复与工具直发共用这一份口径，两者的开关也必须是同一组。
        """
        return clean_text_for_send(
            text,
            markdown=self.config_manager.strip_markdown_enabled,
            period=self.config_manager.strip_period_before_newline,
        )

    @filter.on_using_llm_tool()
    async def clean_tool_sent_text(
        self, event: AstrMessageEvent, tool, tool_args, *args, **kwargs
    ):
        """工具直发消息不经过发送前管线，这里补上同一套文本清理。

        `send_message_to_user` 由 ToolExecutor 直接发出去，`on_decorating_result`
        那条路一概不走：`[#n]` 标记会原样出现在群里，Markdown 也不清洗。钩子拿到的
        tool_args 就是随后展开执行的同一个 dict，就地改写即可生效。
        闭嘴期间同样在这里拦下，工具不该成为闭嘴的后门。
        """
        try:
            if getattr(tool, "name", "") != "send_message_to_user":
                return

            messages = (tool_args or {}).get("messages")
            if not isinstance(messages, list):
                return

            chat_id = event.unified_msg_origin
            if self.angel_context.silenced_until.get(chat_id, 0) > time.time():
                tool_args["messages"] = []
                logger.info(
                    f"AngelHeart[{chat_id}]: 处于闭嘴状态，拦下工具直发的消息。"
                )
                return

            for item in messages:
                if not isinstance(item, dict) or item.get("type") != "plain":
                    continue
                text = item.get("text")
                if not isinstance(text, str) or not text:
                    continue

                stripped = strip_prefix_marker(text)
                if stripped != text:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 工具发送剥离引用标记: '{text[:40]}...'"
                    )
                item["text"] = self._clean_outgoing_text(stripped)
        except Exception as e:
            logger.warning(f"清洗工具发送参数失败，保持原样: {e}")

    @filter.on_decorating_result(priority=200)
    @track_runtime_handler
    async def strip_markdown_on_decorating_result(
        self, event: AstrMessageEvent, *args, **kwargs
    ):
        """
        发送前的统一收口：错误信息拦截、闭嘴拦截、引用目标、文本清洗、
        能量结算与长回复转图。
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
                # 链清空后发送环节被跳过，发送后钩子不再触发，这里先收口
                self._close_work_on_empty_result(
                    event, chat_id, "检测到错误信息，已清空消息链"
                )
                return

            # 2. 闭嘴状态：回复生成期间被掌嘴的，这一条不外发
            if self.angel_context.silenced_until.get(chat_id, 0) > time.time():
                logger.info(f"AngelHeart[{chat_id}]: 处于闭嘴状态，本条回复不外发。")
                result = event.get_result()
                if result:
                    result.chain = []
                self._close_work_on_empty_result(
                    event, chat_id, "闭嘴状态，已拦截发送"
                )
                return

            # 3. 引用目标：解析标记、按间隔判断是否插入 Reply
            # 顺序固定在 Markdown 清洗与字数扣能之前，标记不会计入回复字数。
            try:
                self._apply_quote_target(event, chat_id, message_chain)
            except Exception as e:
                logger.warning(
                    f"AngelHeart[{chat_id}]: 引用目标处理失败，继续原有输出清理: {e}"
                )

            # 4. 文本清洗：Markdown 与句末句号，各按开关，只动 Plain 组件
            for i, component in enumerate(message_chain):
                if not isinstance(component, Plain) or not component.text:
                    continue
                original_text = component.text
                try:
                    cleaned_text = self._clean_outgoing_text(original_text)
                except (AttributeError, ValueError) as e:
                    logger.warning(
                        f"AngelHeart[{chat_id}]: 文本清洗失败: {e}，保持原文本"
                    )
                    continue
                if cleaned_text != original_text:
                    message_chain[i] = Plain(text=cleaned_text)
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 已清洗文本组件: '{original_text[:50]}...' -> '{cleaned_text[:50]}...'"
                    )

            # 5. 按原文结算能量：转图后消息链只剩图片，字符数会归零
            await self.angel_context.debounce_manager.charge_reply_energy(
                event, message_chain
            )

            # 6. 长回复整条转长图：文字换成图片，避免群里刷屏。
            #    账本记录的是 run_context.messages 里的原文，这里只替换发送用的消息链。
            await self._try_convert_long_reply(event, message_chain)
            # 清洗、扣能、转图之后若已无可发内容，发送环节会被跳过，这里先收口
            if not self._has_sendable_content(message_chain):
                self._close_work_on_empty_result(
                    event, chat_id, "发送前消息链无可发内容"
                )
            logger.debug(f"AngelHeart[{chat_id}]: 消息链中的Markdown格式清洗完成。")
        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: strip_markdown_on_decorating_result 处理异常: {e}", exc_info=True)
            # 不重新抛出异常，避免影响消息发送流程

    # --- 引用目标 ---

    def _apply_quote_target(
        self, event: AstrMessageEvent, chat_id: str, message_chain
    ) -> None:
        """解析正文开头的 [#n]，按编号插入 Reply。

        只认正文最前面那一个标记；正文中段出现的 [#n] 一律当普通文字。
        标记无论能否查到目标都会剥掉——它不该出现在群里。
        """
        # 幂等以「单个结果」为范围：工具链会为同一事件产生多条结果，
        # 前一段（工具说明）不能把后一段（真正的回答）挡掉。
        try:
            result = event.get_result()
        except Exception:
            result = None
        if result is not None and getattr(result, "_angelheart_quote_done", False):
            return

        plain = next(
            (component for component in message_chain if isinstance(component, Plain)),
            None,
        )
        if plain is not None:
            number, rest = parse_prefix_marker(plain.text or "")
            if number is not None:
                plain.text = rest
                target = ""
                try:
                    target = (
                        self.angel_context.conversation_ledger.get_message_id_by_quote_no(
                            chat_id, number
                        )
                    )
                except Exception as e:
                    logger.warning(f"AngelHeart[{chat_id}]: 按编号查引用目标失败: {e}")
                if target and not any(
                    isinstance(component, Reply) for component in message_chain
                ):
                    try:
                        message_chain.insert(0, Reply(id=target))
                    except Exception as e:
                        logger.warning(f"AngelHeart[{chat_id}]: 插入引用失败: {e}")
                logger.debug(
                    f"AngelHeart[{chat_id}]: 引用目标 #{number} -> {target or '未命中'}"
                )

        if result is not None:
            try:
                result._angelheart_quote_done = True
            except Exception:
                pass

    def _long_image_brand(self) -> str:
        """页眉左侧显示唤醒名，取 alias 里最长的那一个。"""
        alias = (self.config_manager.alias or "").strip()
        candidates = [part.strip() for part in alias.split("|") if part.strip()]
        return max(candidates, key=len) if candidates else ""

    async def _try_convert_long_reply(self, event: AstrMessageEvent, message_chain) -> bool:
        """回复文本够长时整条排成长图，超过单页高度就分成多张。

        判定放在 Markdown 清洗之前：清洗会把标题、列表压成纯文本，长图要的
        正是这些结构。转换失败一律返回 False，让原文本照常发出。

        只替换发送用的消息链；会话账本记的是 run_context.messages 里的原文，
        图片不会流进历史上下文。
        """
        if not self.config_manager.long_text_image_enabled:
            return False

        # 只处理纯文本消息链：带 @、回复引用、已有图片的回复保持原样
        if not message_chain or any(
            not isinstance(component, Plain) for component in message_chain
        ):
            return False

        text = "".join(
            component.text or "" for component in message_chain if isinstance(component, Plain)
        ).strip()
        if len(text) < self.config_manager.long_text_image_threshold:
            return False

        # 字体是转图的前提：没就绪就不转，宁可发原文，也不要让容器里的系统字体
        # 顶上把字形弄走样。首次未就绪会顺手触发后台下载，这一次不等它。
        if not await ensure_fonts():
            logger.debug(
                f"AngelHeart[{event.unified_msg_origin}]: 字体未就绪，跳过转图"
            )
            return False

        # 排版是重 CPU 活，同一时刻只跑一份，避免并发长回复把内存叠起来
        lock = getattr(self, "_long_image_lock", None)
        if lock is None:
            lock = self._long_image_lock = asyncio.Lock()

        try:
            async with lock:
                image_paths = await asyncio.to_thread(
                    render_long_text_image,
                    text,
                    brand=self._long_image_brand(),
                    footer_left=f"共 {len(text)} 字",
                    footer_right=time.strftime("%Y-%m-%d"),
                )
        except Exception as e:
            logger.error(
                f"AngelHeart[{event.unified_msg_origin}]: 长回复转图失败，按原文本发送: {e}",
                exc_info=True,
            )
            return False

        if not image_paths:
            logger.warning(
                f"AngelHeart[{event.unified_msg_origin}]: 长回复转图没有产出图片，按原文本发送"
            )
            return False

        message_chain.clear()
        for path in image_paths:
            message_chain.append(Image.fromFileSystem(path))
        logger.info(
            f"AngelHeart[{event.unified_msg_origin}]: 长回复已转长图"
            f"（{len(text)} 字，{len(image_paths)} 张）"
        )
        return True

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
                try:
                    self.angel_context.conversation_ledger.flush_pending_cleanup(chat_id)
                except Exception as e:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 补做媒体缓存清理失败: {e}"
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
                    self.angel_context.conversation_ledger.flush_pending_cleanup(chat_id)
                except Exception as e:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 补做媒体缓存清理失败: {e}"
                    )
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

    def _ensure_whitelist(self) -> set:
        """按配置版本懒重建白名单集合，配置热保存后自动跟上。"""
        token = (id(self.config_manager), getattr(self.config_manager, "revision", 0))
        if token != self._whitelist_token:
            self._whitelist_cache = self._prepare_whitelist()
            self._whitelist_token = token
        return self._whitelist_cache

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

    @staticmethod
    def _has_sendable_content(chain) -> bool:
        """消息链里有没有真正能发出去的内容。

        与框架 respond 阶段同口径：空白 Plain、只有 Reply/At 都不算内容。
        空内容时框架会跳过整个发送环节，after_message_sent 不再触发。
        """
        for component in chain or []:
            name = type(component).__name__
            if name == "Plain":
                if str(getattr(component, "text", "") or "").strip():
                    return True
                continue
            if name in {"Reply", "At"}:
                continue
            return True
        return False

    def _close_work_on_empty_result(
        self, event: AstrMessageEvent, chat_id: str, reason: str
    ) -> None:
        """本轮没有可发送内容时立即收口工作账本。

        空内容会被框架跳过发送，after_message_sent 不再触发，账本那条 running
        无人收尾，同会话后续消息会一直撞 assistant_busy 直到超时。这里主动收口。
        """
        try:
            work_id = ""
            if hasattr(event, "get_extra"):
                work_id = str(event.get_extra("angelheart_work_id", "") or "")
            if not work_id:
                work_id = self.front_desk._get_event_message_id(event)
            if not work_id:
                return
            self.angel_context.work_ledger.complete_work(
                chat_id,
                work_id,
                status="failed",
                result_summary=reason,
            )
            logger.debug(
                f"AngelHeart[{chat_id}]: {reason}，已收口工作账本 work_id={work_id}"
            )
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 空结果收口工作账本失败: {e}")

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
        # 字体后台下载不归 _runtime_tasks 收口（它不是 handler），单独取消，
        # 否则插件目录被删后它还在往里面写 .part。
        await shutdown_fonts()
        logger.info("💖 AngelHeart 插件已终止")
