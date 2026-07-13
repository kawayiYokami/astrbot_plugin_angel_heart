"""插件 Handler 运行任务登记与终止收口。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps
from typing import AsyncIterator, Callable, TypeVar


_HandlerResult = TypeVar("_HandlerResult")


class RuntimeTaskTracker:
    """登记插件在途 Handler；停止时拒绝新任务并取消等待旧任务。"""

    def __init__(self) -> None:
        self._accepting = True
        self._tasks: set[asyncio.Task] = set()

    @asynccontextmanager
    async def track(self) -> AsyncIterator[bool]:
        if not self._accepting:
            yield False
            return

        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            yield True
        finally:
            if task is not None:
                self._tasks.discard(task)

    async def stop(self) -> None:
        """拒绝新 Handler，取消并等待当前全部在途 Handler。"""
        self._accepting = False
        current = asyncio.current_task()
        tasks = [
            task
            for task in self._tasks
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


def track_runtime_handler(method: Callable[..., _HandlerResult]):
    """让 AstrBot Handler 自动登记到所属插件实例的运行任务表。"""

    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        async with self._runtime_tasks.track() as accepted:
            if not accepted:
                return None
            return await method(self, *args, **kwargs)

    return wrapped
