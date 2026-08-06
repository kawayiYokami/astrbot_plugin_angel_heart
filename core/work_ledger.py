"""助理工作账本。

记录当前会话里助理正在/已经处理哪一套活：
- work_id
- 触发消息锚点
- 状态：running / done / failed
- 任务摘要 / 结果摘要

用途：
- 注入给秘书：第三人称，避免再派重复问题
- 注入给助理：第二人称临时提醒（_no_save），避免重复回答
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class WorkItem:
    work_id: str
    chat_id: str
    trigger_message_id: str
    trigger_summary: str
    status: str = "running"  # running | done | failed
    result_summary: str = ""
    started_at: float = field(default_factory=time.time)
    ended_at: float = 0.0
    kind: str = ""  # assistant / secretary / private 等

    def to_dict(self) -> Dict:
        return {
            "work_id": self.work_id,
            "chat_id": self.chat_id,
            "trigger_message_id": self.trigger_message_id,
            "trigger_summary": self.trigger_summary,
            "status": self.status,
            "result_summary": self.result_summary,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "kind": self.kind,
        }


class WorkLedger:
    """按会话维护工作账本。

    同步方法（无 await）全部在可重入锁内执行，保证多任务并发下
    _items 与 WorkItem 状态的读写原子性。
    """

    def __init__(
        self,
        retain_finished: int = 8,
        running_timeout: float = 300.0,
        time_func: Optional[Callable[[], float]] = None,
    ):
        self._items: Dict[str, Dict[str, WorkItem]] = {}
        self.retain_finished = max(1, int(retain_finished))
        # running 超时自动失效：防止 complete_work 漏触发（如流式回复/主脑失败）
        # 导致孤儿 running 永久阻断 assistant_busy 门闩。<=0 表示不启用。
        self.running_timeout = float(running_timeout)
        self._lock = threading.RLock()
        self._time = time_func or time.time

    def start_work(
        self,
        *,
        chat_id: str,
        work_id: str,
        trigger_message_id: str,
        trigger_summary: str,
        kind: str = "",
    ) -> WorkItem:
        chat_id = str(chat_id or "")
        work_id = str(work_id or "")
        item = WorkItem(
            work_id=work_id,
            chat_id=chat_id,
            trigger_message_id=str(trigger_message_id or ""),
            trigger_summary=(trigger_summary or "").strip() or "未命名工作",
            status="running",
            kind=str(kind or ""),
            started_at=self._time(),
        )
        with self._lock:
            bucket = self._items.setdefault(chat_id, {})
            # 同群互斥兜底：新工作开始即关闭该 chat 的旧 running，
            # 避免并发放行残留的 running 继续阻断秘书巡检。
            now = self._time()
            for old in bucket.values():
                if old.status == "running":
                    old.status = "failed"
                    old.result_summary = "被新工作替换"
                    old.ended_at = now
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 关闭旧 running 工作 "
                        f"work_id={old.work_id} 原因=被新工作替换 "
                        f"新工作={work_id} running_timeout={self.running_timeout:.0f}s"
                    )
            bucket[work_id] = item
            self._trim(chat_id)
        return item

    def _expire_stale_running(self, bucket: Dict[str, WorkItem]) -> None:
        """把超时未收口的 running 工作惰性标记为 failed。"""
        if self.running_timeout <= 0:
            return
        now = self._time()
        for item in bucket.values():
            if (
                item.status == "running"
                and (now - item.started_at) >= self.running_timeout
            ):
                item.status = "failed"
                item.result_summary = "运行超时自动关闭"
                item.ended_at = now
                logger.debug(
                    f"AngelHeart[{item.chat_id}]: 关闭孤儿 running 工作 "
                    f"work_id={item.work_id} 原因=运行超时自动关闭 "
                    f"running_timeout={self.running_timeout:.0f}s"
                )

    def complete_work(
        self,
        chat_id: str,
        work_id: str,
        *,
        status: str = "done",
        result_summary: str = "",
    ) -> Optional[WorkItem]:
        chat_id = str(chat_id or "")
        work_id = str(work_id or "")
        with self._lock:
            bucket = self._items.get(chat_id) or {}
            item = bucket.get(work_id)
            if not item:
                return None
            item.status = status if status in ("done", "failed", "running") else "done"
            item.result_summary = (result_summary or "").strip()
            item.ended_at = self._time()
            self._trim(chat_id)
        return item

    def get_active_works(self, chat_id: str) -> List[WorkItem]:
        with self._lock:
            bucket = self._items.get(str(chat_id or "")) or {}
            self._expire_stale_running(bucket)
            return [w for w in bucket.values() if w.status == "running"]

    def get_recent_works(self, chat_id: str, limit: int = 8) -> List[WorkItem]:
        with self._lock:
            bucket = self._items.get(str(chat_id or "")) or {}
            self._expire_stale_running(bucket)
            items = list(bucket.values())
            items.sort(key=lambda w: w.started_at, reverse=True)
            return items[: max(1, int(limit))]

    def format_for_secretary(self, chat_id: str, current_work_id: str = "") -> str:
        """第三人称：给秘书。

        本轮 current_work_id 可展示，但不说「不要让助理处理这个问题」。
        避让话术只针对其他 running 工作。
        """
        works = self.get_recent_works(chat_id)
        if not works:
            return "助理工作账本：当前无登记中的工作。"

        current_work_id = str(current_work_id or "")
        lines = ["助理工作账本："]
        current = None
        others_running = []
        for w in works:
            status_cn = {
                "running": "运行中",
                "done": "已完成",
                "failed": "失败",
            }.get(w.status, w.status)
            is_current = bool(current_work_id and w.work_id == current_work_id)
            if is_current:
                current = w
                tag = "本轮"
            else:
                tag = status_cn
                if w.status == "running":
                    others_running.append(w)
            line = (
                f"- [{tag}] 触发锚点={w.trigger_message_id or '未知'}；"
                f"任务={w.trigger_summary}"
            )
            if w.result_summary:
                line += f"；结果={w.result_summary}"
            lines.append(line)

        if current and current.status == "running":
            lines.append(
                f"本轮待处理：{current.trigger_summary}。这是当前事件对应的工作，可以继续决策。"
            )

        if others_running:
            names = "；".join(w.trigger_summary for w in others_running)
            lines.append(
                f"助理正在处理：{names}。不要让助理处理重复的问题。"
            )
        elif not (current and current.status == "running"):
            lines.append("当前没有运行中的助理工作。")
        return "\n".join(lines)

    def format_for_assistant(self, chat_id: str, current_work_id: str = "") -> str:
        """为主脑构建当前工作之外的临时账本提醒。"""
        other_works = [
            work
            for work in self.get_recent_works(chat_id)
            if not current_work_id or work.work_id != current_work_id
        ]
        if not other_works:
            return "工作账本：当前没有其他已登记工作。"

        lines = ["工作账本："]
        for work in other_works:
            status_cn = {
                "running": "运行中",
                "done": "已完成",
                "failed": "失败",
            }.get(work.status, work.status)
            line = (
                f"- [{status_cn}] 触发锚点={work.trigger_message_id or '未知'}；"
                f"任务={work.trigger_summary}"
            )
            if work.result_summary:
                line += f"；结果={work.result_summary}"
            lines.append(line)

        if any(work.status == "running" for work in other_works):
            lines.append("请勿重复处理其他运行中的工作。")
        return "\n".join(lines)

    def clear_chat(self, chat_id: str) -> None:
        with self._lock:
            self._items.pop(str(chat_id or ""), None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _trim(self, chat_id: str) -> None:
        bucket = self._items.get(chat_id) or {}
        if not bucket:
            return
        running = [w for w in bucket.values() if w.status == "running"]
        finished = [w for w in bucket.values() if w.status != "running"]
        finished.sort(key=lambda w: w.ended_at or w.started_at, reverse=True)
        keep = {w.work_id: w for w in running}
        for w in finished[: self.retain_finished]:
            keep[w.work_id] = w
        self._items[chat_id] = keep
