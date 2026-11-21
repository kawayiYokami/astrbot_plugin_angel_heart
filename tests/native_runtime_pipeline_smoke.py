"""Run with installed AstrBot: pytest --noconftest tests/native_runtime_pipeline_smoke.py.

The stages below are synthetic. They never send messages or call providers.
"""

import asyncio
from types import SimpleNamespace

import pytest

import astrbot.api  # Initialize the native API before importing pipeline modules.
from astrbot.core.message.components import Plain
from astrbot.core.pipeline.scheduler import PipelineScheduler
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.platform.astrbot_message import AstrBotMessage, MessageMember
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata

from astrbot_plugin_angel_heart.core.runtime_task_tracker import RuntimeTaskTracker


def event(identity):
    message = AstrBotMessage()
    message.type = MessageType.GROUP_MESSAGE
    message.self_id = "synthetic-bot"
    message.message_id = identity
    message.group_id = "synthetic-group"
    message.sender = MessageMember(user_id="synthetic-user", nickname="Synthetic")
    message.message = [Plain("synthetic command")]
    message.message_str = "synthetic command"
    message.raw_message = {}
    return AstrMessageEvent(
        message.message_str, message,
        PlatformMetadata("test", "native-test", "test"), "synthetic-group",
    )


def scheduler_for(stage):
    scheduler = object.__new__(PipelineScheduler)
    scheduler.stages = [stage]
    return scheduler


@pytest.mark.asyncio
async def test_early_command_hook_claims_and_stops_its_native_event_pipeline():
    tracker = RuntimeTaskTracker()
    current = event("early-command")
    waiting = asyncio.Event()
    continued = asyncio.Event()

    async def process(received):
        await tracker.run(received, lambda: asyncio.sleep(0))
        waiting.set()
        await asyncio.Event().wait()
        continued.set()

    scheduler = scheduler_for(SimpleNamespace(process=process))
    pipeline = asyncio.create_task(scheduler.execute(current))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        assert list(tracker._pipelines) == [pipeline]
        await tracker.stop()
        assert pipeline.done()
        assert current.is_stopped()
        assert not continued.is_set()
        assert tracker._children == {}
        assert tracker._pipelines == {}
    finally:
        pipeline.cancel()
        await asyncio.gather(pipeline, return_exceptions=True)


@pytest.mark.asyncio
async def test_external_scheduler_awaiting_execute_keeps_its_root_task():
    tracker = RuntimeTaskTracker()
    current = event("external")
    waiting = asyncio.Event()
    release = asyncio.Event()
    continued = asyncio.Event()

    async def process(received):
        await tracker.run(received, lambda: asyncio.sleep(0))
        waiting.set()
        await release.wait()

    scheduler = scheduler_for(SimpleNamespace(process=process))

    async def external_loop():
        await scheduler.execute(current)
        continued.set()
        await asyncio.Event().wait()

    external = asyncio.create_task(external_loop())
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        assert tracker._pipelines == {}
        await tracker.stop()
        assert not external.done()
        assert not current.is_stopped()
        release.set()
        await asyncio.wait_for(continued.wait(), 2)
    finally:
        external.cancel()
        await asyncio.gather(external, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_root_cannot_claim_a_hook_for_a_different_event():
    tracker = RuntimeTaskTracker()
    current = event("root")
    other = event("other")
    waiting = asyncio.Event()

    async def process(_received):
        await tracker.run(other, lambda: asyncio.sleep(0))
        waiting.set()
        await asyncio.Event().wait()

    scheduler = scheduler_for(SimpleNamespace(process=process))
    pipeline = asyncio.create_task(scheduler.execute(current))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        assert tracker._pipelines == {}
        await tracker.stop()
        assert not pipeline.done()
        assert not current.is_stopped()
        assert not other.is_stopped()
    finally:
        pipeline.cancel()
        await asyncio.gather(pipeline, return_exceptions=True)
