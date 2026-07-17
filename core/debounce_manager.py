"""群聊双防抖账本与扣押实现。

防抖是目的，扣押是实现原理：
- 账本自管
- 等待挂在事件上
- 旧事件 KILL，只放行最后边界事件
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

try:
    from astrbot.api import logger
except ImportError:
    import logging

    logger = logging.getLogger(__name__)


PROCESS = "PROCESS"
KILL = "KILL"


@dataclass
class DebounceRecord:
    """单条防抖/扣押记录。"""

    kind: str  # assistant | secretary
    chat_id: str
    sender_id: str
    event: Any
    future: asyncio.Future
    version: int
    must_reply: bool
    start_message_id: str
    end_message_id: str
    delay: float
    leave_reply_trigger: str = ""
    created_at: float = field(default_factory=time.time)
    timer: Optional[asyncio.Task] = None


class DebounceManager:
    """群聊双防抖管理器。"""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self._lock = asyncio.Lock()
        self._assistant: Dict[Tuple[str, str], DebounceRecord] = {}
        self._secretary: Dict[str, DebounceRecord] = {}
        # 会话级秘书调度门闩：防抖放行后一直占用到本轮发送/不回复收口。
        self._secretary_dispatching: Dict[str, str] = {}
        self._secretary_cooldown_until: Dict[str, float] = {}
        self._version_seq = 0

    def _next_version(self) -> int:
        self._version_seq += 1
        return self._version_seq

    def _assistant_delay(self) -> float:
        return max(0.05, float(getattr(self.config_manager, "assistant_debounce_time", 1.0)))

    def _secretary_delay(self) -> float:
        return max(0.05, float(getattr(self.config_manager, "secretary_debounce_time", self.config_manager.waiting_time)))

    def _accelerate_delay(self) -> float:
        return max(0.05, float(getattr(self.config_manager, "accelerate_debounce_time", 1.0)))

    def has_assistant_debounce(self, chat_id: str) -> bool:
        return any(key[0] == chat_id for key in self._assistant.keys())

    def has_secretary_debounce(self, chat_id: str) -> bool:
        return chat_id in self._secretary

    def has_secretary_dispatch(self, chat_id: str) -> bool:
        """当前会话是否已有一轮秘书从分析到发送收口仍在运行。"""
        return str(chat_id or "") in self._secretary_dispatching

    def _remaining_secretary_cooldown(self, chat_id: str) -> float:
        """读取剩余冷却；仅在 _lock 内调用。"""
        chat_id = str(chat_id or "")
        cooldown_until = self._secretary_cooldown_until.get(chat_id, 0.0)
        remaining = cooldown_until - time.time()
        if remaining <= 0:
            self._secretary_cooldown_until.pop(chat_id, None)
            return 0.0
        return remaining

    def _reset_record_after_gate(self, record: DebounceRecord, reason: str) -> None:
        """门闩阻断放行时，保留最后边界事件并完整重计当前类型的防抖。"""
        record.delay = (
            self._secretary_delay()
            if record.kind == "secretary"
            else self._assistant_delay()
        )
        record.created_at = time.time()
        record.timer = asyncio.create_task(self._timer_handler(record))
        logger.info(
            f"AngelHeart[{record.chat_id}]: {record.kind}防抖到期但被门闩阻断，"
            f"完整重计 {record.delay:.2f} 秒 (reason={reason}, version={record.version})"
        )

    async def finish_secretary_dispatch(
        self,
        chat_id: str,
        dispatch_id: str,
        *,
        cooldown_seconds: float = 0.0,
        reason: str = "",
    ) -> bool:
        """原子释放会话级秘书调度，并按结果启动下一轮普通消息冷却。"""
        chat_id = str(chat_id or "")
        dispatch_id = str(dispatch_id or "")
        async with self._lock:
            if not dispatch_id or self._secretary_dispatching.get(chat_id) != dispatch_id:
                return False

            self._secretary_dispatching.pop(chat_id, None)
            cooldown_seconds = max(0.0, float(cooldown_seconds))
            if cooldown_seconds:
                self._secretary_cooldown_until[chat_id] = time.time() + cooldown_seconds

            logger.info(
                f"AngelHeart[{chat_id}]: 秘书调度收口 "
                f"(reason={reason or 'unknown'}, cooldown={cooldown_seconds:.2f}s)"
            )
            return True

    async def clear_chat(self, chat_id: str, reason: str = "") -> None:
        """清除某会话全部防抖，旧事件全部 KILL。"""
        async with self._lock:
            assistant_keys = [key for key in self._assistant if key[0] == chat_id]
            for key in assistant_keys:
                await self._kill_record(self._assistant.pop(key), reason or "clear_chat")
            record = self._secretary.pop(chat_id, None)
            if record:
                await self._kill_record(record, reason or "clear_chat")

    async def cleanup(self) -> None:
        """取消全部计时任务，唤醒所有被扣押事件，并清空防抖账本。"""
        async with self._lock:
            records = list(self._assistant.values()) + list(self._secretary.values())
            self._assistant.clear()
            self._secretary.clear()
            self._secretary_dispatching.clear()
            self._secretary_cooldown_until.clear()
            timers = []
            for record in records:
                if record.timer and not record.timer.done():
                    record.timer.cancel()
                    timers.append(record.timer)
                if record.future and not record.future.done():
                    record.future.set_result(KILL)
        if timers:
            await asyncio.gather(*timers, return_exceptions=True)

    async def schedule(
        self,
        *,
        chat_id: str,
        event: Any,
        sender_id: str,
        message_id: str,
        is_wake: bool,
        is_present: bool,
        leave_reply_trigger: str = "",
    ) -> Optional[asyncio.Future]:
        """根据规则创建/更新防抖。

        Returns:
            Future: 调用方应 await；结果为 PROCESS / KILL。
            None: 本事件只入库，不进入后续请求。
        """
        sender_id = str(sender_id or "")
        message_id = str(message_id or "")

        async with self._lock:
            if is_wake:
                return await self._schedule_wake(
                    chat_id=chat_id,
                    event=event,
                    sender_id=sender_id,
                    message_id=message_id,
                    is_present=is_present,
                )
            return await self._schedule_non_wake(
                chat_id=chat_id,
                event=event,
                sender_id=sender_id,
                message_id=message_id,
                is_present=is_present,
                leave_reply_trigger=leave_reply_trigger,
            )

    async def _schedule_wake(
        self,
        *,
        chat_id: str,
        event: Any,
        sender_id: str,
        message_id: str,
        is_present: bool,
    ) -> Optional[asyncio.Future]:
        key = (chat_id, sender_id)
        existing_assistant = self._assistant.get(key)
        if existing_assistant:
            return await self._replace_record(
                store="assistant",
                key=key,
                old=existing_assistant,
                chat_id=chat_id,
                event=event,
                sender_id=sender_id,
                message_id=message_id,
                kind="assistant",
                delay=self._accelerate_delay(),
                must_reply=True,
                keep_start=True,
                reason="assistant_wake_accelerate",
            )

        existing_secretary = self._secretary.get(chat_id)
        if existing_secretary:
            return await self._replace_record(
                store="secretary",
                key=chat_id,
                old=existing_secretary,
                chat_id=chat_id,
                event=event,
                sender_id=sender_id,
                message_id=message_id,
                kind="secretary",
                delay=self._accelerate_delay(),
                must_reply=True,
                keep_start=True,
                reason="secretary_wake_accelerate",
            )

        # 离场唤醒 / 在场新唤醒：建立该群友助理防抖
        return await self._create_record(
            store="assistant",
            key=key,
            chat_id=chat_id,
            event=event,
            sender_id=sender_id,
            message_id=message_id,
            kind="assistant",
            delay=self._assistant_delay(),
            must_reply=True,
            reason="assistant_wake_create",
        )

    async def _schedule_non_wake(
        self,
        *,
        chat_id: str,
        event: Any,
        sender_id: str,
        message_id: str,
        is_present: bool,
        leave_reply_trigger: str,
    ) -> Optional[asyncio.Future]:
        key = (chat_id, sender_id)
        existing_assistant = self._assistant.get(key)
        if existing_assistant:
            # 同一群友助理防抖期间，后续消息更新边界，无需再次唤醒
            return await self._replace_record(
                store="assistant",
                key=key,
                old=existing_assistant,
                chat_id=chat_id,
                event=event,
                sender_id=sender_id,
                message_id=message_id,
                kind="assistant",
                delay=self._assistant_delay(),
                must_reply=existing_assistant.must_reply,
                keep_start=True,
                reason="assistant_boundary_update",
            )

        if self.has_assistant_debounce(chat_id):
            # 有其他群友的助理防抖时，不发起秘书防抖
            logger.debug(
                f"AngelHeart[{chat_id}]: 已有助理防抖，非唤醒消息仅入库 (sender={sender_id})"
            )
            return None

        if not is_present:
            if not leave_reply_trigger:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 离场未唤醒，仅入库 (sender={sender_id})"
                )
                return None

            existing_secretary = self._secretary.get(chat_id)
            if existing_secretary:
                return await self._replace_record(
                    store="secretary",
                    key=chat_id,
                    old=existing_secretary,
                    chat_id=chat_id,
                    event=event,
                    sender_id=sender_id,
                    message_id=message_id,
                    kind="secretary",
                    delay=self._secretary_delay(),
                    must_reply=True,
                    keep_start=True,
                    reason="leave_reply_boundary_update",
                    leave_reply_trigger=leave_reply_trigger,
                )

            return await self._create_record(
                store="secretary",
                key=chat_id,
                chat_id=chat_id,
                event=event,
                sender_id=sender_id,
                message_id=message_id,
                kind="secretary",
                delay=self._secretary_delay(),
                must_reply=True,
                reason="leave_reply_create",
                leave_reply_trigger=leave_reply_trigger,
            )

        existing_secretary = self._secretary.get(chat_id)
        if existing_secretary:
            return await self._replace_record(
                store="secretary",
                key=chat_id,
                old=existing_secretary,
                chat_id=chat_id,
                event=event,
                sender_id=sender_id,
                message_id=message_id,
                kind="secretary",
                delay=self._secretary_delay(),
                must_reply=existing_secretary.must_reply,
                keep_start=True,
                reason="secretary_boundary_update",
            )

        return await self._create_record(
            store="secretary",
            key=chat_id,
            chat_id=chat_id,
            event=event,
            sender_id=sender_id,
            message_id=message_id,
            kind="secretary",
            delay=self._secretary_delay(),
            must_reply=False,
            reason="secretary_create",
        )

    async def _create_record(
        self,
        *,
        store: str,
        key: Any,
        chat_id: str,
        event: Any,
        sender_id: str,
        message_id: str,
        kind: str,
        delay: float,
        must_reply: bool,
        reason: str,
        leave_reply_trigger: str = "",
    ) -> asyncio.Future:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        version = self._next_version()
        record = DebounceRecord(
            kind=kind,
            chat_id=chat_id,
            sender_id=sender_id,
            event=event,
            future=future,
            version=version,
            must_reply=must_reply,
            start_message_id=message_id,
            end_message_id=message_id,
            delay=delay,
            leave_reply_trigger=leave_reply_trigger,
        )
        record.timer = asyncio.create_task(self._timer_handler(record))
        if store == "assistant":
            self._assistant[key] = record
        else:
            self._secretary[key] = record
        logger.info(
            f"AngelHeart[{chat_id}]: 创建{kind}防抖/扣押 "
            f"(sender={sender_id}, delay={delay:.2f}s, must_reply={must_reply}, reason={reason})"
        )
        return future

    async def _replace_record(
        self,
        *,
        store: str,
        key: Any,
        old: DebounceRecord,
        chat_id: str,
        event: Any,
        sender_id: str,
        message_id: str,
        kind: str,
        delay: float,
        must_reply: bool,
        keep_start: bool,
        reason: str,
        leave_reply_trigger: str = "",
    ) -> asyncio.Future:
        await self._kill_record(old, reason)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        version = self._next_version()
        record = DebounceRecord(
            kind=kind,
            chat_id=chat_id,
            sender_id=sender_id,
            event=event,
            future=future,
            version=version,
            must_reply=must_reply,
            start_message_id=old.start_message_id if keep_start else message_id,
            end_message_id=message_id,
            delay=delay,
            leave_reply_trigger=leave_reply_trigger,
        )
        record.timer = asyncio.create_task(self._timer_handler(record))
        if store == "assistant":
            self._assistant[key] = record
        else:
            self._secretary[key] = record
        logger.info(
            f"AngelHeart[{chat_id}]: 更新{kind}防抖/扣押 "
            f"(sender={sender_id}, delay={delay:.2f}s, must_reply={must_reply}, reason={reason})"
        )
        return future

    async def _kill_record(self, record: DebounceRecord, reason: str) -> None:
        timer = record.timer
        if timer and not timer.done():
            timer.cancel()
        if record.future and not record.future.done():
            record.future.set_result(KILL)
        if timer:
            await asyncio.gather(timer, return_exceptions=True)
        logger.debug(
            f"AngelHeart[{record.chat_id}]: 旧{record.kind}事件已 KILL "
            f"(sender={record.sender_id}, reason={reason}, version={record.version})"
        )

    async def _timer_handler(self, record: DebounceRecord) -> None:
        try:
            await asyncio.sleep(record.delay)
            async with self._lock:
                current = self._get_current_record(record)
                if current is None or current.version != record.version:
                    return

                if self._secretary_dispatching.get(record.chat_id):
                    # 同会话已有秘书从分析到发送收口仍在运行，绝不并发放行第二轮。
                    self._reset_record_after_gate(record, "secretary_dispatching")
                    return

                if record.kind == "secretary" and not record.leave_reply_trigger:
                    cooldown_remaining = self._remaining_secretary_cooldown(record.chat_id)
                    if cooldown_remaining > 0:
                        # 冷却中仍保留最后边界事件，但从本次到期时刻完整重计一轮。
                        self._reset_record_after_gate(record, "secretary_cooldown")
                        return

                # 从账本移除后再放行；调度占用必须先写入，避免其他到期事件并发进入秘书。
                self._pop_current_record(record)
                dispatch_id = str(record.version)
                self._secretary_dispatching[record.chat_id] = dispatch_id
                if record.future and not record.future.done():
                    # 把防抖结果与会话级调度归属挂到事件上，供完成路径原子收口。
                    try:
                        if hasattr(record.event, "set_extra"):
                            record.event.set_extra("angelheart_must_reply", record.must_reply)
                            record.event.set_extra("angelheart_debounce_kind", record.kind)
                            record.event.set_extra(
                                "angelheart_debounce_start_message_id", record.start_message_id
                            )
                            record.event.set_extra(
                                "angelheart_debounce_end_message_id", record.end_message_id
                            )
                            record.event.set_extra("angelheart_secretary_dispatch_id", dispatch_id)
                            record.event.set_extra(
                                "angelheart_leave_reply_trigger", record.leave_reply_trigger
                            )
                    except Exception:
                        pass
                    record.future.set_result(PROCESS)
                    logger.info(
                        f"AngelHeart[{record.chat_id}]: {record.kind}防抖到期放行 "
                        f"(sender={record.sender_id}, must_reply={record.must_reply}, "
                        f"version={record.version})"
                    )
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(
                f"AngelHeart[{record.chat_id}]: 防抖计时异常: {e}",
                exc_info=True,
            )
            if record.future and not record.future.done():
                record.future.set_result(KILL)

    def _get_current_record(self, record: DebounceRecord) -> Optional[DebounceRecord]:
        if record.kind == "assistant":
            return self._assistant.get((record.chat_id, record.sender_id))
        return self._secretary.get(record.chat_id)

    def _pop_current_record(self, record: DebounceRecord) -> None:
        if record.kind == "assistant":
            key = (record.chat_id, record.sender_id)
            current = self._assistant.get(key)
            if current and current.version == record.version:
                self._assistant.pop(key, None)
            return
        current = self._secretary.get(record.chat_id)
        if current and current.version == record.version:
            self._secretary.pop(record.chat_id, None)

    def get_must_reply(self, event: Any) -> bool:
        try:
            if hasattr(event, "get_extra"):
                return bool(event.get_extra("angelheart_must_reply", False))
        except Exception:
            pass
        return False

    def get_debounce_kind(self, event: Any) -> str:
        try:
            if hasattr(event, "get_extra"):
                return str(event.get_extra("angelheart_debounce_kind", "") or "")
        except Exception:
            pass
        return ""

    def get_leave_reply_trigger(self, event: Any) -> str:
        try:
            if hasattr(event, "get_extra"):
                return str(event.get_extra("angelheart_leave_reply_trigger", "") or "")
        except Exception:
            pass
        return ""

    def get_end_message_id(self, event: Any) -> str:
        try:
            if hasattr(event, "get_extra"):
                return str(
                    event.get_extra("angelheart_debounce_end_message_id", "") or ""
                )
        except Exception:
            pass
        return ""
