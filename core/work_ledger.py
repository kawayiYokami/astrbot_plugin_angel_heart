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

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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
    """按会话维护工作账本。"""

    def __init__(self, retain_finished: int = 8):
        self._items: Dict[str, Dict[str, WorkItem]] = {}
        self.retain_finished = max(1, int(retain_finished))

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
        )
        bucket = self._items.setdefault(chat_id, {})
        bucket[work_id] = item
        self._trim(chat_id)
        return item

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
        bucket = self._items.get(chat_id) or {}
        item = bucket.get(work_id)
        if not item:
            return None
        item.status = status if status in ("done", "failed", "running") else "done"
        item.result_summary = (result_summary or "").strip()
        item.ended_at = time.time()
        self._trim(chat_id)
        return item

    def get_active_works(self, chat_id: str) -> List[WorkItem]:
        bucket = self._items.get(str(chat_id or "")) or {}
        return [w for w in bucket.values() if w.status == "running"]

    def get_recent_works(self, chat_id: str, limit: int = 8) -> List[WorkItem]:
        bucket = self._items.get(str(chat_id or "")) or {}
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
        """第二人称：给助理临时注入。

        本轮任务：说明「这是您本轮任务」，不说「请不要重复回答」。
        勿重复只针对其他 running / 已完成工作。
        """
        works = self.get_recent_works(chat_id)
        if not works:
            return "工作提醒：当前没有其他已登记工作。"

        lines = ["工作提醒："]
        current = None
        others_running = []
        for w in works:
            if current_work_id and w.work_id == current_work_id:
                current = w
            elif w.status == "running":
                others_running.append(w)

        if current and current.status == "running":
            lines.append(
                f"这是您本轮任务：「{current.trigger_summary}」"
                f"（触发锚点={current.trigger_message_id or '未知'}）。请正常回答。"
            )
        elif current:
            lines.append(
                f"您本轮对应工作「{current.trigger_summary}」状态为 {current.status}。"
            )

        if others_running:
            for w in others_running:
                lines.append(
                    f"另有工作运行中：「{w.trigger_summary}」"
                    f"（触发锚点={w.trigger_message_id or '未知'}）。请勿重复回答同一套问题。"
                )
        elif not current:
            latest = works[0]
            status_cn = {
                "running": "运行中",
                "done": "已完成",
                "failed": "失败",
            }.get(latest.status, latest.status)
            lines.append(
                f"最近工作[{status_cn}]：「{latest.trigger_summary}」。请勿重复回答已处理内容。"
            )

        return "\n".join(lines)

    def clear_chat(self, chat_id: str) -> None:
        self._items.pop(str(chat_id or ""), None)

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
