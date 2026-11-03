"""收口插件 Handler 子任务，以及由消息入口明确登记的事件 pipeline。"""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Awaitable, Callable, TypeVar


_HandlerResult = TypeVar("_HandlerResult")


class RuntimeTaskTracker:
    """追踪 Handler 子任务，只接管消息入口明确拥有的事件 pipeline。"""

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
        # Lifecycle hooks may run inside another plugin's long-lived scheduler.
        # Even an entry handler called recursively from our own child must not
        # promote that child into a separately owned, permanent pipeline lease.
        if (
            pipeline is None
            or pipeline in self._pipelines
            or pipeline in self._children
        ):
            return

        self._pipelines[pipeline] = event

        def _release(done_task: asyncio.Task) -> None:
            self._pipelines.pop(done_task, None)

        pipeline.add_done_callback(_release)

    async def run(
        self,
        event: object | None,
        handler: Callable[[], Awaitable[_HandlerResult]],
        *,
        owns_pipeline: bool = False,
    ) -> _HandlerResult | None:
        """运行 Handler；只有显式消息入口才取得宿主 pipeline 的取消权。"""
        if not self._accepting:
            self._stop_event(event)
            return None

        if owns_pipeline:
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


def track_runtime_handler(
    method: Callable[..., Awaitable[_HandlerResult]] | None = None,
    *,
    owns_pipeline: bool = False,
):
    """隔离 Handler 子任务；消息入口可显式登记其短生命周期 pipeline。

    普通模型、发送和 Agent 完成钩子只借用调用方任务，热更新不能取消
    调用方的长期调度循环。消息入口登记的 pipeline 仍会完整等待退出。
    """

    def decorate(handler_method):
        @wraps(handler_method)
        async def wrapped(self, *args, **kwargs):
            event = args[0] if args else kwargs.get("event")
            return await self._runtime_tasks.run(
                event,
                lambda: handler_method(self, *args, **kwargs),
                owns_pipeline=owns_pipeline,
            )

        return wrapped

    return decorate(method) if method is not None else decorate
