"""AngelHeart 插件 - 状态系统核心模块"""

import time
from enum import Enum
from typing import Dict, Optional, Tuple

try:
    from astrbot.api import logger
except ImportError:
    import logging

    logger = logging.getLogger(__name__)

from .utils.message_hits import (
    extract_plain_body_from_components,
    metadata_has_hit,
    parse_pipe_phrases,
)


class AngelHeartStatus(Enum):
    """AngelHeart 群聊参与状态。

    现行语义只保留两态：
    - NOT_PRESENT：离场
    - OBSERVATION：在场（回复后进入，超时回离场）

    SUMMONED / GETTING_FAMILIAR 仅兼容旧代码路径，不再作为进场条件。
    """

    NOT_PRESENT = "离场"
    SUMMONED = "被呼唤"  # 兼容旧路径，不再作为主状态机进场条件
    GETTING_FAMILIAR = "混脸熟"  # 旧数据兼容值，不参与现行状态机
    OBSERVATION = "在场"


class StatusChecker:
    """前台状态判断模块

    负责基于消息内容和上下文判断当前应该处于什么状态。
    状态判断优先级：被呼唤 > 观测中 > 旧兼容状态 > 不在场

    注意：修复了竞态条件问题，确保状态判断和转换的原子性
    """

    def __init__(self, config_manager, angel_context):
        """
        初始化状态检查器

        Args:
            config_manager: 配置管理器实例
            angel_context: AngelHeart上下文实例
        """
        self.config_manager = config_manager
        self.angel_context = angel_context

    async def determine_status(self, chat_id: str) -> AngelHeartStatus:
        """
        智能状态判断 - 基于多维度信息综合判断

        由于前台通过门牌机制保证了串行处理，不需要额外的锁保护。

        Args:
            chat_id: 聊天会话ID

        Returns:
            AngelHeartStatus: 判断得出的状态
        """
        try:
            # 获取最新消息
            latest_message = self._get_latest_message(chat_id)
            if not latest_message:
                # 没有消息，返回不在场
                return AngelHeartStatus.NOT_PRESENT

            # 1. 检查是否处于闭嘴状态（最高优先级）
            if self._is_silenced(chat_id):
                return AngelHeartStatus.NOT_PRESENT

            # 2. 优先检查是否被呼唤
            if self._is_summoned(chat_id):
                return AngelHeartStatus.SUMMONED

            # 4. 检查是否在观测期
            if self.angel_context.is_in_observation_period(chat_id):
                return AngelHeartStatus.OBSERVATION

            # 5. 获取当前状态（原子性读取）
            current_status = self.angel_context.get_chat_status(chat_id)

            # 旧兼容状态不属于现行状态机，遇到时直接转为不在场
            if current_status == AngelHeartStatus.GETTING_FAMILIAR:
                logger.warning(
                    f"AngelHeart[{chat_id}]: 旧兼容状态出现在状态判断中，直接转为不在场"
                )
                return AngelHeartStatus.NOT_PRESENT

            # 旧兼容状态不再作为进场条件：离场时只有主动呼唤才可进场
            return AngelHeartStatus.NOT_PRESENT

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

    def _get_latest_user_message(self, chat_id: str) -> Optional[Dict]:
        """获取最新的用户消息（过滤 assistant/tool/system）"""
        try:
            ledger = self.angel_context.conversation_ledger
            all_messages = ledger.get_all_messages(chat_id)
            if not all_messages:
                return None

            user_messages = [m for m in all_messages if m.get("role") == "user"]
            if not user_messages:
                return None

            return max(user_messages, key=lambda m: m.get("timestamp", 0))
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 获取最新用户消息失败: {e}")
            return None

    def _has_at_self_since_last_reply(self, chat_id: str) -> bool:
        """扫描"上次 AI 回复之后"的所有 user 消息，判断是否有任何一条@了自己。

        门锁冷却期间多消息排队时，秘书真正处理的事件未必对应 ledger 中最新一条 user 消息，
        因此不能只看最新一条；只要本轮对话（上次 AI 回复之后）出现过@自己的消息，就视为被呼唤。
        """
        try:
            ledger = self.angel_context.conversation_ledger
            all_messages = ledger.get_all_messages(chat_id)
            if not all_messages:
                return False

            # 找上次 AI 回复的时间戳作为下界
            last_reply_ts = 0.0
            for m in all_messages:
                if m.get("role") == "assistant":
                    ts = m.get("timestamp", 0) or 0
                    if ts > last_reply_ts:
                        last_reply_ts = ts

            # 扫描下界之后的所有 user 消息
            for m in all_messages:
                if m.get("role") != "user":
                    continue
                if (m.get("timestamp", 0) or 0) <= last_reply_ts:
                    continue
                if m.get("is_at_self", False):
                    return True
            return False
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 扫描@自己消息失败: {e}")
            return False

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

    def _is_summoned(self, chat_id: str) -> bool:
        """检查是否被点名。

        判定规则：
        - @自己：扫描"上次 AI 回复之后"的所有 user 消息，任意一条 is_at_self/metadata 命中即视为点名。
        - 昵称点名：只看最新一条 user 消息的入库命中结果；不再扫 outline / 引用 / 昵称展示。
        """
        try:
            # 检查是否处于闭嘴状态
            if self._is_silenced(chat_id):
                return False

            # 规则1：扫描上次 AI 回复之后的所有 user 消息，看是否有任何一条@了自己
            if self._has_at_self_since_last_reply(chat_id):
                return True

            # 规则2：基于最新 user 消息的入库命中
            latest_user_message = self._get_latest_user_message(chat_id)
            if not latest_user_message:
                return False
            return self._message_has_alias_hit(latest_user_message, chat_id)
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 检查被点名状态失败: {e}")
            return False

    def _message_has_alias_hit(self, message: Dict, chat_id: str) -> bool:
        """读取消息 metadata 中的 alias 命中；旧消息回退到 body_text。"""
        if not isinstance(message, dict):
            return False
        if not self._alias_detection_enabled(chat_id):
            return False
        metadata = message.get("metadata")
        if isinstance(metadata, dict) and "hits" in metadata:
            return metadata_has_hit(metadata, "alias")
        body_text = ""
        if isinstance(metadata, dict):
            body_text = str(metadata.get("body_text", "") or "")
        if not body_text:
            body_text = self._extract_message_content(message)
        return self._detect_wake_word(body_text, chat_id)

    def is_event_wake(self, event) -> bool:
        """判断当前事件本身是否为点名。

        双防抖调度必须基于当前事件，不能回扫历史@消息。
        优先读入库时写好的 metadata；没有则只对当前事件 Plain 正文判定。
        """
        try:
            chat_id = getattr(event, "unified_msg_origin", "") or ""
            if chat_id and self._is_silenced(chat_id):
                return False

            # 系统级唤醒前缀由 main.py 挂载：等价于点名唤醒
            try:
                if event.get_extra("angelheart_provider_wake_prefix", False):
                    return True
            except Exception:
                pass

            metadata = None
            try:
                if hasattr(event, "get_extra"):
                    metadata = event.get_extra("angelheart_message_metadata", None)
            except Exception:
                metadata = None

            if isinstance(metadata, dict) and "hits" in metadata:
                if metadata_has_hit(metadata, "at_self"):
                    return True
                if self._alias_detection_enabled(chat_id) and metadata_has_hit(metadata, "alias"):
                    return True
                return False

            # 兜底：当前事件组件上直接判定，不使用 outline
            try:
                self_id = str(event.get_self_id())
                for component in event.get_messages() or []:
                    qq = getattr(component, "qq", None)
                    if qq is None:
                        continue
                    cls_name = component.__class__.__name__
                    if str(qq) == self_id and ("At" in cls_name or "at" in cls_name.lower()):
                        return True
            except Exception:
                pass

            body_text = ""
            try:
                if hasattr(event, "get_extra"):
                    body_text = str(event.get_extra("angelheart_body_text", "") or "")
            except Exception:
                body_text = ""
            if not body_text:
                try:
                    body_text = extract_plain_body_from_components(event.get_messages() or [])
                except Exception:
                    body_text = ""
            return self._detect_wake_word(body_text, chat_id)
        except Exception as e:
            logger.debug(f"AngelHeart: 当前事件点名判定失败: {e}")
            return False

    def _is_silenced(self, chat_id: str) -> bool:
        """检查是否处于闭嘴状态"""
        current_time = time.time()
        silenced_until = self.angel_context.silenced_until.get(chat_id, 0)
        return current_time < silenced_until

    def _alias_detection_enabled(self, chat_id: str) -> bool:
        """点名昵称检测是否启用。"""
        cm = self.config_manager.for_chat(chat_id)
        return bool(
            cm.enter_on_mention_only
            or cm.force_reply_when_summoned
        )

    def _detect_wake_word(self, message_content: str, chat_id: str) -> bool:
        """检测正文中是否包含点名昵称（大小写不敏感）。"""
        if not self._alias_detection_enabled(chat_id):
            return False

        cm = self.config_manager.for_chat(chat_id)
        aliases = parse_pipe_phrases(cm.alias)
        if not aliases or not message_content:
            return False

        normalized = message_content.casefold()
        return any(alias.casefold() in normalized for alias in aliases)

    def get_leave_reply_trigger(self, chat_id: str) -> str:
        """返回当前离场消息应触发的一次性回复类型；无触发时返回空字符串。"""
        try:
            if self.angel_context.is_leave_reply_in_cooldown(chat_id):
                return ""
            cm = self.config_manager.for_chat(chat_id)
            if (
                cm.leave_echo_reply
                and self._detect_echo_chamber(chat_id)
            ):
                return "echo_chamber"
            if (
                cm.leave_dense_reply
                and self._detect_dense_conversation(chat_id)
            ):
                return "dense_conversation"
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 离场应答检测失败: {e}")
        return ""

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
            cm = self.config_manager.for_chat(chat_id)
            threshold = cm.echo_detection_threshold
            content_count = {}  # content -> count
            window = cm.echo_detection_window
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
                    has_image = any(
                        item.get("type") == "image_url"
                        for item in content
                        if isinstance(item, dict)
                    )
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
            cm = self.config_manager.for_chat(chat_id)
            window = cm.dense_conversation_window
            cutoff_time = time.time() - window
            message_threshold = cm.dense_conversation_threshold
            participant_threshold = cm.min_participant_count

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
            # 状态转换时不清理扣押计时器，两者是独立机制
            await self.angel_context._update_chat_status(chat_id, new_status, reason)

            # 记录状态开始时间
            self.status_start_times[chat_id] = (new_status, time.time())

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 状态转换失败: {e}")

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

    def get_status_start_time(self, chat_id: str) -> float:
        """
        获取状态开始时间

        Args:
            chat_id: 聊天会话ID

        Returns:
            float: 状态开始时间戳，0表示未找到
        """
        try:
            if chat_id not in self.status_start_times:
                return 0.0

            status, start_time = self.status_start_times[chat_id]
            return start_time
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

            has_assistant = False
            has_secretary = False
            try:
                dm = self.angel_context.debounce_manager
                has_assistant = dm.has_assistant_debounce(chat_id)
                has_secretary = dm.has_secretary_debounce(chat_id)
            except Exception:
                pass

            return {
                "current_status": status.value if status else "Unknown",
                "duration_seconds": round(duration, 2),
                "duration_minutes": round(duration / 60, 2),
                "has_assistant_debounce": has_assistant,
                "has_secretary_debounce": has_secretary,
            }
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 获取状态摘要失败: {e}")
            return {"current_status": "Error", "duration_seconds": 0}
