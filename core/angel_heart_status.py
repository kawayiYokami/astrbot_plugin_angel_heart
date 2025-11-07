"""AngelHeart 插件 - 状态系统核心模块"""

import time
import asyncio
from enum import Enum
from typing import Dict, Optional, Tuple
from collections import defaultdict

try:
    from astrbot.api import logger
except ImportError:
    import logging

    logger = logging.getLogger(__name__)


class AngelHeartStatus(Enum):
    """AngelHeart 4状态枚举"""

    NOT_PRESENT = "不在场"  # 初始状态，无消息缓存
    SUMMONED = "被呼唤"  # 检测到唤醒词或@消息
    GETTING_FAMILIAR = "混脸熟"  # 连续互动阶段，可直接回复
    OBSERVATION = "观测中"  # 观测中等待，10分钟自动降级


class StatusChecker:
    """前台状态判断模块

    负责基于消息内容和上下文判断当前应该处于什么状态。
    状态判断优先级：被呼唤 > 观测中 > 混脸熟 > 不在场

    注意：修复了竞态条件问题，确保状态判断和转换的原子性
    """

    def __init__(self, config_manager, angel_context):
        """
        初始化状态判断器

        Args:
            config_manager: 配置管理器
            angel_context: AngelHeart全局上下文
        """
        self.config_manager = config_manager
        self.angel_context = angel_context

        # 状态判断缓存：避免重复判断（缩短缓存时间到3秒）
        # 值格式：(status, timestamp, trigger_type)
        self._last_judgment_cache: Dict[
            str, Tuple[AngelHeartStatus, float, Optional[str]]
        ] = {}

    async def determine_status(self, chat_id: str) -> AngelHeartStatus:
        """
        智能状态判断 - 基于多维度信息综合判断

        由于前台通过门牌机制保证了串行处理，不需要额外的锁保护。

        Args:
            chat_id: 聊天会话ID

        Returns:
            AngelHeartStatus: 判断得出的状态
        """
        current_time = time.time()

        # 获取最新消息用于缓存键
        latest_message = self._get_latest_message(chat_id)
        if not latest_message:
            # 没有消息，返回不在场
            return AngelHeartStatus.NOT_PRESENT

        # 检查缓存（使用配置管理）
        cache_duration = self.config_manager.status_judgment_cache_duration
        message_content = self._extract_message_content(latest_message)
        cache_key = f"{chat_id}:{hash(message_content)}"
        if cache_key in self._last_judgment_cache:
            cached_status, cached_time, cached_trigger = self._last_judgment_cache[
                cache_key
            ]
            if current_time - cached_time < cache_duration:
                return cached_status

        try:
            # 1. 检查是否处于闭嘴状态（最高优先级）
            if self._is_silenced(chat_id):
                result = AngelHeartStatus.NOT_PRESENT
                self._cache_judgment(cache_key, result, current_time, None)
                return result

            # 3. 检查是否检测到唤醒词（最高优先级）
            if self._detect_wake_word(chat_id, message_content):
                result = AngelHeartStatus.SUMMONED
                self._cache_judgment(cache_key, result, current_time, "wake_word")
                return result

            # 4. 获取当前状态（原子性读取）
            current_status = self.angel_context.get_chat_status(chat_id)

            # 5. 如果当前是观测中，保持观测中
            if current_status == AngelHeartStatus.OBSERVATION:
                result = AngelHeartStatus.OBSERVATION
                self._cache_judgment(cache_key, result, current_time, "observation")
                return result

            # 6. 如果当前是混脸熟，检查是否刷新或继续
            if current_status == AngelHeartStatus.GETTING_FAMILIAR:
                if self._should_continue_familiarity(chat_id):
                    result = AngelHeartStatus.GETTING_FAMILIAR
                    self._cache_judgment(
                        cache_key, result, current_time, "continuation"
                    )
                    return result
                else:
                    # 混脸熟状态结束，转为不在场
                    result = AngelHeartStatus.NOT_PRESENT
                    self._cache_judgment(cache_key, result, current_time, None)
                    return result

            # 7. 检查混脸熟触发条件（只有在不在场时才能触发）
            if current_status == AngelHeartStatus.NOT_PRESENT:
                # 7.1 检查复读行为
                if self._detect_echo_chamber(chat_id):
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 检测到复读行为，进入混脸熟状态"
                    )
                    result = AngelHeartStatus.GETTING_FAMILIAR
                    self._cache_judgment(
                        cache_key, result, current_time, "echo_chamber"
                    )
                    return result

                # 7.2 检查密集发言
                if self._detect_dense_conversation(chat_id):
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 检测到密集发言，进入混脸熟状态"
                    )
                    result = AngelHeartStatus.GETTING_FAMILIAR
                    self._cache_judgment(
                        cache_key, result, current_time, "dense_conversation"
                    )
                    return result

            # 8. 默认状态：不在场
            result = AngelHeartStatus.NOT_PRESENT
            self._cache_judgment(cache_key, result, current_time, None)
            return result

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 状态判断异常: {e}", exc_info=True)
            # 出错时返回安全状态
            return AngelHeartStatus.NOT_PRESENT

    def _get_latest_message(self, chat_id: str) -> Optional[Dict]:
        """获取最新消息"""
        try:
            ledger = self.angel_context.conversation_ledger
            all_messages = ledger.get_all_messages(chat_id)
            if not all_messages:
                return None
            # 返回时间戳最大的消息
            return max(all_messages, key=lambda m: m.get("timestamp", 0))
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 获取最新消息失败: {e}")
            return None

    def _extract_message_content(self, message: Dict) -> str:
        """提取消息内容"""
        if not message:
            return ""
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            # 处理多模态内容
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            return "".join(text_parts)
        return str(content)

    def _is_silenced(self, chat_id: str) -> bool:
        """检查是否处于闭嘴状态"""
        current_time = time.time()
        silenced_until = self.angel_context.silenced_until.get(chat_id, 0)
        return current_time < silenced_until

    def _detect_wake_word(self, chat_id: str, message_content: str) -> bool:
        """检测唤醒词或@消息"""
        # 检查是否启用呼唤模式
        if not self.config_manager.analysis_on_mention_only:
            return False

        # 获取昵称列表
        alias_str = self.config_manager.alias
        if not alias_str:
            return False

        aliases = [name.strip() for name in alias_str.split("|") if name.strip()]
        if not aliases:
            return False

        # 检查消息中是否包含昵称
        return any(alias in message_content for alias in aliases)

    def _has_unprocessed_messages(self, chat_id: str) -> bool:
        """检查是否有未处理消息"""
        try:
            ledger = self.angel_context.conversation_ledger
            all_messages = ledger.get_all_messages(chat_id)
            return any(not msg.get("is_processed", False) for msg in all_messages)
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 检查未处理消息失败: {e}")
            return False

    def _detect_echo_chamber(self, chat_id: str) -> bool:
        """
        检测复读行为 - 统计窗口内相同内容的纯文字消息数量

        Returns:
            bool: True if echo chamber detected
        """
        try:
            # 直接从 ConversationLedger 获取最近消息
            all_messages = self.angel_context.conversation_ledger.get_all_messages(
                chat_id
            )
            if len(all_messages) < 3:
                return False

            # 统计窗口内每个纯文字内容的出现次数
            threshold = self.config_manager.echo_detection_threshold
            content_count = {}  # content -> count
            window = self.config_manager.echo_detection_window
            cutoff_time = time.time() - window

            for msg in all_messages:
                if msg.get("role") != "user":
                    continue

                # 检查时间窗口
                if msg.get("timestamp", 0) < cutoff_time:
                    continue

                # 先检查是否为纯文字消息（不包含图片）
                content = msg.get("content", "")
                if isinstance(content, list):
                    # 检查是否包含图片
                    has_image = any(item.get("type") == "image_url" for item in content if isinstance(item, dict))
                    if has_image:
                        continue  # 跳过包含图片的消息

                    # 提取纯文字内容
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                    content = "".join(text_parts)

                content = str(content).strip()

                if not content:
                    continue

                # 统计内容出现次数
                if content not in content_count:
                    content_count[content] = 0
                content_count[content] += 1

            # 检查是否有内容出现次数达到阈值
            for content, count in content_count.items():
                if count >= threshold:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 检测到复读行为 - 内容: '{content}', 出现次数: {count}"
                    )
                    return True

            return False

        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 复读检测失败: {e}")
            return False



    def _detect_dense_conversation(self, chat_id: str) -> bool:
        """
        检测密集发言 - 在时间窗口内消息数量和参与人数都达到阈值

        Returns:
            bool: True if dense conversation detected
        """
        try:
            # 直接从 ConversationLedger 获取消息
            all_messages = self.angel_context.conversation_ledger.get_all_messages(
                chat_id
            )

            # 获取配置参数
            window = self.config_manager.dense_conversation_window
            cutoff_time = time.time() - window
            message_threshold = self.config_manager.dense_conversation_threshold
            participant_threshold = self.config_manager.min_participant_count

            # 统计时间窗口内的消息
            message_count = 0
            participant_set = set()

            for msg in all_messages:
                if msg.get("timestamp", 0) > cutoff_time:
                    message_count += 1
                    participant_set.add(msg.get("sender_id", ""))

            # 早期退出优化
            if message_count < message_threshold:
                return False

            participant_count = len(participant_set)
            is_dense = participant_count >= participant_threshold

            if is_dense:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 密集发言检测 - "
                    f"消息数: {message_count}/{message_threshold}, "
                    f"参与人数: {participant_count}/{participant_threshold}"
                )

            return is_dense

        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 密集发言检测失败: {e}")
            return False

    def _should_continue_familiarity(self, chat_id: str) -> bool:
        """
        判断是否应该继续混脸熟状态（使用配置管理）

        Returns:
            bool: True if should continue familiarity state
        """
        try:
            last_time = self.angel_context.get_last_analysis_time(chat_id)
            if last_time == 0:
                return False

            # 使用配置的混脸熟超时时间
            familiarity_duration = self.config_manager.familiarity_timeout
            return (time.time() - last_time) < familiarity_duration

        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 混脸熟状态判断失败: {e}")
            return False

    def _cache_judgment(
        self,
        cache_key: str,
        status: AngelHeartStatus,
        current_time: float,
        trigger_type: Optional[str],
    ):
        """缓存状态判断结果及触发类型"""
        self._last_judgment_cache[cache_key] = (status, current_time, trigger_type)

        # 清理过期缓存（超过30秒）
        cutoff_time = current_time - 30
        expired_keys = [
            k
            for k, (_, cached_time, _) in self._last_judgment_cache.items()
            if cached_time < cutoff_time
        ]
        for key in expired_keys:
            self._last_judgment_cache.pop(key, None)

    def get_last_trigger_type(self, chat_id: str) -> Optional[str]:
        """
        获取最近一次状态判断的触发类型

        Args:
            chat_id: 聊天会话ID

        Returns:
            Optional[str]: 触发类型（"echo_chamber", "dense_conversation", "new_message", "wake_word", "continuation", "observation"）
                         如果无缓存或无触发类型则返回None
        """
        try:
            latest_message = self._get_latest_message(chat_id)
            if not latest_message:
                return None

            message_content = self._extract_message_content(latest_message)
            cache_key = f"{chat_id}:{hash(message_content)}"
            if cache_key in self._last_judgment_cache:
                _, _, trigger_type = self._last_judgment_cache[cache_key]
                return trigger_type
            return None
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 获取触发类型失败: {e}")
            return None


class StatusTransitionManager:
    """状态转换管理器

    负责管理状态的转换、计时器启动和清理。
    """

    def __init__(self, angel_context):
        """
        初始化状态转换管理器

        Args:
            angel_context: AngelHeart全局上下文
        """
        self.angel_context = angel_context

        # 状态持续时间跟踪：chat_id -> (status, start_time)
        self.status_start_times: Dict[str, Tuple[AngelHeartStatus, float]] = {}

        # 自动降级计时器：chat_id -> asyncio.Task
        self.degradation_timers: Dict[str, asyncio.Task] = {}

    async def cancel_degradation_timer(self, chat_id: str):
        """取消指定会话的降级计时器"""
        if chat_id in self.degradation_timers:
            timer = self.degradation_timers.pop(chat_id)
            if not timer.done():
                timer.cancel()
                logger.debug(f"AngelHeart[{chat_id}]: 已取消降级计时器")

    async def transition_to_status(
        self, chat_id: str, new_status: AngelHeartStatus, reason: str = ""
    ):
        """
        状态转换

        Args:
            chat_id: 聊天会话ID
            new_status: 新状态
            reason: 转换原因
        """
        try:
            old_status = self.angel_context.get_chat_status(chat_id)

            # 取消旧的计时器
            if old_status == AngelHeartStatus.OBSERVATION:
                # 取消观察期计时器
                if chat_id in self.angel_context.observation_timers:
                    timer = self.angel_context.observation_timers.pop(chat_id)
                    if not timer.done():
                        timer.cancel()
                    logger.debug(f"AngelHeart[{chat_id}]: 已取消观察期计时器")

            # 记录状态开始时间
            self.status_start_times[chat_id] = (new_status, time.time())

            # 启动新计时器
            await self._start_new_timer(chat_id, new_status)

            # 记录转换
            logger.info(
                f"AngelHeart[{chat_id}]: 状态转换: {old_status.value if old_status else 'None'} -> {new_status.value} ({reason})"
            )

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 状态转换失败: {e}")



    async def _start_new_timer(self, chat_id: str, new_status: AngelHeartStatus):
        """启动新状态的计时器"""
        try:
            # 从配置获取观测中超时时间
            observation_timeout = self.angel_context.config_manager.observation_timeout

            if new_status == AngelHeartStatus.OBSERVATION:
                # 观测中启动自动降级计时器（超时时间由配置决定）
                self.degradation_timers[chat_id] = asyncio.create_task(
                    self._degradation_timer_handler(chat_id)
                )
                logger.debug(
                    f"AngelHeart[{chat_id}]: 已启动{observation_timeout}秒自动降级计时器"
                )

            elif new_status == AngelHeartStatus.GETTING_FAMILIAR:
                # 混脸熟状态不启动单独计时器，通过消息刷新
                logger.debug(f"AngelHeart[{chat_id}]: 混脸熟状态，不启动计时器")

        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 启动新计时器失败: {e}")

    async def _degradation_timer_handler(self, chat_id: str):
        """自动降级计时器处理器"""
        try:
            # 从配置获取观测中超时时间
            observation_timeout = self.angel_context.config_manager.observation_timeout

            # 等待配置的超时时间
            await asyncio.sleep(observation_timeout)

            # 检查是否仍然在观测中
            current_status = self.angel_context.get_chat_status(chat_id)
            if current_status != AngelHeartStatus.OBSERVATION:
                logger.debug(f"AngelHeart[{chat_id}]: 状态已变化，取消降级")
                return

            # 检查最后分析时间
            last_time = self.angel_context.get_last_analysis_time(chat_id)
            if last_time == 0 or (time.time() - last_time) > observation_timeout:
                logger.info(
                    f"AngelHeart[{chat_id}]: {observation_timeout}秒无活动，自动降级为'不在场'"
                )
                await self.transition_to_status(
                    chat_id,
                    AngelHeartStatus.NOT_PRESENT,
                    f"{observation_timeout}秒无活动自动降级",
                )

                # 清理所有未处理消息
                try:
                    self.angel_context.conversation_ledger.mark_as_processed(
                        chat_id, time.time()
                    )
                except Exception as e:
                    logger.warning(f"AngelHeart[{chat_id}]: 清理未处理消息失败: {e}")

        except asyncio.CancelledError:
            logger.debug(f"AngelHeart[{chat_id}]: 自动降级计时器被取消")
        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 自动降级计时器异常: {e}")

    def get_status_duration(self, chat_id: str) -> float:
        """
        获取当前状态的持续时间（秒）

        Args:
            chat_id: 聊天会话ID

        Returns:
            float: 持续时间，秒
        """
        try:
            if chat_id not in self.status_start_times:
                return 0.0

            status, start_time = self.status_start_times[chat_id]
            return time.time() - start_time
        except Exception:
            return 0.0

    def get_status_summary(self, chat_id: str) -> Dict:
        """
        获取状态摘要

        Args:
            chat_id: 聊天会话ID

        Returns:
            Dict: 状态摘要信息
        """
        try:
            status = self.angel_context.get_chat_status(chat_id)
            duration = self.get_status_duration(chat_id)

            return {
                "current_status": status.value if status else "Unknown",
                "duration_seconds": round(duration, 2),
                "duration_minutes": round(duration / 60, 2),
                "has_timer": chat_id in self.degradation_timers,
            }
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 获取状态摘要失败: {e}")
            return {"current_status": "Error", "duration_seconds": 0}
