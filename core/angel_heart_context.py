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
from ..core.angel_heart_status import AngelHeartStatus, StatusTransitionManager
from ..core.proactive_manager import ProactiveManager


class AngelHeartContext:
    """AngelHeart 全局上下文管理器"""

    def __init__(self, config_manager, astr_context: Context, data_dir):
        """
        初始化全局上下文。

        Args:
            config_manager: 配置管理器实例，用于获取观察期时长等配置。
            astr_context: AstrBot 的主 context，用于发送消息等操作。
            data_dir: 插件的数据目录路径，用于持久化存储。
        """
        self.config_manager = config_manager
        self.astr_context = astr_context

        # 核心资源：对话总账
        self.conversation_ledger = ConversationLedger(
            cache_expiry=config_manager.cache_expiry,
            data_dir=data_dir
        )

        # 门牌管理
        self.processing_chats: Dict[str, float] = {}  # chat_id -> 开始分析时间
        self.processing_lock: asyncio.Lock = asyncio.Lock()  # 门牌操作锁
        # 门锁冷却时间：归还门锁后需要等待的时间
        self.lock_cooldown_until: Dict[str, float] = {}  # chat_id -> 冷却结束时间

        # 事件扣押管理：来访者等候牌记录
        # pending_futures[chat_id] = 等候牌（Future对象）
        self.pending_futures: Dict[str, asyncio.Future] = {}
        # pending_events[chat_id] = 正在等待的事件对象（用于检测事件是否被停止）
        self.pending_events: Dict[str, any] = {}
        # dispatch_lock: 取号排队锁，防止来访者插队
        self.dispatch_lock: asyncio.Lock = asyncio.Lock()

        # 扣押超时计时器：每个来访者的最长等待时间限制
        self.detention_timeout_timers: Dict[str, asyncio.Task] = {}
        # 耐心计时器：老板思考时，让来访者安心等待的安抚机制
        # 每隔一段时间告诉来访者"老板在思考，请稍等"
        self.patience_timers: Dict[str, asyncio.Task] = {}

        # 时序控制
        self.last_analysis_time: Dict[str, float] = {}  # chat_id -> 上次分析时间
        self.silenced_until: Dict[str, float] = {}  # chat_id -> 闭嘴结束时间

        # 混脸熟冷却控制
        self.familiarity_cooldown_until: Dict[str, float] = {}  # chat_id -> 混脸熟冷却结束时间

        # 决策缓存
        self.analysis_cache: OrderedDict[str, SecretaryDecision] = OrderedDict()
        self.CACHE_MAX_SIZE = 100  # 缓存最大尺寸

        # ========== 4状态机制状态管理 ==========
        # 当前状态跟踪：chat_id -> AngelHeartStatus
        self.current_states: Dict[str, AngelHeartStatus] = {}

        # 状态转换管理器
        self.status_transition_manager = StatusTransitionManager(self)

        # 主动应答管理器
        self.proactive_manager = ProactiveManager(self)

    @property
    def detention_max_wait_time(self) -> float:
        """扣押最长等待时间（秒），来访者愿意等待老板的最长时间"""
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

    async def acquire_chat_processing(self, chat_id: str) -> tuple[bool, str, float]:
        """
        原子性地尝试获取会话处理权（挂上门牌）。
        包含冷却机制：归还门锁后需要等待一段时间才能再次获取。

        Args:
            chat_id (str): 会话ID。

        Returns:
            tuple[bool, str, float]: (是否成功, 失败原因, 剩余时间)
                - 成功时返回 (True, "SUCCESS", 0.0)
                - 冷却期失败时返回 (False, "COOLDOWN", 剩余秒数)
                - 被占用失败时返回 (False, "LOCKED", 0.0)
        """
        async with self.processing_lock:
            current_time = time.time()

            # 【新增】检查是否在冷却期，并自动清理过期记录
            cooldown_end = self.lock_cooldown_until.get(chat_id, 0)
            if current_time < cooldown_end:
                remaining = cooldown_end - current_time
                logger.debug(
                    f"AngelHeart[{chat_id}]: 门锁在冷却期，剩余 {remaining:.1f} 秒"
                )
                return False, "COOLDOWN", remaining

            # 自动清理过期的冷却记录
            if chat_id in self.lock_cooldown_until and current_time >= cooldown_end:
                del self.lock_cooldown_until[chat_id]
                logger.debug(f"AngelHeart[{chat_id}]: 已清理过期的冷却记录")

            start_time = self.processing_chats.get(chat_id)

            # 检查门牌是否卡死（例如，超过5分钟）
            if start_time is not None:
                STALE_THRESHOLD_SECONDS = 300  # 5分钟
                if current_time - start_time > STALE_THRESHOLD_SECONDS:
                    logger.warning(
                        f"AngelHeart[{chat_id}]: 检测到会话处理卡死 (超过 {STALE_THRESHOLD_SECONDS} 秒)，自动重置门牌。"
                    )
                    # 自动清理卡死的门牌
                    self.processing_chats.pop(chat_id, None)
                    start_time = None

            # 如果门牌不存在（或刚被清理），则挂上新门牌
            if start_time is None:
                self.processing_chats[chat_id] = current_time
                logger.debug(
                    f"AngelHeart[{chat_id}]: 已挂上门牌 (开始处理时间: {self.processing_chats[chat_id]})"
                )
                return True, "SUCCESS", 0.0
            else:
                # 门牌正挂着，且未卡死
                logger.debug(
                    f"AngelHeart[{chat_id}]: 门牌已被占用 (开始时间: {start_time})"
                )
                return False, "LOCKED", 0.0

    async def release_chat_processing(self, chat_id: str, set_cooldown: bool = True, duration: Optional[float] = None):
        """
        原子性地释放会话处理权（收起门牌）。
        可选择是否设置冷却期，防止立即重新获取。

        Args:
            chat_id (str): 会话ID。
            set_cooldown (bool): 是否设置冷却期，默认True
            duration (Optional[float]): 自定义冷却时长（秒）。如果未提供，则使用默认的 waiting_time。
        """
        async with self.processing_lock:
            if self.processing_chats.pop(chat_id, None) is not None:
                if set_cooldown:
                    # 如果未指定时长，则使用默认的回复后冷却时长
                    cooldown_duration = duration if duration is not None else self.config_manager.waiting_time
                    self.lock_cooldown_until[chat_id] = time.time() + cooldown_duration
                    logger.debug(f"AngelHeart[{chat_id}]: 已收起门牌，进入 {cooldown_duration:.2f} 秒冷却期")
                else:
                    logger.debug(f"AngelHeart[{chat_id}]: 已收起门牌，不设置冷却期")

    # ========== 事件扣押与观察期 (V2: Future 阻塞机制) ==========

    async def hold_and_start_observation(self, chat_id: str, event=None) -> asyncio.Future:
        """
        来访者取号等候机制

        当老板正在接待其他来访者时，新的来访者需要领取等候牌，
        在等候室等待老板忙完。如果之前有人已经在等，则取消他的等候。

        比喻说明：
        - 老板 = 秘书（正在处理消息）
        - 来访者 = 新消息事件
        - 等候牌 = Future（等待凭证）
        - 候室 = 观察期（等待时间）
        - 叫号 = set_result()（老板通知可以进来了）

        Args:
            chat_id (str): 来访者ID（会话ID）
            event: 消息事件对象，用于检测事件是否被撤回插件停止

        Returns:
            asyncio.Future: 等候牌，来访者需要 await 等待被叫号
        """
        # 排队取号，防止插队（确保取号过程的原子性）
        async with self.dispatch_lock:
            # 1. 检查是否有人已经在等这个老板
            old_ticket = self.pending_futures.get(chat_id)
            if old_ticket and not old_ticket.done():
                logger.debug(
                    f"AngelHeart[{chat_id}]: 检测到旧来访者正在等待，取消其等候资格"
                )
                old_ticket.set_result("KILL")  # 请旧来访者离开

            # 1.1. 清理旧的事件记录（如果有）
            if chat_id in self.pending_events:
                old_event = self.pending_events.pop(chat_id, None)
                if old_event:
                    logger.debug(f"AngelHeart[{chat_id}]: 已清理旧的事件记录")

            # 2. 给新来访者发放等候牌
            new_ticket = asyncio.Future()
            self.pending_futures[chat_id] = new_ticket

            # 2.1. 存储事件对象（如果有），用于检测撤回
            if event:
                self.pending_events[chat_id] = event
                logger.debug(f"AngelHeart[{chat_id}]: 已存储事件对象用于撤回检测")

            # 3. 取消之前的扣押超时计时器（如果有）
            if chat_id in self.detention_timeout_timers:
                self.detention_timeout_timers[chat_id].cancel()
                logger.debug(f"AngelHeart[{chat_id}]: 已取消之前的扣押超时计时")

            # 4. 启动新的扣押超时计时器（最长等待时间）
            self.detention_timeout_timers[chat_id] = asyncio.create_task(
                self._detention_timeout_handler(chat_id)
            )

            logger.info(
                f"AngelHeart[{chat_id}]: 已发放等候牌，最长等待180秒"
            )

            # 5. 返回等候牌给来访者
            return new_ticket

    async def _detention_timeout_handler(self, chat_id: str):
        """
        扣押超时处理

        来访者在扣押室等待，系统会定期检查老板是否已经空闲。
        如果老板空闲了，就请来访者进来；如果等太久，就请来访者离开。

        等待策略：
        1. 先等待一个基础等待时间（让老板处理完当前事务）
        2. 然后进入轮询模式，每3秒检查一次老板是否空闲
        3. 最多等待3分钟，超时自动离开

        Args:
            chat_id (str): 来访者ID
        """
        try:
            # 1. 设置轮询参数
            detention_timeout_seconds = 180  # 扣押超时时间：最多等待3分钟
            recheck_interval_seconds = 3  # 每3秒检查一次
            total_waited = 0

            # 2. 进入轮询等待模式
            while total_waited < detention_timeout_seconds:
                # 【新增】检查事件是否被撤回插件停止
                event = self.pending_events.get(chat_id)
                if event and hasattr(event, 'is_stopped') and event.is_stopped():
                    # 事件被撤回插件停止了！立即结束等待
                    ticket = self.pending_futures.get(chat_id)
                    if ticket and not ticket.done():
                        logger.info(
                            f"AngelHeart[{chat_id}]: 检测到事件被撤回，立即结束等待"
                        )
                        ticket.set_result("KILL")  # 叫号：离开

                    # 清理扣押记录
                    self._cleanup_detention_resources(chat_id)
                    return  # 等候被撤回结束

                # 【简化】检查老板是否已经空闲（门锁已包含冷却机制）
                if not await self.is_chat_processing(chat_id):
                    # 老板空闲！请来访者进来
                    ticket = self.pending_futures.get(chat_id)
                    if ticket and not ticket.done():
                        logger.info(
                            f"AngelHeart[{chat_id}]: 老板已空闲，请来访者进来"
                        )
                        ticket.set_result("PROCESS")  # 叫号：请进

                    # 清理扣押记录
                    self._cleanup_detention_resources(chat_id)
                    return  # 等候成功结束

                # 老板还在忙，继续等
                logger.debug(
                    f"AngelHeart[{chat_id}]: 等待中... (已等 {total_waited}秒/{detention_timeout_seconds}秒, 事件状态: {'已停止' if event and hasattr(event, 'is_stopped') and event.is_stopped() else '正常'})"
                )
                await asyncio.sleep(recheck_interval_seconds)
                total_waited += recheck_interval_seconds

            # 4. 超时处理：等太久了，请来访者离开
            logger.warning(
                f"AngelHeart[{chat_id}]: 等候超过{detention_timeout_seconds}秒，请来访者离开"
            )
            ticket = self.pending_futures.get(chat_id)
            if ticket and not ticket.done():
                ticket.set_result("KILL")  # 叫号：不好意思，今天不接待了

            # 清理扣押记录
            self._cleanup_detention_resources(chat_id)

        except asyncio.CancelledError:
            logger.debug(f"AngelHeart[{chat_id}]: 等候被取消（老板直接叫号了）")
            # 清理事件记录
            self._cleanup_detention_resources(chat_id)
        except Exception as e:
            logger.error(
                f"AngelHeart[{chat_id}]: 等候处理出错: {e}", exc_info=True
            )

    def _cleanup_detention_resources(self, chat_id: str):
        """
        清理单个会话的扣押相关资源

        Args:
            chat_id (str): 会话ID
        """
        # 清理扣押记录
        self.pending_futures.pop(chat_id, None)
        self.detention_timeout_timers.pop(chat_id, None)
        self.pending_events.pop(chat_id, None)

        # 清理门牌占用记录
        self.processing_chats.pop(chat_id, None)

        # 清理冷却期记录
        self.lock_cooldown_until.pop(chat_id, None)

        logger.debug(f"AngelHeart[{chat_id}]: 已清理该会话的所有扣押资源")

    # ========== V3: Patience Timer (Multi-Stage) ==========

    async def _patience_timer_handler(self, chat_id: str):
        """
        耐心安抚机制

        当老板需要较长时间思考时，定期告诉来访者"请稍等"，
        避免来访者以为被遗忘了而离开。

        Args:
            chat_id: 来访者ID
        """
        try:
            # 获取安抚语配置
            interval = self.config_manager.patience_interval
            comfort_words_raw = self.config_manager.comfort_words
            if not comfort_words_raw:
                logger.warning(f"AngelHeart[{chat_id}]: comfort_words 配置为空，跳过安抚")
                return
            comfort_words = comfort_words_raw.split('|')

            # 定期发送安抚语
            for i, word in enumerate(comfort_words):
                await asyncio.sleep(interval)
                logger.debug(f"AngelHeart[{chat_id}]: 安抚来访者 - 第{i+1}次 ({(i+1)*interval}s)")
                chain = MessageChain([Plain(word.strip())])
                await self.astr_context.send_message(chat_id, chain)
            logger.debug(f"AngelHeart[{chat_id}]: 安抚停止（老板已经有答案了）")
        except Exception as e:
            logger.error(
                f"AngelHeart[{chat_id}]: 安抚出错: {e}", exc_info=True
            )
    async def start_patience_timer(self, chat_id: str):
        """启动或重置指定来访者的安抚机制"""
        # 先停止之前的安抚
        await self.cancel_patience_timer(chat_id)

        # 开始新的安抚
        self.patience_timers[chat_id] = asyncio.create_task(
            self._patience_timer_handler(chat_id)
        )
        comfort_words = self.config_manager.comfort_words.split('|')
        logger.info(f"AngelHeart[{chat_id}]: 已启动安抚机制（{len(comfort_words)}次安抚，每隔{self.config_manager.patience_interval}秒一次）")

    async def cancel_patience_timer(self, chat_id: str):
        """停止指定来访者的安抚机制"""
        if chat_id in self.patience_timers:
            timer_task = self.patience_timers.pop(chat_id)
            if not timer_task.done():
                timer_task.cancel()
                logger.debug(f"AngelHeart[{chat_id}]: 已停止安抚（老板已经有答案了）")

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
        if self.analysis_cache.pop(chat_id, None) is not None:
            logger.debug(f"AngelHeart[{chat_id}]: 已从缓存中移除一次性决策。")

    # ========== 时序控制 ==========

    async def update_last_analysis_time(self, chat_id: str):
        """更新最后一次分析的时间戳"""
        self.last_analysis_time[chat_id] = time.time()
        logger.debug(f"AngelHeart[{chat_id}]: 已更新 last_analysis_time。")

    def get_last_analysis_time(self, chat_id: str) -> float:
        """获取最后一次分析的时间戳"""
        return self.last_analysis_time.get(chat_id, 0)


    # ========== 4状态机制状态管理方法 ==========

    def get_chat_status(self, chat_id: str) -> AngelHeartStatus:
        """
        获取当前聊天状态

        Args:
            chat_id: 聊天会话ID

        Returns:
            AngelHeartStatus: 当前状态，如果未设置则返回NOT_PRESENT
        """
        return self.current_states.get(chat_id, AngelHeartStatus.NOT_PRESENT)

    async def _update_chat_status(self, chat_id: str, new_status: AngelHeartStatus, reason: str = ""):
        """
        更新聊天状态（内部方法，仅更新状态值）

        注意：此方法仅更新状态值，不执行计时器管理等完整转换流程。
        如需完整的状态转换（包括计时器管理），请使用 transition_to_status 方法。

        Args:
            chat_id: 聊天会话ID
            new_status: 新状态
            reason: 状态转换原因
        """
        old_status = self.get_chat_status(chat_id)
        self.current_states[chat_id] = new_status

        if reason:
            logger.info(f"AngelHeart[{chat_id}]: 状态更新: {old_status.value} -> {new_status.value} ({reason})")
        else:
            logger.debug(f"AngelHeart[{chat_id}]: 状态更新: {old_status.value} -> {new_status.value}")

    async def transition_to_status(self, chat_id: str, new_status: AngelHeartStatus, reason: str = ""):
        """
        状态转换（完整转换流程，包括计时器管理）

        Args:
            chat_id: 聊天会话ID
            new_status: 新状态
            reason: 转换原因
        """
        await self.status_transition_manager.transition_to_status(chat_id, new_status, reason)

    def get_status_summary(self, chat_id: str) -> Dict:
        """
        获取状态摘要信息

        Args:
            chat_id: 聊天会话ID

        Returns:
            Dict: 包含当前状态、持续时间等信息
        """
        return self.status_transition_manager.get_status_summary(chat_id)

    async def handle_message_sent(self, chat_id: str):
        """
        消息发送后的状态处理

        当AI发送消息后，强制转换到观测中状态

        Args:
            chat_id: 聊天会话ID
        """
        current_status = self.get_chat_status(chat_id)

        # 如果是从混脸熟状态转换，设置冷却期
        if current_status == AngelHeartStatus.GETTING_FAMILIAR:
            logger.debug(f"AngelHeart[{chat_id}]: 从混脸熟状态转换，设置冷却期")
            self.set_familiarity_cooldown(chat_id)

        # 强制转换到观测中状态
        await self.transition_to_status(chat_id, AngelHeartStatus.OBSERVATION, "AI回复完成，进入观测中")



    def is_in_observation_period(self, chat_id: str) -> bool:
        """
        检查是否在观测中

        Args:
            chat_id: 聊天会话ID

        Returns:
            bool: True if in observation period
        """
        return (self.get_chat_status(chat_id) == AngelHeartStatus.OBSERVATION or
                chat_id in self.detention_timeout_timers)

    def is_not_present(self, chat_id: str) -> bool:
        """
        检查是否不在场

        Args:
            chat_id: 聊天会话ID

        Returns:
            bool: True if not present
        """
        return self.get_chat_status(chat_id) == AngelHeartStatus.NOT_PRESENT

    def is_familiarity_in_cooldown(self, chat_id: str) -> bool:
        """
        检查混脸熟是否在冷却期

        Args:
            chat_id: 聊天会话ID

        Returns:
            bool: True if in cooldown period
        """
        if chat_id not in self.familiarity_cooldown_until:
            return False

        current_time = time.time()
        cooldown_end = self.familiarity_cooldown_until[chat_id]

        # 如果冷却期已过，清理记录
        if current_time >= cooldown_end:
            del self.familiarity_cooldown_until[chat_id]
            return False

        return True

    def set_familiarity_cooldown(self, chat_id: str):
        """
        设置混脸熟冷却期

        Args:
            chat_id: 聊天会话ID
        """
        cooldown_duration = self.config_manager.familiarity_cooldown_duration
        self.familiarity_cooldown_until[chat_id] = time.time() + cooldown_duration
        logger.info(f"AngelHeart[{chat_id}]: 混脸熟进入冷却期，冷却时间 {cooldown_duration} 秒")
