"""
AngelHeart 插件 - 秘书角色 (Secretary)
负责定时分析缓存内容，决定是否回复。
"""

import asyncio
from typing import Dict, List

from ..core.llm_analyzer import LLMAnalyzer
from ..models.analysis_result import SecretaryDecision
from ..core.angel_heart_status import AngelHeartStatus
from astrbot.api.event import AstrMessageEvent

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


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

        boundary_message_id = self.angel_context.debounce_manager.get_end_message_id(event)
        if not boundary_message_id:
            boundary_message_id = str(
                getattr(getattr(event, "message_obj", None), "message_id", "") or ""
            )
        historical_context, recent_dialogue, boundary_ts = (
            self.angel_context.conversation_ledger.get_context_snapshot(
                chat_id, boundary_message_id
            )
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
                        "boundary_message_id": boundary_message_id,
                    },
                )
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 固化决策上下文失败: {e}")

        decision = await self.perform_analysis(
            recent_dialogue, historical_context, chat_id, event=event
        )

        # 点名巡检 / 助理防抖放行后必须回复；是否有理由不再影响门闩结果。
        if must_reply:
            decision.should_reply = True
            if not decision.reply_strategy or decision.reply_strategy == "继续观察":
                decision.reply_strategy = "必须回应"

        return decision

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

            return decision

        except asyncio.TimeoutError as e:
            return self._handle_analysis_error(e, "秘书处理过程(超时)", chat_id)
        except Exception as e:
            return self._handle_analysis_error(e, "秘书处理过程", chat_id)

    @property
    def config_manager(self):
        return self._config_manager

    @config_manager.setter
    def config_manager(self, value):
        self._config_manager = value

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
