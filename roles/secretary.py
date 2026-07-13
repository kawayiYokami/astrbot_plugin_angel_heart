"""
AngelHeart 插件 - 秘书角色 (Secretary)
负责定时分析缓存内容，决定是否回复。
"""

import asyncio
import json
from typing import Dict, List
from enum import Enum

from ..core.utils import json_serialize_context
from ..core.llm_analyzer import LLMAnalyzer
from ..models.analysis_result import SecretaryDecision
from ..core.angel_heart_status import StatusChecker, AngelHeartStatus
from astrbot.api.event import AstrMessageEvent

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class AwakenReason(Enum):
    """秘书唤醒原因枚举"""
    OK = "正常"
    COOLING_DOWN = "冷却中"
    PROCESSING = "处理中"


class Secretary:
    """
    秘书角色 - 专注的分析与决策员
    """

    def __init__(self, config_manager, context, angel_context):
        """
        初始化秘书角色。

        Args:
            config_manager: 配置管理器实例。
            context: 插件上下文对象。
            angel_context: AngelHeart全局上下文实例。
        """
        self._config_manager = config_manager
        self.context = context
        self.angel_context = angel_context
        self.status_checker = StatusChecker(config_manager, angel_context)

        # -- 常量定义 --
        self.DB_HISTORY_MERGE_LIMIT = 5  # 数据库历史记录合并限制

        # -- 核心组件 --
        # 初始化 LLMAnalyzer
        analyzer_model_name = self.config_manager.analyzer_model
        reply_strategy_guide = self.config_manager.reply_strategy_guide
        # 传递 context 对象，让 LLMAnalyzer 在需要时动态获取 provider
        self.llm_analyzer = LLMAnalyzer(
            analyzer_model_name, context, reply_strategy_guide, self.config_manager
        )

    async def handle_message_by_state(self, event: AstrMessageEvent) -> SecretaryDecision:
        """
        秘书职责：防抖激活后，重建上下文并决策。

        群聊现行模型：
        - 离场 / 在场
        - 助理防抖 / 秘书防抖（扣押实现）
        - must_reply 由防抖账本在放行时挂到事件上
        """
        chat_id = event.unified_msg_origin
        current_status = self.angel_context.get_chat_status(chat_id)
        must_reply = self.angel_context.debounce_manager.get_must_reply(event)
        debounce_kind = self.angel_context.debounce_manager.get_debounce_kind(event)
        logger.info(
            f"AngelHeart[{chat_id}]: 秘书处理激活事件 "
            f"(状态: {current_status.value}, kind={debounce_kind or 'unknown'}, must_reply={must_reply})"
        )

        # 激活后确保在场
        if not self.angel_context.is_present(chat_id):
            await self.angel_context.status_transition_manager.transition_to_status(
                chat_id, AngelHeartStatus.OBSERVATION, "防抖激活，确保在场"
            )

        # 激活时重建上下文后再分析
        historical_context, recent_dialogue, boundary_ts = (
            self.angel_context.conversation_ledger.get_context_snapshot(chat_id)
        )
        if not recent_dialogue:
            logger.info(f"AngelHeart[{chat_id}]: 无新消息需要分析。")
            return SecretaryDecision(
                should_reply=False, reply_strategy="无新消息", topic="未知",
                entities=[], facts=[], keywords=[]
            )

        # 钉死秘书判断点：主脑 rewrite 必须用同一份切片，禁止组请求时再全量扩窗
        try:
            if hasattr(event, "set_extra"):
                event.set_extra(
                    "angelheart_decision_context",
                    {
                        "historical_context": historical_context,
                        "recent_dialogue": recent_dialogue,
                        "boundary_ts": boundary_ts,
                    },
                )
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 固化决策上下文失败: {e}")

        decision = await self.perform_analysis(
            recent_dialogue, historical_context, chat_id, event=event
        )

        # 必须回应：助理防抖 / 加速秘书防抖
        if must_reply:
            has_reason = (
                decision.is_questioned
                or decision.is_interesting
                or self.config_manager.reply_even_not_questioned
                or self.config_manager.force_reply_when_summoned
            )
            if has_reason:
                decision.should_reply = True
                if not decision.reply_strategy or decision.reply_strategy == "继续观察":
                    decision.reply_strategy = "必须回应"
            else:
                # 配置要求强制回复时，即使理由弱也回
                if self.config_manager.force_reply_when_summoned:
                    decision.should_reply = True
                    decision.reply_strategy = "必须回应"

        return decision

    async def _handle_familiarity_reply(self, event: AstrMessageEvent, chat_id: str) -> SecretaryDecision:
        """兼容旧路径：混脸熟不再作为进场条件。"""
        logger.info(f"AngelHeart[{chat_id}]: 混脸熟路径已停用，默认不回复")
        return SecretaryDecision(
            should_reply=False, reply_strategy="混脸熟已停用", topic="未知",
            entities=[], facts=[], keywords=[]
        )

    async def _handle_summoned_reply(self, event: AstrMessageEvent, chat_id: str) -> SecretaryDecision:
        """兼容旧路径：唤醒决策改由统一激活入口处理。"""
        logger.info(f"AngelHeart[{chat_id}]: 旧被呼唤路径已停用，改由激活入口处理")
        return SecretaryDecision(
            should_reply=False, reply_strategy="请走防抖激活入口", topic="未知",
            entities=[], facts=[], keywords=[]
        )

    async def _handle_observation_reply(self, event: AstrMessageEvent, chat_id: str) -> SecretaryDecision:
        """兼容旧路径：在场决策改由统一激活入口处理。"""
        logger.info(f"AngelHeart[{chat_id}]: 旧在场路径已停用，改由激活入口处理")
        return SecretaryDecision(
            should_reply=False, reply_strategy="请走防抖激活入口", topic="未知",
            entities=[], facts=[], keywords=[]
        )

    async def _handle_not_present_check(self, event: AstrMessageEvent, chat_id: str) -> SecretaryDecision:
        """兼容旧路径：离场未唤醒不应进入秘书；若误入则不回复。"""
        logger.debug(f"AngelHeart[{chat_id}]: 离场检查路径命中，默认不回复")
        return SecretaryDecision(
            should_reply=False, reply_strategy="离场", topic="未知",
            entities=[], facts=[], keywords=[]
        )

    async def perform_analysis(
        self,
        recent_dialogue: List[Dict],
        db_history: List[Dict],
        chat_id: str,
        event: AstrMessageEvent | None = None,
    ) -> SecretaryDecision:
        """
        秘书职责：分析缓存内容并做出决策。
        此函数只负责调用LLM分析器，不再关心缓存和历史记录的剪枝。

        Args:
            recent_dialogue (List[Dict]): 剪枝后的新消息列表。
            db_history (List[Dict]): 数据库中的历史记录。
            chat_id (str): 会话ID。
            event: 当前激活事件；用于排除本轮 work_id。

        Returns:
            SecretaryDecision: 分析后得出的决策对象。
        """
        logger.info(f"AngelHeart[{chat_id}]: 秘书开始调用LLM进行分析...")

        try:
            work_ledger_text = ""
            try:
                current_work_id = ""
                if event is not None and hasattr(event, "get_extra"):
                    current_work_id = str(event.get_extra("angelheart_work_id", "") or "")
                if not current_work_id and event is not None:
                    try:
                        current_work_id = str(
                            getattr(event, "angelheart_internal_event_id", "") or ""
                        )
                    except Exception:
                        current_work_id = ""
                work_ledger_text = self.angel_context.work_ledger.format_for_secretary(
                    chat_id, current_work_id=current_work_id
                )
            except Exception:
                work_ledger_text = ""

            # 调用分析器进行决策，传递结构化的上下文
            decision = await self.llm_analyzer.analyze_and_decide(
                historical_context=db_history,
                recent_dialogue=recent_dialogue,
                chat_id=chat_id,
                work_ledger_text=work_ledger_text,
            )

            # 移除重复日志，已在 process_notification 中记录
            return decision

        except asyncio.TimeoutError as e:
            return self._handle_analysis_error(e, "秘书处理过程(超时)", chat_id)
        except Exception as e:
            return self._handle_analysis_error(e, "秘书处理过程", chat_id)

    async def update_last_event_time(self, chat_id: str):
        """在 LLM 成功响应后，更新最后一次事件（回复）的时间戳"""
        await self.angel_context.update_last_analysis_time(chat_id)

    @property
    def config_manager(self):
        return self._config_manager

    @config_manager.setter
    def config_manager(self, value):
        self._config_manager = value

    @property
    def waiting_time(self):
        return self.config_manager.waiting_time

    def _handle_analysis_error(self, error: Exception, context: str, chat_id: str) -> SecretaryDecision:
        """
        统一处理分析错误

        Args:
            error (Exception): 捕获到的异常
            context (str): 错误发生的上下文描述
            chat_id (str): 会话ID

        Returns:
            SecretaryDecision: 表示分析失败的决策对象
        """
        logger.error(
            f"AngelHeart[{chat_id}]: {context}出错: {error}", exc_info=True
        )
        # 返回一个默认的不参与决策
        return SecretaryDecision(
            should_reply=False, reply_strategy=f"{context}失败", topic="未知",
            entities=[], facts=[], keywords=[]
        )

    # ========== 4状态机制：状态感知分析 ==========

    async def process_notification(self, event: AstrMessageEvent):
        """
        处理前台通知
        秘书只负责处理消息，不做任何条件检查
        注意：调用此方法时，前台已经获取了门锁

        Args:
            event: 消息事件
        """
        chat_id = event.unified_msg_origin

        try:
            # 1. 获取上下文
            historical_context, recent_dialogue, boundary_ts = self.angel_context.conversation_ledger.get_context_snapshot(chat_id)

            if not recent_dialogue:
                logger.info(f"AngelHeart[{chat_id}]: 无新消息需要分析。")
                return

            # 2. 执行分析
            decision = await self.perform_analysis(
                recent_dialogue, historical_context, chat_id, event=event
            )

            # 3. 处理决策结果
            await self._handle_analysis_result(decision, recent_dialogue, historical_context, boundary_ts, event, chat_id)

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 秘书处理异常: {e}", exc_info=True)



    async def _handle_analysis_result(self, decision, recent_dialogue, historical_context, boundary_ts, event, chat_id):
        """
        处理分析结果（复用原有逻辑）

        注意：此方法不返回任何值，锁的释放由调用者的 finally 块统一处理
        """
        if decision and decision.should_reply:
            logger.info(f"AngelHeart[{chat_id}]: 决策为'参与'。策略: {decision.reply_strategy}")

            # 图片转述处理
            try:
                caption_provider_id = self.config_manager.image_caption_provider_id
            except Exception as e:
                logger.warning(f"AngelHeart[{chat_id}]: 无法读取图片转述配置: {e}")
                caption_provider_id = ""

            caption_count = await self.angel_context.conversation_ledger.process_image_captions_if_needed(
                chat_id=chat_id,
                caption_provider_id=caption_provider_id,
                astr_context=self.context
            )
            if caption_count > 0:
                logger.info(f"AngelHeart[{chat_id}]: 已为 {caption_count} 张图片生成转述")

            # 启动耐心计时器
            await self.angel_context.start_patience_timer(chat_id)

            # 旁路上下文：聊天记录 + 决策 挂到本事件，供日志/下游钩子读
            # 不写会话共享缓存；主脑 req 临时注入仍只留工作账本
            full_snapshot = historical_context + recent_dialogue
            try:
                event.angelheart_context = json_serialize_context(full_snapshot, decision)
                logger.info(f"AngelHeart[{chat_id}]: 上下文已注入 event.angelheart_context")
            except Exception as e:
                logger.error(f"AngelHeart[{chat_id}]: 注入上下文失败: {e}")
                event.angelheart_context = json.dumps({
                    "chat_records": [],
                    "secretary_decision": {"should_reply": False, "error": "注入失败"},
                    "error": "注入失败"
                }, ensure_ascii=False)

            # 决策门闩：要回就唤醒主脑
            if not self.config_manager.debug_mode:
                event.is_at_or_wake_command = True
            else:
                logger.info(f"AngelHeart[{chat_id}]: 调试模式已启用，阻止了实际唤醒。")
                try:
                    work_id = ""
                    if hasattr(event, "get_extra"):
                        work_id = str(event.get_extra("angelheart_work_id", "") or "")
                    if not work_id:
                        work_id = str(getattr(event, "angelheart_internal_event_id", "") or "")
                    if work_id:
                        self.angel_context.work_ledger.complete_work(
                            chat_id,
                            work_id,
                            status="done",
                            result_summary="debug跳过发送",
                        )
                except Exception:
                    pass

        elif decision:
            logger.info(f"AngelHeart[{chat_id}]: 决策为'不参与'。原因: {decision.reply_strategy}")
        else:
            logger.warning(f"AngelHeart[{chat_id}]: 分析失败，无决策结果")
