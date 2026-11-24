"""闭嘴要打断进行中的 agent 执行。

闭嘴原先只终止入站事件：命中的那条消息不再往下走，但已经开始生成的执行
照样会继续调工具、继续烧模型。这里钉住两条路径都要向框架发起停止请求，
且当前事件本身不被打断请求波及。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot.core.utils.active_event_registry import active_event_registry

from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

CHAT = "aiocqhttp:GroupMessage:1001"


def _front_desk():
    config = MagicMock()
    config.for_chat.return_value = SimpleNamespace(
        speak_words="", slap_words="闭嘴", silence_duration=60
    )
    context = MagicMock()
    context.silenced_until = {}
    context.debounce_manager.clear_chat = AsyncMock()
    front_desk = FrontDesk(config, context)
    front_desk._check_and_handle_timeout = AsyncMock()
    return front_desk, context


def _event(text: str):
    event = MagicMock()
    event.unified_msg_origin = CHAT
    event.get_message_outline.return_value = text
    return event


def _spy(monkeypatch, returned: int = 1):
    spy = MagicMock(return_value=returned)
    monkeypatch.setattr(
        active_event_registry, "request_agent_stop_all", spy, raising=True
    )
    return spy


@pytest.mark.asyncio
async def test_slap_word_interrupts_running_agent(monkeypatch):
    """掌嘴词进闭嘴：立刻打断正在跑的执行。"""
    front_desk, context = _front_desk()
    spy = _spy(monkeypatch)
    event = _event("你给我闭嘴")

    await front_desk.handle_event(event)

    spy.assert_called_once_with(CHAT, exclude=event)
    assert context.silenced_until[CHAT] > time.time()
    event.stop_event.assert_called_once()


@pytest.mark.asyncio
async def test_message_while_muted_also_interrupts(monkeypatch):
    """已在闭嘴期间收到消息：同样补一次打断，防止漏网的在跑执行。"""
    front_desk, context = _front_desk()
    spy = _spy(monkeypatch)
    context.silenced_until[CHAT] = time.time() + 60
    event = _event("在吗")

    await front_desk.handle_event(event)

    spy.assert_called_once_with(CHAT, exclude=event)
    event.stop_event.assert_called_once()


@pytest.mark.asyncio
async def test_speak_word_does_not_interrupt(monkeypatch):
    """张嘴词解除闭嘴：不打断，正常继续往下处理。"""
    front_desk, context = _front_desk()
    front_desk._config_manager.for_chat.return_value = SimpleNamespace(
        speak_words="说话", slap_words="", silence_duration=60
    )
    spy = _spy(monkeypatch)
    context.silenced_until[CHAT] = time.time() + 60
    event = _event("说话")
    front_desk.cache_message = AsyncMock()
    front_desk._schedule_group_debounce = AsyncMock()

    await front_desk.handle_event(event)

    spy.assert_not_called()
    assert CHAT not in context.silenced_until


def test_helper_survives_missing_registry(monkeypatch):
    """框架没有该模块时只记日志，不把消息处理搞崩。"""
    front_desk, _ = _front_desk()
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "astrbot.core.utils.active_event_registry":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert front_desk._interrupt_running_agent(CHAT, _event("闭嘴")) == 0
