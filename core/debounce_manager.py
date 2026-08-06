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

MAXIMUM_ENERGY = 100.0
MINIMUM_ENERGY = -100.0
INITIAL_ENERGY = 100.0
ENERGY_RECOVERY_PER_SECOND = 0.6
BASE_REPLY_ENERGY_COST = 14.0
ENERGY_COST_PER_CHARACTER = 0.12


@dataclass
class ChatEnergyState:
    """单群运行时能量；不持久化，插件重启后重新初始化。"""

    energy: float = INITIAL_ENERGY
    updated_at: float = field(default_factory=time.time)


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

    def __init__(self, config_manager, work_ledger=None):
        self.config_manager = config_manager
        self.work_ledger = work_ledger
        self._lock = asyncio.Lock()
        self._assistant: Dict[Tuple[str, str], DebounceRecord] = {}
        self._secretary: Dict[str, DebounceRecord] = {}
        # 会话级秘书调度门闩：只覆盖秘书判断阶段。
        self._secretary_dispatching: Dict[str, str] = {}
        self._assistant_rest_until: Dict[str, float] = {}
        self.energy_states: Dict[str, ChatEnergyState] = {}
        self._version_seq = 0

    def _next_version(self) -> int:
        self._version_seq += 1
        return self._version_seq

    def _assistant_delay(self, chat_id: str) -> float:
        cm = self.config_manager.for_chat(chat_id)
        return max(0.05, float(getattr(cm, "assistant_debounce_time", 1.0)))

    def _secretary_delay(self, chat_id: str) -> float:
        cm = self.config_manager.for_chat(chat_id)
        return max(0.05, float(getattr(cm, "secretary_debounce_time", cm.waiting_time)))

    def _accelerate_delay(self, chat_id: str) -> float:
        cm = self.config_manager.for_chat(chat_id)
        return max(0.05, float(getattr(cm, "accelerate_debounce_time", 1.0)))

    def _initial_energy(self, chat_id: str) -> float:
        cm = self.config_manager.for_chat(chat_id)
        return float(getattr(cm, "initial_energy", INITIAL_ENERGY))

    def _maximum_energy(self, chat_id: str) -> float:
        cm = self.config_manager.for_chat(chat_id)
        return float(getattr(cm, "max_energy", MAXIMUM_ENERGY))

    def _minimum_energy(self, chat_id: str) -> float:
        cm = self.config_manager.for_chat(chat_id)
        return float(getattr(cm, "min_energy", MINIMUM_ENERGY))

    def _energy_recovery_per_second(self, chat_id: str) -> float:
        cm = self.config_manager.for_chat(chat_id)
        return float(
            getattr(
                cm,
                "recovery_per_second",
                ENERGY_RECOVERY_PER_SECOND,
            )
        )

    def _base_reply_energy_cost(self, chat_id: str) -> float:
        cm = self.config_manager.for_chat(chat_id)
        return float(
            getattr(cm, "base_reply_cost", BASE_REPLY_ENERGY_COST)
        )

    def _reply_energy_cost_per_character(self, chat_id: str) -> float:
        cm = self.config_manager.for_chat(chat_id)
        return float(
            getattr(
                cm,
                "reply_cost_per_character",
                ENERGY_COST_PER_CHARACTER,
            )
        )

    def _get_energy_state(self, chat_id: str) -> ChatEnergyState:
        chat_id = str(chat_id or "")
        state = self.energy_states.get(chat_id)
        if state is None:
            state = ChatEnergyState(energy=self._initial_energy(chat_id))
            self.energy_states[chat_id] = state
        return state

    def _recover_energy_before_patrol(self, chat_id: str) -> ChatEnergyState:
        """在普通巡检资格判断前，按当前时间恢复一次能量。"""
        state = self._get_energy_state(chat_id)
        now = time.time()
        elapsed = max(0.0, now - state.updated_at)
        state.energy = min(
            self._maximum_energy(chat_id),
            state.energy + elapsed * self._energy_recovery_per_second(chat_id),
        )
        state.updated_at = now
        return state

    def get_chat_energy(self, chat_id: str) -> float:
        return self._get_energy_state(chat_id).energy

    @staticmethod
    def _effective_character_count(message_chain) -> int:
        text_parts = []
        for component in message_chain:
            text = getattr(component, "text", None)
            if text is not None:
                text_parts.append(str(text))
                continue
            data = getattr(component, "data", None)
            if isinstance(data, dict):
                text = data.get("text", "")
                if text:
                    text_parts.append(str(text))
        return len("".join(text_parts).strip())

    async def charge_reply_energy(self, event: Any, message_chain) -> bool:
        """在最终消息链上对当前 AngelHeart 回复统一扣能一次。"""
        if not message_chain or not hasattr(event, "get_extra"):
            return False

        try:
            if not event.get_extra("angelheart_energy_charge_eligible", False):
                return False
            if event.get_extra("angelheart_energy_charged", False):
                return False
            chat_id = str(event.unified_msg_origin or "")
        except Exception:
            return False
        if not chat_id:
            return False

        character_count = self._effective_character_count(message_chain)
        cost = self._base_reply_energy_cost(chat_id) + character_count * self._reply_energy_cost_per_character(chat_id)
        async with self._lock:
            try:
                if event.get_extra("angelheart_energy_charged", False):
                    return False
                state = self._get_energy_state(chat_id)
                energy_before = state.energy
                state.energy = max(self._minimum_energy(chat_id), state.energy - cost)
                energy_after = state.energy
                state.updated_at = time.time()
                event.set_extra("angelheart_energy_charged", True)
            except Exception:
                logger.warning(
                    f"AngelHeart[{chat_id}]: 回复能量结算失败",
                    exc_info=True,
                )
                return False

        leave_reply_trigger = event.get_extra("angelheart_leave_reply_trigger", "")
        if leave_reply_trigger:
            mode = "leave_reply"
        elif event.get_extra("angelheart_must_reply", False):
            mode = "mention"
        else:
            mode = "ordinary"
        logger.info(
            f"AngelHeart[{chat_id}]: 回复结算 mode={mode} "
            f"characters={character_count} cost={cost:.2f} "
            f"energy={energy_before:.2f}->{energy_after:.2f}"
        )
        return True

    @staticmethod
    def _record_label(kind: str) -> str:
        return "助理防抖" if kind == "assistant" else "巡检"

    @staticmethod
    def _record_mode(record: DebounceRecord) -> str:
        if record.leave_reply_trigger:
            return "leave_reply"
        if record.must_reply:
            return "mention"
        return "ordinary"

    def _log_gate_decision(
        self,
        record: DebounceRecord,
        action: str,
        reason: str,
        details: str = "",
    ) -> None:
        label = "巡检" if record.kind == "secretary" else "助理防抖"
        fields = [
            f"mode={self._record_mode(record)}",
            f"action={action}",
            f"reason={reason}",
        ]
        if details:
            fields.append(details)
        log = logger.info if record.kind == "secretary" else logger.debug
        log(f"AngelHeart[{record.chat_id}]: {label}判定 " + " ".join(fields))

    def has_assistant_debounce(self, chat_id: str) -> bool:
        return any(key[0] == chat_id for key in self._assistant.keys())

    def has_secretary_debounce(self, chat_id: str) -> bool:
        return chat_id in self._secretary

    def has_secretary_dispatch(self, chat_id: str) -> bool:
        """当前会话是否已有一轮秘书分析尚未收口。

        秘书单飞只覆盖“判断是否接话”阶段；放行给助理后应立即释放，
        不得占到助理生成/发送完成。
        """
        return str(chat_id or "") in self._secretary_dispatching

    async def patrol_snapshot(self, chat_id: str) -> Dict[str, Any]:
        """返回该会话当前的巡检/等待快照，供 WebUI 状态栏展示。

        返回：
        {
            "waiting": "secretary" | "assistant" | "rest" | "",
            "remaining": 剩余秒数,
            "total": 本轮等待总秒数,
        }
        优先级：秘书防抖 > 点名防抖 > 助理休息。
        """
        chat_id = str(chat_id or "")
        now = time.time()
        async with self._lock:
            record = self._secretary.get(chat_id)
            if record is not None:
                remaining = max(0.0, record.created_at + record.delay - now)
                return {
                    "waiting": "secretary",
                    "remaining": round(remaining, 1),
                    "total": round(float(record.delay), 1),
                }
            assistant_keys = [key for key in self._assistant if key[0] == chat_id]
            if assistant_keys:
                record = min(
                    (self._assistant[key] for key in assistant_keys),
                    key=lambda r: r.created_at + r.delay,
                )
                remaining = max(0.0, record.created_at + record.delay - now)
                return {
                    "waiting": "assistant",
                    "remaining": round(remaining, 1),
                    "total": round(float(record.delay), 1),
                }
            rest_remaining = self._remaining_assistant_rest(chat_id)
            if rest_remaining > 0:
                return {
                    "waiting": "rest",
                    "remaining": round(rest_remaining, 1),
                    "total": round(rest_remaining, 1),
                }
            return {"waiting": "", "remaining": 0.0, "total": 0.0}

    def _remaining_assistant_rest(self, chat_id: str) -> float:
        """读取助理休息剩余时间；仅在 _lock 内调用。"""
        chat_id = str(chat_id or "")
        rest_until = self._assistant_rest_until.get(chat_id, 0.0)
        remaining = rest_until - time.time()
        if remaining <= 0:
            self._assistant_rest_until.pop(chat_id, None)
            return 0.0
        return remaining

    def _has_running_assistant_work(self, chat_id: str) -> bool:
        if self.work_ledger is None:
            return False
        try:
            return any(
                getattr(work, "status", "") == "running"
                and getattr(work, "kind", "") in ("assistant", "secretary")
                for work in self.work_ledger.get_active_works(chat_id)
            )
        except Exception:
            logger.warning(
                f"AngelHeart[{chat_id}]: 读取助理工作账本失败",
                exc_info=True,
            )
            return False

    def _reset_record_after_gate(self, record: DebounceRecord, reason: str) -> None:
        """硬门闩阻断放行时，保留最后边界事件并按完整巡检时长重计。"""
        if record.kind == "assistant":
            key = (record.chat_id, record.sender_id)
            if self._assistant.get(key) is record:
                self._assistant.pop(key, None)
            record.kind = "secretary"
            self._secretary[record.chat_id] = record

        record.delay = self._secretary_delay(record.chat_id)
        record.created_at = time.time()
        record.timer = asyncio.create_task(self._timer_handler(record))
        label = self._record_label(record.kind)
        logger.debug(
            f"AngelHeart[{record.chat_id}]: {label}到期但被门闩阻断，"
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
        """原子释放会话级秘书单飞；不再附带任何休息语义。"""
        chat_id = str(chat_id or "")
        dispatch_id = str(dispatch_id or "")
        async with self._lock:
            if not dispatch_id or self._secretary_dispatching.get(chat_id) != dispatch_id:
                return False

            self._secretary_dispatching.pop(chat_id, None)
            ignored_cooldown = max(0.0, float(cooldown_seconds))
            logger.debug(
                f"AngelHeart[{chat_id}]: 秘书调度收口 "
                f"(reason={reason or 'unknown'}, ignored_cooldown={ignored_cooldown:.2f}s)"
            )
            return True

    async def start_assistant_rest(
        self,
        chat_id: str,
        rest_seconds: float = 0.0,
        *,
        reason: str = "",
    ) -> bool:
        """启动助理休息；只表示助理已被调用后的普通巡检抑制。"""
        chat_id = str(chat_id or "")
        rest_seconds = max(0.0, float(rest_seconds))
        if not chat_id or rest_seconds <= 0:
            return False

        async with self._lock:
            self._assistant_rest_until[chat_id] = time.time() + rest_seconds
            logger.debug(
                f"AngelHeart[{chat_id}]: 启动助理休息 "
                f"(reason={reason or 'unknown'}, rest={rest_seconds:.2f}s)"
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
            self._assistant_rest_until.clear()
            self.energy_states.clear()
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
                delay=self._accelerate_delay(chat_id),
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
                delay=self._accelerate_delay(chat_id),
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
            delay=self._assistant_delay(chat_id),
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
                delay=self._assistant_delay(chat_id),
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
                    delay=self._secretary_delay(chat_id),
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
                delay=self._secretary_delay(chat_id),
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
                delay=self._secretary_delay(chat_id),
                must_reply=existing_secretary.must_reply,
                keep_start=True,
                reason="secretary_boundary_update",
            )

        if not leave_reply_trigger:
            rest_remaining = self._remaining_assistant_rest(chat_id)
            if rest_remaining > 0:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 助理休息中，普通消息仅入库 "
                    f"(sender={sender_id}, rest={rest_remaining:.2f}s)"
                )
                return None

        return await self._create_record(
            store="secretary",
            key=chat_id,
            chat_id=chat_id,
            event=event,
            sender_id=sender_id,
            message_id=message_id,
            kind="secretary",
            delay=self._secretary_delay(chat_id),
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
        label = self._record_label(kind)
        logger.debug(
            f"AngelHeart[{chat_id}]: 创建{label} "
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
        label = self._record_label(kind)
        logger.debug(
            f"AngelHeart[{chat_id}]: 更新{label} "
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
            f"AngelHeart[{record.chat_id}]: 旧{self._record_label(record.kind)}事件已 KILL "
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
                    # 同会话已有秘书正在分析，不并发放行第二轮秘书。
                    # 助理生成/发送不占此门闩。
                    self._log_gate_decision(
                        record, "retry", "secretary_dispatching"
                    )
                    self._reset_record_after_gate(record, "secretary_dispatching")
                    return

                if self._has_running_assistant_work(record.chat_id):
                    self._log_gate_decision(
                        record, "retry", "assistant_busy"
                    )
                    self._reset_record_after_gate(record, "assistant_busy")
                    return

                if not record.leave_reply_trigger:
                    rest_remaining = self._remaining_assistant_rest(record.chat_id)
                    if rest_remaining > 0:
                        self._log_gate_decision(
                            record,
                            "retry",
                            "assistant_rest",
                            f"rest={rest_remaining:.2f}s",
                        )
                        self._reset_record_after_gate(record, "assistant_rest")
                        return

                energy_after_gate = None
                recovered = None
                # 普通巡检才受精力门槛限制；点名和离场应答跳过精力门闩。
                if not record.leave_reply_trigger and not record.must_reply:
                    energy_state = self._get_energy_state(record.chat_id)
                    energy_before = energy_state.energy
                    energy_state = self._recover_energy_before_patrol(record.chat_id)
                    energy_after_gate = energy_state.energy
                    recovered = max(0.0, energy_after_gate - energy_before)
                    if energy_after_gate <= 0:
                        self._log_gate_decision(
                            record,
                            "retry",
                            "energy_insufficient",
                            f"energy={energy_after_gate:.2f} recovered={recovered:.2f}",
                        )
                        self._reset_record_after_gate(record, "energy_insufficient")
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
                            record.event.set_extra(
                                "angelheart_energy_charge_eligible", True
                            )
                    except Exception:
                        pass
                    mode = self._record_mode(record)
                    if mode == "ordinary":
                        self._log_gate_decision(
                            record,
                            "process",
                            "energy_sufficient",
                            f"energy={energy_after_gate:.2f} recovered={recovered:.2f} cooldown=pass",
                        )
                    elif mode == "mention":
                        self._log_gate_decision(
                            record,
                            "process",
                            "mentioned",
                            "energy_check=skip",
                        )
                    else:
                        self._log_gate_decision(
                            record,
                            "process",
                            "leave_reply",
                            "energy_check=skip rest=skip",
                        )
                    record.future.set_result(PROCESS)
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
