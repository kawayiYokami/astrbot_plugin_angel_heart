"""
AngelHeart 插件 - 全局上下文管理器
集中管理所有共享状态，解决循环依赖和状态分散问题。
"""

import time
import asyncio
from typing import Dict, Optional, Any
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
from ..core.debounce_manager import DebounceManager


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
            config_manager=config_manager,
            data_dir=data_dir,
            astr_context=astr_context
        )

        # 门牌管理（兼容保留；群聊主路径已不再依赖单槽门锁做消息收集）
        self.processing_chats: Dict[str, tuple[float, Any]] = {}  # chat_id -> (开始分析时间, event对象)
        self.processing_lock: asyncio.Lock = asyncio.Lock()  # 门牌操作锁
        # 门锁冷却时间：归还门锁后需要等待的时间
        self.lock_cooldown_until: Dict[str, float] = {}  # chat_id -> 冷却结束时间

        # 耐心计时器：主脑思考时，定期发送安抚消息
        self.patience_timers: Dict[str, asyncio.Task] = {}

        # 时序控制
        self.last_analysis_time: Dict[str, float] = {}  # chat_id -> 上次分析时间
        self.silenced_until: Dict[str, float] = {}  # chat_id -> 闭嘴结束时间

        # 混脸熟冷却控制（兼容保留；混脸熟不再作为进场条件）
        self.familiarity_cooldown_until: Dict[str, float] = {}  # chat_id -> 混脸熟冷却结束时间

        # 决策缓存
        self.analysis_cache: OrderedDict[str, SecretaryDecision] = OrderedDict()
        self.CACHE_MAX_SIZE = 100  # 缓存最大尺寸

        # ========== 群聊参与状态 ==========
        # 当前状态跟踪：chat_id -> AngelHeartStatus
        # 现行语义：NOT_PRESENT=离场，OBSERVATION=在场
        self.current_states: Dict[str, AngelHeartStatus] = {}

        # 状态转换管理器
        self.status_transition_manager = StatusTransitionManager(self)

        # 群聊双防抖（目的）/ 扣押（实现）
        self.debounce_manager = DebounceManager(config_manager)

        # 整理开始时关闭防抖
        def _close_debounce_on_organize(chat_id: str):
            try:
                # 同步调度清理；debounce clear 是 async
                import asyncio

                async def _clear():
                    await self.debounce_manager.clear_chat(chat_id, reason="context_organize")

                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(_clear())
                except RuntimeError:
                    # 无事件循环时尽力同步跑
                    try:
                        asyncio.run(_clear())
                    except Exception:
                        pass
            except Exception as e:
                logger.warning(f"AngelHeart[{chat_id}]: 整理时关闭防抖失败: {e}")

        self.conversation_ledger.on_before_organize = _close_debounce_on_organize

        # 主动应答管理器
        self.proactive_manager = ProactiveManager(self)

    def _get_processing_stale_threshold(self) -> float:
        """
        获取会话处理僵尸占用阈值（秒）。

        设计目标：使用独立的 LLM 超时配置，最大不超过 300 秒。
        """
        llm_timeout = max(0.0, float(self.config_manager.llm_timeout))
        return min(llm_timeout, 300.0)

    def _get_plain_chat_id(self, chat_id: str) -> str:
        """从 unified_msg_origin 中提取纯净的聊天 ID。"""
        parts = chat_id.split(":")
        return parts[-1] if parts else ""

    def _is_patience_timer_allowed(self, chat_id: str) -> bool:
        """检查安抚机制是否允许在当前会话生效。"""
        if not self.config_manager.whitelist_enabled:
            return True

        plain_chat_id = self._get_plain_chat_id(chat_id)
        whitelist = {str(cid) for cid in self.config_manager.chat_ids}
        return plain_chat_id in whitelist

    # ========== 门牌管理 ==========

    async def is_chat_processing(self, chat_id: str) -> bool:
        """
        检查该会话是否正在被处理（v3: 包含冷却期检查与事件存活检测）。
        只有当既不在处理中，也不在冷却期时，才返回 False（表示空闲）。

        Args:
            chat_id (str): 会话ID。

        Returns:
            bool: 如果正忙（处理中或冷却中）返回 True，完全空闲返回 False。
        """
        async with self.processing_lock:
            current_time = time.time()

            # 1. 检查冷却期 (冷却期也视为正忙)
            cooldown_end = self.lock_cooldown_until.get(chat_id, 0)
            if current_time < cooldown_end:
                return True

            # 2. 检查实际处理情况
            if chat_id not in self.processing_chats:
                return False

            start_time, occupant_event = self.processing_chats[chat_id]

            # 3. 实时探活：检查占用者事件是否已停止
            if occupant_event and hasattr(occupant_event, 'is_stopped') and occupant_event.is_stopped():
                # 事件停止，立即转入冷却期
                cooldown_duration = self.config_manager.waiting_time
                self.lock_cooldown_until[chat_id] = current_time + cooldown_duration
                logger.info(f"AngelHeart[{chat_id}]: 检测到占用门牌的事件已停止，清理并进入 {cooldown_duration} 秒冷却期。")
                self.processing_chats.pop(chat_id, None)
                return True # 现在转为冷却了，依然算“正忙”

            # 4. 硬超时：检查是否卡死（超过 min(waiting_time, 300) 秒）
            stale_threshold = self._get_processing_stale_threshold()
            if current_time - start_time > stale_threshold:
                # 卡死清理也强制进入冷却，保证节奏
                cooldown_duration = self.config_manager.waiting_time
                self.lock_cooldown_until[chat_id] = current_time + cooldown_duration
                logger.warning(
                    f"AngelHeart[{chat_id}]: 检测到卡死的门牌 (超过{stale_threshold:.1f}秒)，自动清理并进入冷却。"
                )
                self.processing_chats.pop(chat_id, None)
                return True

            return True

    async def acquire_chat_processing(self, chat_id: str, event: Any) -> tuple[bool, str, float]:
        """
        原子性地尝试获取会话处理权（挂上门牌）。
        包含冷却机制和占用者存活检测。

        Args:
            chat_id (str): 会话ID。
            event (Any): 当前尝试获取锁的事件对象。

        Returns:
            tuple[bool, str, float]: (是否成功, 失败原因, 剩余时间)
                - 成功时返回 (True, "SUCCESS", 0.0)
                - 冷却期失败时返回 (False, "COOLDOWN", 剩余秒数)
                - 被占用失败时返回 (False, "LOCKED", 0.0)
        """
        async with self.processing_lock:
            current_time = time.time()

            # 1. 检查冷却期
            cooldown_end = self.lock_cooldown_until.get(chat_id, 0)
            if current_time < cooldown_end:
                remaining = cooldown_end - current_time
                logger.debug(f"AngelHeart[{chat_id}]: 门锁在冷却期，剩余 {remaining:.1f} 秒")
                return False, "COOLDOWN", remaining

            # 自动清理过期的冷却记录
            if chat_id in self.lock_cooldown_until and current_time >= cooldown_end:
                del self.lock_cooldown_until[chat_id]

            # 2. 检查门牌占用情况
            if chat_id in self.processing_chats:
                start_time, occupant_event = self.processing_chats[chat_id]

                # 2.1. 实时探活：检查占用者事件是否已停止
                if occupant_event and hasattr(occupant_event, 'is_stopped') and occupant_event.is_stopped():
                    # 前任死了，但我们要等它“断气”完（进入冷却期）
                    cooldown_duration = self.config_manager.waiting_time
                    self.lock_cooldown_until[chat_id] = current_time + cooldown_duration
                    logger.info(f"AngelHeart[{chat_id}]: 检测到占用门牌的事件已停止，清理并进入 {cooldown_duration} 秒冷却。")
                    self.processing_chats.pop(chat_id, None)
                    return False, "COOLDOWN", cooldown_duration

                # 2.2. 硬超时：检查是否卡死（超过 min(waiting_time, 300) 秒）
                stale_threshold = self._get_processing_stale_threshold()
                if current_time - start_time > stale_threshold:
                    cooldown_duration = self.config_manager.waiting_time
                    self.lock_cooldown_until[chat_id] = current_time + cooldown_duration
                    logger.warning(
                        f"AngelHeart[{chat_id}]: 检测到会话处理卡死(>{stale_threshold:.1f}s)，强制进入冷却清理。"
                    )
                    self.processing_chats.pop(chat_id, None)
                    return False, "COOLDOWN", cooldown_duration

                # 2.3. 门牌正被活跃事件占用
                logger.debug(f"AngelHeart[{chat_id}]: 门牌已被活跃事件占用 (开始时间: {start_time})")
                return False, "LOCKED", 0.0

            # 3. 如果门牌不存在，则挂上新门牌
            self.processing_chats[chat_id] = (current_time, event)
            logger.debug(f"AngelHeart[{chat_id}]: 已挂上门牌 (开始处理时间: {current_time}, 事件: {id(event)})")
            return True, "SUCCESS", 0.0

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

    # ========== Patience Timer ==========

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
                if not self._is_patience_timer_allowed(chat_id):
                    logger.debug(f"AngelHeart[{chat_id}]: 安抚白名单条件不满足，停止发送后续安抚语")
                    return
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

        if not self._is_patience_timer_allowed(chat_id):
            logger.debug(f"AngelHeart[{chat_id}]: 当前会话不满足安抚白名单条件，跳过安抚启动")
            return

        # 开始新的安抚
        self.patience_timers[chat_id] = asyncio.create_task(
            self._patience_timer_handler(chat_id)
        )
        comfort_words_raw = self.config_manager.comfort_words
        if not comfort_words_raw:
            logger.warning(f"AngelHeart[{chat_id}]: comfort_words 配置为空，跳过安抚启动")
            return
        comfort_words = comfort_words_raw.split('|')
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
        消息发送后的状态处理。

        AI 回复完成后进入在场；混脸熟不再作为进场/保持条件。
        """
        current_status = self.get_chat_status(chat_id)
        logger.info(
            f"AngelHeart[{chat_id}]: AI回复完成，当前状态: {current_status.value}，转入在场"
        )
        await self.transition_to_status(
            chat_id, AngelHeartStatus.OBSERVATION, "AI回复完成，进入在场"
        )

    def is_in_observation_period(self, chat_id: str) -> bool:
        """
        检查是否在场。

        现行语义：
        - OBSERVATION = 在场
        - SUMMONED 兼容为在场过渡态
        """
        status = self.get_chat_status(chat_id)
        return status in (AngelHeartStatus.OBSERVATION, AngelHeartStatus.SUMMONED)

    def is_present(self, chat_id: str) -> bool:
        """群聊是否在场。"""
        return self.is_in_observation_period(chat_id)

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
