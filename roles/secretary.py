"""
AngelHeart 插件 - 秘书角色 (Secretary)
负责定时分析缓存内容，决定是否回复。
"""

import time
import asyncio
import json
import datetime
from typing import Dict, List
from enum import Enum

# 导入公共工具函数
from ..core.utils import json_serialize_context

from ..core.llm_analyzer import LLMAnalyzer
from ..models.analysis_result import SecretaryDecision
from ..core.angel_heart_status import AngelHeartStatus, StatusChecker

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from astrbot.api.event import AstrMessageEvent

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
        self.DECISION_EXPIRATION_MINUTES = 3  # 决策超时时间（分钟）

        # -- 核心组件 --
        # 初始化 LLMAnalyzer
        analyzer_model_name = self.config_manager.analyzer_model
        reply_strategy_guide = self.config_manager.reply_strategy_guide
        # 传递 context 对象，让 LLMAnalyzer 在需要时动态获取 provider
        self.llm_analyzer = LLMAnalyzer(
            analyzer_model_name, context, reply_strategy_guide, self.config_manager
        )

    def _is_decision_expired(self, decision: SecretaryDecision) -> bool:
        """
        检查决策是否已过期（超过3分钟）。

        此方法用于防止因外部依赖（如LLM响应慢）导致的系统死锁。
        任何超过3分钟的决策都将被视为过期，允许启动新的分析。

        Args:
            decision (SecretaryDecision): 要检查的决策对象。

        Returns:
            bool: 如果决策已过期则返回 True，否则返回 False。
        """
        # 计算决策创建时间与当前时间的差值
        time_diff = datetime.datetime.now(datetime.timezone.utc) - decision.created_at
        # 如果差值超过设定的超时时间，则认为决策已过期
        return time_diff > datetime.timedelta(minutes=self.DECISION_EXPIRATION_MINUTES)

    async def perform_analysis(
        self, recent_dialogue: List[Dict], db_history: List[Dict], chat_id: str
    ) -> SecretaryDecision:
        """
        秘书职责：分析缓存内容并做出决策。
        此函数只负责调用LLM分析器，不再关心缓存和历史记录的剪枝。

        Args:
            recent_dialogue (List[Dict]): 剪枝后的新消息列表。
            db_history (List[Dict]): 数据库中的历史记录。
            chat_id (str): 会话ID。

        Returns:
            SecretaryDecision: 分析后得出的决策对象。
        """
        logger.info(f"AngelHeart[{chat_id}]: 秘书开始调用LLM进行分析...")

        try:
            # 调用分析器进行决策，传递结构化的上下文
            decision = await self.llm_analyzer.analyze_and_decide(
                historical_context=db_history, recent_dialogue=recent_dialogue, chat_id=chat_id
            )

            # 移除重复日志，已在 process_notification 中记录
            return decision

        except asyncio.TimeoutError as e:
            return self._handle_analysis_error(e, "秘书处理过程(超时)", chat_id)
        except Exception as e:
            return self._handle_analysis_error(e, "秘书处理过程", chat_id)

    def get_decision(self, chat_id: str) -> SecretaryDecision | None:
        """获取指定会话的决策"""
        return self.angel_context.get_decision(chat_id)

    async def update_last_event_time(self, chat_id: str):
        """在 LLM 成功响应后，更新最后一次事件（回复）的时间戳"""
        await self.angel_context.update_last_analysis_time(chat_id)

    async def clear_decision(self, chat_id: str):
        """清除指定会话的决策"""
        await self.angel_context.clear_decision(chat_id)


    def get_cached_decisions_for_display(self) -> list:
        """获取用于状态显示的缓存决策列表"""
        cached_items = list(self.angel_context.analysis_cache.items())
        display_list = []
        for chat_id, result in reversed(cached_items[-5:]): # 显示最近的5条
            if result:
                topic = result.topic
                display_list.append(f"- {chat_id}:")
                display_list.append(f"  - 话题: {topic}")
            else:
                display_list.append(f"- {chat_id}: (分析数据不完整)")
        return display_list




    @property
    def config_manager(self):
        return self._config_manager

    @config_manager.setter
    def config_manager(self, value):
        self._config_manager = value

    @property
    def waiting_time(self):
        return self.config_manager.waiting_time

    @property
    def cache_expiry(self):
        return self.config_manager.cache_expiry

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
            should_reply=False, reply_strategy=f"{context}失败", topic="未知"
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
            decision = await self.perform_analysis(recent_dialogue, historical_context, chat_id)

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
                cfg = self.context.get_config(umo=event.unified_msg_origin)["provider_settings"]
                caption_provider_id = cfg.get("default_image_caption_provider_id", "")
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

            # 存储决策
            decision.boundary_timestamp = boundary_ts
            decision.recent_dialogue = recent_dialogue
            await self.angel_context.update_analysis_cache(chat_id, decision, reason="分析完成")

            # 启动耐心计时器
            await self.angel_context.start_patience_timer(chat_id)

            # 标记对话为已处理
            self.angel_context.conversation_ledger.mark_as_processed(chat_id, boundary_ts)

            # 注入上下文
            full_snapshot = historical_context + recent_dialogue
            try:
                event.angelheart_context = json_serialize_context(full_snapshot, decision)
                logger.info(f"AngelHeart[{chat_id}]: 上下文已注入 event.angelheart_context")
            except Exception as e:
                logger.error(f"AngelHeart[{chat_id}]: 注入上下文失败: {e}")
                event.angelheart_context = json.dumps({
                    "chat_records": [],
                    "secretary_decision": {"should_reply": False, "error": "注入失败"},
                    "needs_search": False,
                    "error": "注入失败"
                }, ensure_ascii=False)

            # 唤醒主脑
            if not self.config_manager.debug_mode:
                event.is_at_or_wake_command = True
            else:
                logger.info(f"AngelHeart[{chat_id}]: 调试模式已启用，阻止了实际唤醒。")

        elif decision:
            logger.info(f"AngelHeart[{chat_id}]: 决策为'不参与'。原因: {decision.reply_strategy}")
            await self.angel_context.clear_decision(chat_id)
            self.angel_context.conversation_ledger.mark_as_processed(chat_id, boundary_ts)
        else:
            logger.warning(f"AngelHeart[{chat_id}]: 分析失败，无决策结果")
            await self.angel_context.clear_decision(chat_id)
            self.angel_context.conversation_ledger.mark_as_processed(chat_id, boundary_ts)

    