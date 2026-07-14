"""插件 Handler 调用与所属事件 pipeline 的生命周期收口。"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Awaitable, Callable, TypeVar


_HandlerResult = TypeVar("_HandlerResult")


class RuntimeTaskTracker:
    """以独立子任务运行 Handler，并等待其所属事件 pipeline 完整退出。"""

    def __init__(self) -> None:
        self._accepting = True
        self._children: dict[asyncio.Task, object] = {}
        self._pipelines: dict[asyncio.Task, object] = {}

    @staticmethod
    def _stop_event(event: object | None) -> None:
        if event is None:
            return
        try:
            event.stop_event()
        except Exception:
            pass

    def _lease_pipeline(self, event: object | None) -> None:
        pipeline = asyncio.current_task()
        if pipeline is None or pipeline in self._pipelines:
            return

        self._pipelines[pipeline] = event

        def _release(done_task: asyncio.Task) -> None:
            self._pipelines.pop(done_task, None)

        pipeline.add_done_callback(_release)

    async def run(
        self,
        event: object | None,
        handler: Callable[[], Awaitable[_HandlerResult]],
    ) -> _HandlerResult | None:
        """登记事件 pipeline，并在独立子任务中执行一次 Handler。"""
        if not self._accepting:
            self._stop_event(event)
            return None

        self._lease_pipeline(event)
        child = asyncio.create_task(handler())
        self._children[child] = event
        try:
            return await child
        except asyncio.CancelledError:
            if not self._accepting:
                self._stop_event(event)
                return None
            raise
        finally:
            self._children.pop(child, None)

    async def stop(self) -> None:
        """拒绝新 Handler，停止事件，取消子任务并等待旧 pipeline 退出。"""
        self._accepting = False
        current = asyncio.current_task()
        children = [task for task in self._children if not task.done()]
        pipelines = [
            task
            for task in self._pipelines
            if task is not current and not task.done()
        ]

        events = {
            event
            for event in (*self._children.values(), *self._pipelines.values())
            if event is not None
        }
        for event in events:
            self._stop_event(event)

        for task in children:
            task.cancel()
        for task in pipelines:
            task.cancel()

        tasks = [*children, *pipelines]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._children.clear()
        self._pipelines.clear()


def track_runtime_handler(method: Callable[..., Awaitable[_HandlerResult]]):
    """让 AstrBot Handler 使用独立子任务并登记所属事件 pipeline。"""

    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        event = args[0] if args else kwargs.get("event")
        return await self._runtime_tasks.run(
            event,
            lambda: method(self, *args, **kwargs),
        )

    return wrapped
