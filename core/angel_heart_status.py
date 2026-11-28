"""AngelHeart 插件 - 状态系统核心模块"""

import time
from enum import Enum
from typing import Dict, List, Optional, Tuple

from astrbot.api import logger

from .utils.message_hits import (
    extract_plain_body_from_components,
    metadata_has_hit,
    parse_pipe_phrases,
)

# 上下文整理产出的摘要消息以 user 角色存在账本里，但它不是任何人说的话，
# 任何「扫描用户消息」的判断都必须把它排除。
SUMMARY_MESSAGE_KINDS = ("context_summary",)

# 冷场关怀的固定参数，不开放配置：一个人连说 3 句、都在 10 分钟内、单条不超过 50 字。
# 判定细则见 StatusChecker._detect_lone_speaker。
LONE_SPEAKER_THRESHOLD = 3
LONE_SPEAKER_WINDOW_SECONDS = 600
LONE_SPEAKER_MAX_CHARS = 50


def _is_summary_message(msg: Dict) -> bool:
    return msg.get("kind") in SUMMARY_MESSAGE_KINDS


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
        """获取账本里最新的用户消息（过滤 assistant/tool/system）。

        这是**账本视角**的最新 user，不是当前轮输入。当前轮消息走
        `req.prompt` / `event.message_str`，不经过账本压缩，也不靠这个函数取。

        压缩不会让它取错：整理永远保留最近的消息，当前轮一定在当前块里，
        所以取到的仍是同一条，只是可能被折成历史条目（正文截断）。
        """
        try:
            ledger = self.angel_context.conversation_ledger
            all_messages = ledger.get_all_messages(chat_id)
            if not all_messages:
                return None

            user_messages = [
                m
                for m in all_messages
                if m.get("role") == "user" and not _is_summary_message(m)
            ]
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
                if m.get("role") != "user" or _is_summary_message(m):
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

            metadata = None
            try:
                if hasattr(event, "get_extra"):
                    metadata = event.get_extra("angelheart_message_metadata", None)
            except Exception:
                metadata = None

            if isinstance(metadata, dict) and "hits" in metadata:
                if metadata_has_hit(metadata, "at_self"):
                    return True
                if self._alias_detection_enabled(chat_id) and metadata_has_hit(
                    metadata, "alias"
                ):
                    return self._accept_alias_wake(event, chat_id)
                return False

            # 兜底：当前事件组件上直接判定，不使用 outline
            try:
                self_id = str(event.get_self_id())
                for component in event.get_messages() or []:
                    cls_name = component.__class__.__name__
                    # 引用自己发的消息视为点名（与 main._should_process 口径一致）
                    sender_id = getattr(component, "sender_id", None)
                    if (
                        sender_id is not None
                        and str(sender_id) == self_id
                        and "Reply" in cls_name
                    ):
                        return True
                    qq = getattr(component, "qq", None)
                    if qq is None:
                        continue
                    if str(qq) == self_id and (
                        "At" in cls_name or "at" in cls_name.lower()
                    ):
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
                    body_text = extract_plain_body_from_components(
                        event.get_messages() or []
                    )
                except Exception:
                    body_text = ""
            return self._detect_wake_word(
                body_text, chat_id
            ) and self._accept_alias_wake(event, chat_id)
        except Exception as e:
            logger.debug(f"AngelHeart: 当前事件点名判定失败: {e}")
            return False

    def _accept_alias_wake(self, event, chat_id: str) -> bool:
        """同群首次昵称唤醒占用冷却；重复读取本事件不再次计时。"""
        parts = chat_id.split(":")
        if len(parts) >= 3 and parts[1] == "FriendMessage":
            return True
        seconds = self.config_manager.for_chat(chat_id).alias_wake_cooldown_seconds
        if seconds <= 0:
            return True

        with self.angel_context.alias_wake_lock:
            accepted = event.get_extra("angelheart_alias_wake_accepted", None)
            if isinstance(accepted, bool):
                return accepted
            now = time.monotonic()
            previous = self.angel_context.last_alias_wake_time.get(chat_id)
            accepted = previous is None or now - previous >= seconds
            if accepted:
                self.angel_context.last_alias_wake_time[chat_id] = now
            event.set_extra("angelheart_alias_wake_accepted", accepted)
            return accepted

    def _is_silenced(self, chat_id: str) -> bool:
        """检查是否处于闭嘴状态"""
        current_time = time.time()
        silenced_until = self.angel_context.silenced_until.get(chat_id, 0)
        return current_time < silenced_until

    def _alias_detection_enabled(self, chat_id: str) -> bool:
        """点名昵称检测是否启用。"""
        cm = self.config_manager.for_chat(chat_id)
        return bool(cm.enter_on_mention_only or cm.force_reply_when_summoned)

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

    def get_leave_reply_trigger(
        self, chat_id: str, *, message_id: Optional[str] = None
    ) -> str:
        """返回当前离场消息应触发的一次性回复类型；无触发时返回空字符串。"""
        try:
            if self.angel_context.is_leave_reply_in_cooldown(chat_id):
                return ""
            cm = self.config_manager.for_chat(chat_id)
            if cm.leave_echo_reply and self._detect_echo_chamber(
                chat_id, message_id=message_id
            ):
                return "echo_chamber"
            if cm.leave_dense_reply and self._detect_dense_conversation(chat_id):
                return "dense_conversation"
            if cm.leave_lone_reply and self._detect_lone_speaker(
                chat_id, message_id=message_id
            ):
                return "lone_speaker"
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 离场应答检测失败: {e}")
        return ""

    @staticmethod
    def _echo_message_text(message: Dict) -> str:
        """Read only actual user text; summaries and nontext placeholders do not count."""
        if (
            message.get("role") != "user"
            or _is_summary_message(message)
            or message.get("echo_eligible") is False
        ):
            return ""
        content = message.get("content", "")
        if isinstance(content, list):
            if not content or any(
                not isinstance(part, dict)
                or part.get("type") != "text"
                or not isinstance(part.get("text"), str)
                for part in content
            ):
                return ""
            content = "".join(part["text"] for part in content)
        return content.strip() if isinstance(content, str) else ""

    def _detect_echo_chamber(
        self, chat_id: str, *, message_id: Optional[str] = None
    ) -> bool:
        """Count matching sources within the window, bounded by the triggering event."""
        try:
            all_messages = self.angel_context.conversation_ledger.get_all_messages(
                chat_id
            )
            requested_id = str(message_id or "") if message_id is not None else None
            if requested_id == "":
                return False
            target_index = None
            for index in range(len(all_messages) - 1, -1, -1):
                message = all_messages[index]
                if message.get("role") != "user" or _is_summary_message(message):
                    continue
                identity = str(
                    message.get("source_message_id", message.get("message_id", ""))
                    or ""
                )
                if requested_id is None or identity == requested_id:
                    target_index = index
                    break
            if target_index is None:
                return False
            target = all_messages[target_index]
            text = self._echo_message_text(target)
            if not text:
                return False
            cm = self.config_manager.for_chat(chat_id)
            cutoff_time = time.time() - cm.echo_detection_window
            if target.get("timestamp", 0) < cutoff_time:
                return False
            count = 0
            seen = set()
            for message in reversed(all_messages[: target_index + 1]):
                if message.get("role") != "user" or _is_summary_message(message):
                    continue
                if self._echo_message_text(message) != text:
                    continue
                identity = str(
                    message.get("source_message_id", message.get("message_id", ""))
                    or ""
                )
                if identity and identity in seen:
                    continue
                if identity:
                    seen.add(identity)
                if message.get("timestamp", 0) >= cutoff_time:
                    count += 1
            return count >= max(1, int(cm.echo_detection_threshold))
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

    def _lone_message_text(self, message: Dict) -> str:
        """取这条消息的纯文字正文；不是纯文字（含引用、@、图片表情）返回空串。

        `lone_eligible` 由实时消息入口按原始消息段是否全是 Plain 写入，是唯一可信的
        纯文字标记。缺失时一律按非纯文字处理（fail-closed）：冷启动补种的消息不带这个
        字段，且其 content 是把原始消息压平后的字符串，图片与 @ 段在压平中就丢了，
        放行等于把「图 + 一句话」当成纯文字。宁可这类消息打断连发段，也不要误接。
        """
        if message.get("role") != "user" or _is_summary_message(message):
            return ""
        if message.get("lone_eligible") is not True:
            return ""
        content = message.get("content", "")
        if isinstance(content, list):
            if not content or any(
                not isinstance(part, dict)
                or part.get("type") != "text"
                or not isinstance(part.get("text"), str)
                for part in content
            ):
                return ""
            content = "".join(part["text"] for part in content)
        return content.strip() if isinstance(content, str) else ""

    @staticmethod
    def _spam_key(text: str) -> str:
        """把一句话压成比较用的形状：只留文字数字，丢掉标点和空白。

        刷屏的特征是同一条内容反复发，所以只认「压完之后完全一样」，
        不按字符重合比例判——中文短句本来就共用常用字。
        """
        return "".join(ch for ch in str(text or "") if ch.isalnum())

    def _detect_lone_speaker(
        self, chat_id: str, *, message_id: Optional[str] = None
    ) -> bool:
        """同一人自说自话没人接。

        窗口内只有他一个人在说话、每条都是纯文字且不算太长、条数达到阈值、
        三条内容不全是同一句（挡刷屏）。同一个人当天已经被接过就不再接。

        message_id 给出时只统计到这条消息为止，避免把后来的消息算进这一轮。
        """
        try:
            all_messages = self.angel_context.conversation_ledger.get_all_messages(
                chat_id
            )
            requested_id = str(message_id or "") if message_id is not None else None
            if requested_id == "":
                return False
            target_index = None
            for index in range(len(all_messages) - 1, -1, -1):
                message = all_messages[index]
                if message.get("role") != "user" or _is_summary_message(message):
                    continue
                identity = str(
                    message.get("source_message_id", message.get("message_id", ""))
                    or ""
                )
                if requested_id is None or identity == requested_id:
                    target_index = index
                    break
            if target_index is None:
                return False

            target = all_messages[target_index]
            sender_id = str(target.get("sender_id", "") or "")
            if not sender_id:
                return False

            # 同一个人当天已经被接过一次，就不再接。
            given = self.angel_context.lone_reply_given.get(chat_id) or {}
            today = time.strftime("%Y-%m-%d")
            if given.get(sender_id) == today:
                return False

            target_text = self._lone_message_text(target)
            if not target_text or len(target_text) > LONE_SPEAKER_MAX_CHARS:
                return False

            cutoff_time = time.time() - LONE_SPEAKER_WINDOW_SECONDS
            if float(target.get("timestamp", 0) or 0) < cutoff_time:
                return False

            texts: List[str] = []
            seen = set()
            for message in reversed(all_messages[: target_index + 1]):
                if message.get("role") != "user" or _is_summary_message(message):
                    continue
                if float(message.get("timestamp", 0) or 0) < cutoff_time:
                    continue
                # 别人的消息是连发段的边界：他这段自说自话到此为止，
                # 更早别人说过话不算他"没人搭话"，只说明连发段从这里开始。
                if str(message.get("sender_id", "") or "") != sender_id:
                    break
                identity = str(
                    message.get("source_message_id", message.get("message_id", ""))
                    or ""
                )
                if identity and identity in seen:
                    continue
                if identity:
                    seen.add(identity)
                text = self._lone_message_text(message)
                if not text:
                    # 中间夹了带图、带引用、带 @ 的消息：不是连续的纯文字，本轮作废
                    return False
                if len(text) > LONE_SPEAKER_MAX_CHARS:
                    # 纯文字但太长：这条不计入，也不算中断
                    continue
                texts.append(text)

            if len(texts) < LONE_SPEAKER_THRESHOLD:
                return False

            # 三条全是同一句话 → 刷屏广告，不算。
            # 只认「全部相同」：人自说自话时重复一个应答词（好、嗯）很常见，
            # 按部分重复判会把正常连发整轮吞掉。
            # 归一化后为空的（纯标点、纯 emoji）退回原文比较：三条各不相同的表情
            # 压完都是空串，直接按集合去重会被误判成刷屏；三条一模一样的表情仍该挡。
            spam_keys = {self._spam_key(text) or text.strip() for text in texts}
            if len(texts) > 1 and len(spam_keys) == 1:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 自说自话检测 - 内容完全重复，判为刷屏，不接"
                )
                return False

            given[sender_id] = today
            self.angel_context.lone_reply_given[chat_id] = given
            logger.debug(
                f"AngelHeart[{chat_id}]: 自说自话检测 - {sender_id} "
                f"连发 {len(texts)} 条纯文字无人应答"
            )
            return True

        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 自说自话检测失败: {e}")
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
