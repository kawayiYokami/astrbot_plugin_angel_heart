"""
AngelHeart 插件 - 全局上下文管理器
集中管理所有共享状态，解决循环依赖和状态分散问题。
"""

import time
import asyncio
from typing import Dict, Optional
from collections import OrderedDict

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from astrbot.core.star.context import Context
from astrbot.api.event import MessageChain
from astrbot.core.message.components import Plain
from ..models.analysis_result import SecretaryDecision
from ..core.conversation_ledger import ConversationLedger


class AngelHeartContext:
    """AngelHeart 全局上下文管理器"""

    def __init__(self, config_manager, astr_context: Context):
        """
        初始化全局上下文。

        Args:
            config_manager: 配置管理器实例，用于获取观察期时长等配置。
            astr_context: AstrBot 的主 context，用于发送消息等操作。
        """
        self.config_manager = config_manager
        self.astr_context = astr_context

        # 核心资源：对话总账
        self.conversation_ledger = ConversationLedger(
            cache_expiry=config_manager.cache_expiry
        )

        # 门牌管理
        self.processing_chats: Dict[str, float] = {}  # chat_id -> 开始分析时间
        self.processing_lock: asyncio.Lock = asyncio.Lock()  # 门牌操作锁

        # 事件扣押（V2：使用 Future 阻塞机制，V3：添加调度锁）
        self.pending_futures: Dict[str, asyncio.Future] = {}  # chat_id -> 阻塞中的 Future
        self.dispatch_lock: asyncio.Lock = asyncio.Lock()  # 调度锁，防止并发竞态

        # 观察期管理
        self.observation_timers: Dict[str, asyncio.Task] = {}  # chat_id -> 观察期计时任务
        self.patience_timers: Dict[str, asyncio.Task] = {}  # chat_id -> V3新增，耐心计时器任务

        # 时序控制
        self.last_analysis_time: Dict[str, float] = {}  # chat_id -> 上次分析时间
        self.silenced_until: Dict[str, float] = {}  # chat_id -> 闭嘴结束时间

        # 决策缓存
        self.analysis_cache: OrderedDict[str, SecretaryDecision] = OrderedDict()
        self.CACHE_MAX_SIZE = 100  # 缓存最大尺寸

    @property
    def observation_duration(self) -> float:
        """观察期时长（秒），复用 config_manager.waiting_time"""
        return self.config_manager.waiting_time

    # ========== 门牌管理 ==========

    async def is_chat_processing(self, chat_id: str) -> bool:
        """
        检查该会话是否正在被处理。

        Args:
            chat_id (str): 会话ID。

        Returns:
            bool: 如果正在处理返回 True，否则返回 False。
        """
        async with self.processing_lock:
            # 检查门牌是否存在且未卡死
            if chat_id in self.processing_chats:
                # 检查是否卡死（超过5分钟）
                if time.time() - self.processing_chats[chat_id] > 300:
                    logger.warning(f"检测到卡死的门牌，自动清理: {chat_id}")
                    self.processing_chats.pop(chat_id, None)
                    return False
                return True
            return False

    async def acquire_chat_processing(self, chat_id: str) -> bool:
        """
        原子性地尝试获取会话处理权（挂上门牌）。

        Args:
            chat_id (str): 会话ID。

        Returns:
            bool: 成功挂上门牌返回 True，如果门牌已被占用则返回 False。
        """
        async with self.processing_lock:
            start_time = self.processing_chats.get(chat_id)

            # 检查门牌是否卡死（例如，超过5分钟）
            if start_time is not None:
                STALE_THRESHOLD_SECONDS = 300  # 5分钟
                if time.time() - start_time > STALE_THRESHOLD_SECONDS:
                    logger.warning(
                        f"AngelHeart[{chat_id}]: 检测到会话处理卡死 (超过 {STALE_THRESHOLD_SECONDS} 秒)，自动重置门牌。"
                    )
                    # 自动清理卡死的门牌
                    self.processing_chats.pop(chat_id, None)
                    start_time = None

            # 如果门牌不存在（或刚被清理），则挂上新门牌
            if start_time is None:
                self.processing_chats[chat_id] = time.time()
                logger.debug(
                    f"AngelHeart[{chat_id}]: 已挂上门牌 (开始处理时间: {self.processing_chats[chat_id]})"
                )
                return True
            else:
                # 门牌正挂着，且未卡死
                logger.debug(
                    f"AngelHeart[{chat_id}]: 门牌已被占用 (开始时间: {start_time})"
                )
                return False

    async def release_chat_processing(self, chat_id: str):
        """
        原子性地释放会话处理权（收起门牌）。

        Args:
            chat_id (str): 会话ID。
        """
        async with self.processing_lock:
            if self.processing_chats.pop(chat_id, None) is not None:
                logger.debug(f"AngelHeart[{chat_id}]: 已收起门牌")

    # ========== 事件扣押与观察期 (V2: Future 阻塞机制) ==========

    async def hold_and_start_observation(self, chat_id: str) -> asyncio.Future:
        """
        扣押事件并启动观察期 (V3版本：添加调度锁)。

        当 Secretary 忙中时，创建一个 Future 用于阻塞当前事件。
        如果之前有旧的 Future 在等待，则向其发送 KILL 信号，让旧事件终止。

        Args:
            chat_id (str): 会话ID。

        Returns:
            asyncio.Future: 新创建的 Future，调用者应 await 它来阻塞事件。
        """
        async with self.dispatch_lock:  # V3：添加调度锁，防止并发竞态
            # 1. 检查是否有旧的 Future，如果有则杀死它
            old_future = self.pending_futures.get(chat_id)
            if old_future and not old_future.done():
                logger.debug(
                    f"AngelHeart[{chat_id}]: 检测到旧事件正在等待，发送 KILL 信号"
                )
                old_future.set_result("KILL")

            # 2. 创建新的 Future
            new_future = asyncio.Future()
            self.pending_futures[chat_id] = new_future

            # 3. 取消之前的观察期计时器
            if chat_id in self.observation_timers:
                self.observation_timers[chat_id].cancel()
                logger.debug(f"AngelHeart[{chat_id}]: 已取消之前的观察期")

            # 4. 启动新的观察期计时器
            self.observation_timers[chat_id] = asyncio.create_task(
                self._observation_timeout_handler(chat_id)
            )

            logger.info(
                f"AngelHeart[{chat_id}]: 已创建 Future 并启动观察期 ({self.observation_duration} 秒)"
            )

            # 5. 返回 Future 供调用者阻塞
            return new_future

    async def _observation_timeout_handler(self, chat_id: str):
        """
        观察期超时处理 (V3版本：轮询等待模式，防止僵尸事件)。

        等待指定的观察期时长后，如果秘书仍忙，进入轮询等待模式。
        最多等待3分钟，如果秘书一直忙碌，则发送 KILL 信号让事件自杀。

        Args:
            chat_id (str): 会话ID。
        """
        try:
            # 1. 首先，完成初始的观察期等待
            await asyncio.sleep(self.observation_duration)

            # 2. 设置轮询参数
            max_wait_seconds = 180  # 最多等待3分钟
            recheck_interval_seconds = 3  # 每3秒检查一次
            total_waited = 0

            # 3. 进入轮询循环
            while total_waited < max_wait_seconds:
                # 检查秘书是否空闲
                if not await self.is_chat_processing(chat_id):
                    # 秘书已空闲，发送 PROCESS 信号并成功退出
                    future = self.pending_futures.get(chat_id)
                    if future and not future.done():
                        logger.info(
                            f"AngelHeart[{chat_id}]: 秘书已空闲，发送 PROCESS 信号唤醒事件"
                        )
                        future.set_result("PROCESS")

                    # 清理
                    self.pending_futures.pop(chat_id, None)
                    self.observation_timers.pop(chat_id, None)
                    return  # 任务成功结束

                # 秘书仍在忙，继续等待
                logger.debug(
                    f"AngelHeart[{chat_id}]: 秘书仍忙碌，继续等待... (已等待 {total_waited}秒/{max_wait_seconds}秒)"
                )
                await asyncio.sleep(recheck_interval_seconds)
                total_waited += recheck_interval_seconds

            # 4. 超时处理：如果循环结束仍未等到秘书空闲，说明已超时
            logger.warning(
                f"AngelHeart[{chat_id}]: 等待秘书空闲超过{max_wait_seconds}秒，事件已超时，发送 KILL 信号"
            )
            future = self.pending_futures.get(chat_id)
            if future and not future.done():
                # 发送 KILL 信号，让 FrontDesk 终结这个僵尸事件
                future.set_result("KILL")

            # 清理
            self.pending_futures.pop(chat_id, None)
            self.observation_timers.pop(chat_id, None)

        except asyncio.CancelledError:
            logger.debug(f"AngelHeart[{chat_id}]: 观察期计时器被取消")
        except Exception as e:
            logger.error(
                f"AngelHeart[{chat_id}]: 观察期超时处理出错: {e}", exc_info=True
            )

    # ========== V3: Patience Timer (Multi-Stage) ==========

    async def _patience_timer_handler(self, chat_id: str):
        """动态耐心计时器处理器，根据配置发送安心词。"""
        try:
            # 获取配置
            interval = self.config_manager.patience_interval
            comfort_words = self.config_manager.comfort_words.split('|')

            # 发送每个安心词
            for i, word in enumerate(comfort_words):
                await asyncio.sleep(interval)
                logger.debug(f"AngelHeart[{chat_id}]: 耐心计时器 - 阶段{i+1}触发 ({(i+1)*interval}s)")
                chain = MessageChain([Plain(word.strip())])
                await self.astr_context.send_message(chat_id, chain)

        except asyncio.CancelledError:
            logger.debug(f"AngelHeart[{chat_id}]: 耐心计时器被取消，任务终止。")
        except Exception as e:
            logger.error(
                f"AngelHeart[{chat_id}]: 耐心计时器处理出错: {e}", exc_info=True
            )
    async def start_patience_timer(self, chat_id: str):
        """启动或重置指定会话的耐心计时器。"""
        # 先取消已存在的计时器
        await self.cancel_patience_timer(chat_id)

        # 创建并存储新的计时器任务
        self.patience_timers[chat_id] = asyncio.create_task(
            self._patience_timer_handler(chat_id)
        )
        comfort_words = self.config_manager.comfort_words.split('|')
        logger.info(f"AngelHeart[{chat_id}]: 已启动耐心计时器（{len(comfort_words)}阶段，每隔{self.config_manager.patience_interval}秒发送一次）。")

    async def cancel_patience_timer(self, chat_id: str):
        """取消指定会话的耐心计时器。"""
        if chat_id in self.patience_timers:
            timer_task = self.patience_timers.pop(chat_id)
            if not timer_task.done():
                timer_task.cancel()
                logger.debug(f"AngelHeart[{chat_id}]: 已取消正在运行的耐心计时器。")

    # ========== 决策缓存管理 ==========

    async def update_analysis_cache(
        self, chat_id: str, result: SecretaryDecision, reason: str = "分析完成"
    ):
        """
        更新分析缓存。

        Args:
            chat_id (str): 会话ID。
            result (SecretaryDecision): 决策结果。
            reason (str): 更新原因（用于日志）。
        """
        async with self.processing_lock:
            self.analysis_cache[chat_id] = result

            # 如果缓存超过最大尺寸，则移除最旧的条目
            if len(self.analysis_cache) > self.CACHE_MAX_SIZE:
                self.analysis_cache.popitem(last=False)

            logger.info(
                f"AngelHeart[{chat_id}]: {reason}，已更新缓存。决策: {'回复' if result.should_reply else '不回复'} | 策略: {result.reply_strategy} | 话题: {result.topic} | 目标: {result.reply_target}"
            )

    def get_decision(self, chat_id: str) -> Optional[SecretaryDecision]:
        """获取指定会话的决策"""
        return self.analysis_cache.get(chat_id)

    async def clear_decision(self, chat_id: str):
        """清除指定会话的决策"""
        async with self.processing_lock:
            if self.analysis_cache.pop(chat_id, None) is not None:
                logger.debug(f"AngelHeart[{chat_id}]: 已从缓存中移除一次性决策。")

    # ========== 时序控制 ==========

    async def update_last_analysis_time(self, chat_id: str):
        """更新最后一次分析的时间戳"""
        async with self.processing_lock:
            self.last_analysis_time[chat_id] = time.time()
            logger.debug(f"AngelHeart[{chat_id}]: 已更新 last_analysis_time。")

    def get_last_analysis_time(self, chat_id: str) -> float:
        """获取最后一次分析的时间戳"""
        return self.last_analysis_time.get(chat_id, 0)
