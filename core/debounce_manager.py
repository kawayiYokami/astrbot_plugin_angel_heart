"""群聊双防抖账本与扣押实现。

防抖是目的，扣押是实现原理：
- 账本自管
- 等待挂在事件上
- 旧事件 KILL，只放行最后边界事件
"""

from __future__ import annotations

import asyncio
import time
import threading
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
    start_event_id: str
    end_event_id: str
    delay: float
    generation: int = 0
    created_at: float = field(default_factory=time.time)
    timer: Optional[asyncio.Task] = None


class DebounceManager:
    """群聊双防抖管理器。"""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self._lock = asyncio.Lock()
        self._assistant: Dict[Tuple[str, str], DebounceRecord] = {}
        self._secretary: Dict[str, DebounceRecord] = {}
        self._version_seq = 0
        # 同步代际：整理开始时 bump，后续 schedule 用新代际，clear 只杀旧代际
        self._generation_lock = threading.Lock()
        self._generation: Dict[str, int] = {}

    def _next_version(self) -> int:
        self._version_seq += 1
        return self._version_seq

    def current_generation(self, chat_id: str) -> int:
        with self._generation_lock:
            return int(self._generation.get(str(chat_id or ""), 0))

    def bump_generation(self, chat_id: str) -> int:
        """整理开始：代际 +1，返回旧代际（clear 只杀 <= 旧代际）。"""
        chat_id = str(chat_id or "")
        with self._generation_lock:
            old = int(self._generation.get(chat_id, 0))
            self._generation[chat_id] = old + 1
            return old

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

    async def clear_chat(
        self,
        chat_id: str,
        reason: str = "",
        *,
        only_upto_generation: int | None = None,
    ) -> None:
        """清除某会话防抖。

        only_upto_generation 有值时，只杀 generation <= 该值的记录，
        避免整理回调误杀整理后新建的 ticket。
        """
        async with self._lock:
            assistant_keys = [key for key in self._assistant if key[0] == chat_id]
            for key in assistant_keys:
                record = self._assistant.get(key)
                if record is None:
                    continue
                if (
                    only_upto_generation is not None
                    and int(getattr(record, "generation", 0)) > only_upto_generation
                ):
                    continue
                await self._kill_record(
                    self._assistant.pop(key), reason or "clear_chat"
                )
            record = self._secretary.get(chat_id)
            if record is not None:
                if (
                    only_upto_generation is None
                    or int(getattr(record, "generation", 0)) <= only_upto_generation
                ):
                    await self._kill_record(
                        self._secretary.pop(chat_id), reason or "clear_chat"
                    )

    async def schedule(
        self,
        *,
        chat_id: str,
        event: Any,
        sender_id: str,
        event_id: str,
        is_wake: bool,
        is_present: bool,
    ) -> Optional[asyncio.Future]:
        """根据规则创建/更新防抖。

        Returns:
            Future: 调用方应 await；结果为 PROCESS / KILL。
            None: 本事件只入库，不进入后续请求。
        """
        sender_id = str(sender_id or "")
        event_id = str(event_id or "")

        async with self._lock:
            if is_wake:
                return await self._schedule_wake(
                    chat_id=chat_id,
                    event=event,
                    sender_id=sender_id,
                    event_id=event_id,
                    is_present=is_present,
                )
            return await self._schedule_non_wake(
                chat_id=chat_id,
                event=event,
                sender_id=sender_id,
                event_id=event_id,
                is_present=is_present,
            )

    async def _schedule_wake(
        self,
        *,
        chat_id: str,
        event: Any,
        sender_id: str,
        event_id: str,
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
                event_id=event_id,
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
                event_id=event_id,
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
            event_id=event_id,
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
        event_id: str,
        is_present: bool,
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
                event_id=event_id,
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
            # 离场未唤醒：只入库
            logger.debug(f"AngelHeart[{chat_id}]: 离场未唤醒，仅入库 (sender={sender_id})")
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
                event_id=event_id,
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
            event_id=event_id,
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
        event_id: str,
        kind: str,
        delay: float,
        must_reply: bool,
        reason: str,
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
            start_event_id=event_id,
            end_event_id=event_id,
            delay=delay,
            generation=self.current_generation(chat_id),
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
        event_id: str,
        kind: str,
        delay: float,
        must_reply: bool,
        keep_start: bool,
        reason: str,
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
            start_event_id=old.start_event_id if keep_start else event_id,
            end_event_id=event_id,
            delay=delay,
            generation=self.current_generation(chat_id),
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
        if record.timer and not record.timer.done():
            record.timer.cancel()
        if record.future and not record.future.done():
            record.future.set_result(KILL)
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
                # 从账本移除后再放行，避免重复触发
                self._pop_current_record(record)
                if record.future and not record.future.done():
                    # 把 must_reply 挂到事件上，激活后重建上下文再决策
                    try:
                        if hasattr(record.event, "set_extra"):
                            record.event.set_extra("angelheart_must_reply", record.must_reply)
                            record.event.set_extra("angelheart_debounce_kind", record.kind)
                            record.event.set_extra(
                                "angelheart_debounce_start_event_id", record.start_event_id
                            )
                            record.event.set_extra(
                                "angelheart_debounce_end_event_id", record.end_event_id
                            )
                    except Exception:
                        pass
                    record.future.set_result(PROCESS)
                    logger.info(
                        f"AngelHeart[{record.chat_id}]: {record.kind}防抖到期放行 "
                        f"(sender={record.sender_id}, must_reply={record.must_reply}, version={record.version})"
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
