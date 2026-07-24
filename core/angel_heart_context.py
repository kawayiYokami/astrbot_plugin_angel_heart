"""
AngelHeart 插件 - 全局上下文管理器
集中管理所有共享状态，解决循环依赖和状态分散问题。
"""

import time
import asyncio
from typing import Dict, Optional, Any

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from astrbot.core.star.context import Context
from ..core.conversation_ledger import ConversationLedger
from ..core.angel_heart_status import AngelHeartStatus, StatusTransitionManager
from ..core.proactive_manager import ProactiveManager
from ..core.debounce_manager import DebounceManager
from ..core.work_ledger import WorkLedger


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

        # 调度：群聊双防抖；旧单槽门锁已退役
        # （processing_chats / acquire_chat_processing 已删除）

        # 时序控制
        self.last_analysis_time: Dict[str, float] = {}  # chat_id -> 上次分析时间
        self.silenced_until: Dict[str, float] = {}  # chat_id -> 闭嘴结束时间

        # 离场应答冷却：一次性回复后，短时间内不再因复读或密集聊天触发回复。
        self.leave_reply_cooldown_until: Dict[str, float] = {}

        # ========== 群聊参与状态 ==========
        # 当前状态跟踪：chat_id -> AngelHeartStatus
        # 现行语义：NOT_PRESENT=离场，OBSERVATION=在场
        self.current_states: Dict[str, AngelHeartStatus] = {}

        # 状态转换管理器
        self.status_transition_manager = StatusTransitionManager(self)

        # 群聊调度：助理防抖与巡检（旧单槽门锁已退役）
        self.debounce_manager = DebounceManager(config_manager)
        self.energy_states = self.debounce_manager.energy_states

        # 助理工作账本：正在/已经处理哪一套活
        self.work_ledger = WorkLedger()

        # 主动应答管理器
        self.proactive_manager = ProactiveManager(self)

    async def cleanup(self) -> None:
        """清理全局运行态：后台任务、调度账本、状态内存与持久连接。"""
        try:
            await self.proactive_manager.cleanup()
        except Exception as e:
            logger.error(f"AngelHeart: 清理主动应答任务失败: {e}", exc_info=True)
        try:
            await self.debounce_manager.cleanup()
        except Exception as e:
            logger.error(f"AngelHeart: 清理双防抖任务失败: {e}", exc_info=True)

        self.last_analysis_time.clear()
        self.silenced_until.clear()
        self.leave_reply_cooldown_until.clear()
        self.current_states.clear()
        self.status_transition_manager.status_start_times.clear()
        self.work_ledger.clear()
        self.conversation_ledger.close()

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

    async def handle_message_sent(self, chat_id: str, *, keep_not_present: bool = False):
        """处理消息发送后的群聊参与状态。"""
        current_status = self.get_chat_status(chat_id)
        if keep_not_present:
            self.start_leave_reply_cooldown(chat_id)
            logger.info(
                f"AngelHeart[{chat_id}]: 离场应答已发送，保持离场"
            )
            return

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

    def is_leave_reply_in_cooldown(self, chat_id: str) -> bool:
        """离场应答是否仍在冷却中。"""
        cooldown_end = self.leave_reply_cooldown_until.get(chat_id, 0.0)
        if time.time() >= cooldown_end:
            self.leave_reply_cooldown_until.pop(chat_id, None)
            return False
        return True

    def start_leave_reply_cooldown(self, chat_id: str) -> None:
        """离场应答成功发送后，启动下一次离场应答的冷却。"""
        cooldown_duration = self.config_manager.leave_reply_cooldown_duration
        self.leave_reply_cooldown_until[chat_id] = time.time() + cooldown_duration
        logger.info(
            f"AngelHeart[{chat_id}]: 离场应答进入冷却期，冷却时间 {cooldown_duration} 秒"
        )
