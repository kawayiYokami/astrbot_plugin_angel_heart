"""
AngelHeart 插件 - 秘书角色 (Secretary)
负责定时分析缓存内容，决定是否回复。
"""

import time
import asyncio
import json
from collections import OrderedDict
from typing import Dict, List
from enum import Enum

class AwakenReason(Enum):
    """秘书唤醒原因枚举"""
    OK = "ok"
    COOLING_DOWN = "cooling_down"
    PROCESSING = "processing"

from astrbot.api.event import AstrMessageEvent

from astrbot.api import logger

# 导入公共工具函数
from ..core.utils import get_latest_message_time, convert_content_to_string, prune_old_messages

from ..core.llm_analyzer import LLMAnalyzer
from ..models.analysis_result import SecretaryDecision


class Secretary:
    """
    秘书角色 - 专注的分析与决策员
    """

    def __init__(self, config_manager, context, front_desk):
        """
        初始化秘书角色。

        Args:
            config_manager: 配置管理器实例。
            context: 插件上下文对象。
            front_desk: 前台角色实例。
        """
        self.config_manager = config_manager
        self.context = context
        self.front_desk = front_desk

        # -- 并发控制 --
        self._shared_state_lock: asyncio.Lock = asyncio.Lock()
        """用于保护共享状态 (last_analysis_time, analysis_cache) 的全局锁"""

        # -- 秘书调度 --
        self.last_analysis_time: Dict[str, float] = {}
        """秘书上次分析时间：用于控制分析频率"""
        # 秘书分析间隔：两次分析之间的最小时间间隔（秒）
        # 缓存过期时间：消息缓存的过期时间（秒）

        # -- 决策缓存 --
        # 使用OrderedDict实现有大小限制的缓存
        self.analysis_cache: OrderedDict[str, SecretaryDecision] = OrderedDict()
        # 定义缓存的最大尺寸
        self.CACHE_MAX_SIZE = 100

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
        from datetime import datetime, timedelta
        # 计算决策创建时间与当前时间的差值
        time_diff = datetime.now() - decision.created_at
        # 如果差值超过设定的超时时间，则认为决策已过期
        return time_diff > timedelta(minutes=self.DECISION_EXPIRATION_MINUTES)

    def should_awaken(self, chat_id: str) -> tuple[bool, AwakenReason, dict]:
        """
        检查是否应该唤醒秘书进行分析。

        Args:
            chat_id (str): 会话ID。

        Returns:
            tuple[bool, AwakenReason, dict]: 一个包含三个元素的元组：
                - bool: 如果应该唤醒则返回 True，否则返回 False。
                - AwakenReason: 唤醒或不唤醒的具体原因。
                - dict: 附加信息，例如剩余冷却时间。
        """
        current_time = time.time()
        last_time = self.last_analysis_time.get(chat_id, 0)

        # 1. 检查冷却时间：确保两次分析之间有足够的时间间隔
        if current_time - last_time < self.config_manager.waiting_time:
            remaining = max(0, self.config_manager.waiting_time - (current_time - last_time))
            return False, AwakenReason.COOLING_DOWN, {"remaining": remaining}

        # 2. 检查是否存在未处理的决策：防止在机器人完成回复前启动新的分析
        existing_decision = self.analysis_cache.get(chat_id)
        if existing_decision:
            # 如果存在决策，检查是否已过期（超过3分钟）
            if not self._is_decision_expired(existing_decision):
                # 决策未过期，说明机器人仍在处理中，不应唤醒
                return False, AwakenReason.PROCESSING, {}

        # 如果冷却时间已过，且没有未过期的决策，则应唤醒秘书进行分析
        return True, AwakenReason.OK, {}

    async def process_notification(
        self, event: AstrMessageEvent
    ):
        """
        处理前台发来的通知。这是秘书的核心工作入口。
        秘书将主动从前台获取最新的缓存消息。

        Args:
            event (AstrMessageEvent): 触发此通知的原始事件。
        """
        chat_id = event.unified_msg_origin
        logger.info(f"AngelHeart[Debug-LockID]: 正在使用 chat_id: '{chat_id}' 进行状态锁定。")
        logger.info(f"AngelHeart[{chat_id}]: 秘书收到前台通知")

        # 1. 锁定并检查状态：决定是否启动新的分析
        async with self._shared_state_lock:
            should_awaken, reason, info = self.should_awaken(chat_id)

            if not should_awaken:
                if reason == AwakenReason.COOLING_DOWN:
                    remaining = info.get("remaining", 0)
                    interval = self.config_manager.waiting_time
                    logger.info(f"AngelHeart[{chat_id}]: 放弃分析请求，原因: 应答冷却中 (剩余 {remaining:.1f}s / {interval}s)")
                elif reason == AwakenReason.PROCESSING:
                    logger.info(f"AngelHeart[{chat_id}]: 放弃分析请求，原因: 专家正在处理先前的请求")
                return

            # 条件满足，准备启动分析
            logger.debug(f"AngelHeart[{chat_id}]: 条件满足，准备启动分析。")
        # 锁在此处被释放，允许其他会话并发检查

        decision = None
        db_history = [] # 初始化 db_history，使其在 finally 块后可用
        try:
            # 2. 执行分析 (无锁)：这可能是一个耗时操作
            # 注意：在调用 perform_analysis 之前，先获取 db_history
            db_history = await self.get_conversation_history(chat_id)
            decision = await self.perform_analysis(chat_id, db_history) # 传入 db_history

        finally:
            # 3. 处理决策结果（无论分析成功与否）
            if decision and decision.should_reply:
                # 在唤醒核心前，将待处理历史（数据库历史记录）同步回数据库
                # 不包含当前消息，因为当前消息会在后续被核心系统处理并添加到记录中
                curr_cid = await self.context.conversation_manager.get_curr_conversation_id(
                    chat_id
                )
                if curr_cid:
                    await self.context.conversation_manager.update_conversation(
                        unified_msg_origin=chat_id,
                        conversation_id=curr_cid,
                        history=db_history,  # 只同步数据库历史记录，不包含当前消息
                    )
                    logger.info(
                        f"AngelHeart[{chat_id}]: 决策为'参与'，已同步待处理历史并标记事件以唤醒核心。策略: {decision.reply_strategy}"
                    )

                    # 检查是否处于调试模式
                    if self.config_manager.debug_mode:
                        logger.info(f"AngelHeart[{chat_id}]: 调试模式已启用，分析器建议回复但阻止了实际唤醒。")
                    else:
                        event.is_at_or_wake_command = True  # 将此事件标记为需要LLM回复，以唤醒AstrBot的核心回复逻辑
            elif decision:
                logger.info(f"AngelHeart[{chat_id}]: 决策为'不参与'。")

    async def perform_analysis(
        self, chat_id: str, db_history: List[Dict]
    ) -> SecretaryDecision:
        """
        秘书职责：分析缓存内容并做出决策。
        秘书将主动从前台获取最新的缓存消息。

        Args:
            chat_id (str): 会话ID。
            db_history (List[Dict]): 数据库中的历史记录。

        Returns:
            SecretaryDecision: 分析后得出的决策对象。
        """
        # 主动从前台获取最新的缓存消息
        cached_messages = self.front_desk.get_messages(chat_id)
        logger.info(f"AngelHeart[{chat_id}]: 秘书开始分析...")

        # 注意：last_analysis_time 的更新已移至 process_notification 的 finally 块中，
        # 以确保准确记录分析完成的时间点。

        try:
            # 1. 获取数据库历史 (前台已清理过期消息)
            # db_history 已由调用者传入，无需在此重复获取

            # 1. 智能剪枝：从 cached_messages 中移除已经存在于 db_history 中的消息
            recent_dialogue = prune_old_messages(cached_messages, db_history)

            # 2. 检查剪枝后是否还有新消息
            if not recent_dialogue:
                logger.debug(f"AngelHeart[{chat_id}]: 缓存中的消息均已存在于历史记录中，无需重复分析。")
                # 返回一个默认的不参与决策
                return SecretaryDecision(
                    should_reply=False, reply_strategy="无新消息", topic="未知"
                )

            # 3. 调用分析器进行决策，传递结构化的上下文
            decision = await self.llm_analyzer.analyze_and_decide(
                historical_context=db_history, recent_dialogue=recent_dialogue, chat_id=chat_id
            )

            logger.info(
                f"AngelHeart[{chat_id}]: 秘书分析完成。决策: {'回复' if decision.should_reply else '不回复'} | 策略: {decision.reply_strategy} | 话题: {decision.topic} | 目标: {decision.reply_target}"
            )
            # 只有在决定回复时，才更新分析缓存，以避免阻塞后续请求
            if decision.should_reply:
                self._update_analysis_cache(chat_id, decision)
            return decision

        except asyncio.TimeoutError as e:
            return self._handle_analysis_error(e, "秘书处理过程(超时)", chat_id)
        except Exception as e:
            return self._handle_analysis_error(e, "秘书处理过程", chat_id)

    def _update_analysis_cache(self, chat_id: str, result: SecretaryDecision):
        """
        更新分析缓存

        将新的决策结果存入缓存，并维护缓存大小不超过限制。
        注意：决策包含创建时间戳，用于后续的超时检查。
        """

        self.analysis_cache[chat_id] = result
        # 如果缓存超过最大尺寸，则移除最旧的条目
        if len(self.analysis_cache) > self.CACHE_MAX_SIZE:
            self.analysis_cache.popitem(last=False)
        logger.info(f"AngelHeart[{chat_id}]: 分析完成，已更新缓存。决策: {'回复' if result.should_reply else '不回复'} | 策略: {result.reply_strategy} | 话题: {result.topic} | 目标: {result.reply_target}")

    def get_decision(self, chat_id: str) -> SecretaryDecision | None:
        """获取指定会话的决策"""
        return self.analysis_cache.get(chat_id)

    async def update_last_event_time(self, chat_id: str):
        """在 LLM 成功响应后，更新最后一次事件（回复）的时间戳"""
        async with self._shared_state_lock:
            self.last_analysis_time[chat_id] = time.time()
            logger.debug(f"AngelHeart[{chat_id}]: 已在 LLM 响应后更新 last_analysis_time。")

    async def clear_decision(self, chat_id: str):
        """清除指定会话的决策"""
        # 使用全局锁保护对共享状态的访问
        async with self._shared_state_lock:
            if self.analysis_cache.get(chat_id):
                if self.analysis_cache.pop(chat_id, None) is not None:
                    logger.debug(f"AngelHeart[{chat_id}]: 已从缓存中移除一次性决策。")

    def get_cached_decisions_for_display(self) -> list:
        """获取用于状态显示的缓存决策列表"""
        cached_items = list(self.analysis_cache.items())
        display_list = []
        for chat_id, result in reversed(cached_items[-5:]): # 显示最近的5条
            if result:
                topic = result.topic
                display_list.append(f"- {chat_id}:")
                display_list.append(f"  - 话题: {topic}")
            else:
                display_list.append(f"- {chat_id}: (分析数据不完整)")
        return display_list



    async def get_conversation_history(self, chat_id: str) -> List[Dict]:
        """获取当前会话的完整对话历史"""
        try:
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(
                chat_id
            )
            if not curr_cid:
                logger.debug(f"未找到当前会话的对话ID: {chat_id}")
                return []

            conversation = await self.context.conversation_manager.get_conversation(
                chat_id, curr_cid
            )
            if not conversation or not conversation.history:
                logger.debug(f"对话对象为空或无历史记录: {curr_cid}")
                return []

            history = json.loads(conversation.history)
            return history

        except json.JSONDecodeError as e:
            logger.error(f"解析对话历史JSON失败: {e}")
            return []
        except (TypeError, AttributeError) as e:
            logger.error(f"获取对话历史时发生类型或属性错误: {e}", exc_info=True)
            return []
        except Exception as e:
            logger.error(f"获取对话历史时发生未知错误: {e}", exc_info=True)
            return []

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
