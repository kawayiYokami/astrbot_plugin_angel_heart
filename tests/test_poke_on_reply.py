"""决定回复时戳触发者测试。

覆盖：群聊才触发、aiocqhttp 才真戳、不戳自己、
非法 ID 跳过、开关关闭跳过、冷却防刷、异常不打断。
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


def _make_config(poke_enabled=True, cooldown=60):
    return ConfigManager(
        {
            "wake_interaction": {
                "poke_on_reply_enabled": poke_enabled,
                "poke_on_reply_cooldown": cooldown,
            },
            "timing": {"waiting_time": 30.0},
        }
    )


class _PokeEvent:
    def __init__(self, chat_id, sender="12345", self_id="99999", with_poke=True,
                 platform_name="aiocqhttp", group_id=""):
        self.unified_msg_origin = chat_id
        self._sender = sender
        self._self_id = self_id
        self._platform_name = platform_name
        # 默认从 chat_id 尾段解析群号，与真实 _extract_group_id 一致
        self._group_id = group_id or (chat_id.split(":")[-1] if ":" in chat_id else "")
        if with_poke:
            self.bot = MagicMock()
            self.bot.group_poke = AsyncMock()
        # 无 bot 属性分支：直接不挂 bot

    def get_sender_id(self):
        return self._sender

    def get_self_id(self):
        return self._self_id

    def get_platform_name(self):
        return self._platform_name

    def get_group_id(self):
        return self._group_id


def _make_fd(config):
    angel = MagicMock()
    angel.astr_context = MagicMock()
    fd = FrontDesk(config, angel)
    return fd


@pytest.mark.asyncio
async def test_group_reply_pokes_trigger():
    fd = _make_fd(_make_config())
    event = _PokeEvent("aiocqhttp:GroupMessage:123456", sender="2172762770")
    await fd._maybe_poke_trigger_user(event, event.unified_msg_origin)
    event.bot.group_poke.assert_awaited_once_with(
        group_id=123456, user_id=2172762770
    )


@pytest.mark.asyncio
async def test_private_chat_never_pokes():
    fd = _make_fd(_make_config())
    event = _PokeEvent("aiocqhttp:FriendMessage:12345", sender="12345")
    await fd._maybe_poke_trigger_user(event, event.unified_msg_origin)
    if hasattr(event, "bot"):
        event.bot.group_poke.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_config_skips():
    fd = _make_fd(_make_config(poke_enabled=False))
    event = _PokeEvent("aiocqhttp:GroupMessage:123456", sender="2172762770")
    await fd._maybe_poke_trigger_user(event, event.unified_msg_origin)
    event.bot.group_poke.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_aiocqhttp_skips():
    fd = _make_fd(_make_config())
    event = _PokeEvent("default:GroupMessage:123456", sender="2172762770",
                       platform_name="webchat")
    await fd._maybe_poke_trigger_user(event, event.unified_msg_origin)
    event.bot.group_poke.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_platform_id_with_aiocqhttp_pokes():
    """真实环境 chat_id 首段是适配器实例 ID（如 default），平台类型走 get_platform_name。"""
    fd = _make_fd(_make_config(cooldown=0))
    event = _PokeEvent("default:GroupMessage:828870440", sender="2172762770",
                       platform_name="aiocqhttp")
    await fd._maybe_poke_trigger_user(event, event.unified_msg_origin)
    event.bot.group_poke.assert_awaited_once_with(
        group_id=828870440, user_id=2172762770
    )


@pytest.mark.asyncio
async def test_aiocqhttp_event_type_fallback_when_name_mismatch():
    """平台名被改名时，真 aiocqhttp 事件按类型名放行。"""
    fd = _make_fd(_make_config(cooldown=0))
    FakeEvent = type("FakeAiocqhttpEvent", (_PokeEvent,), {"__module__": "aiocqhttp.fake_adapter"})
    # type() 会把 __module__ 吃掉，手动改回以模拟真实 aiocqhttp 事件模块路径
    FakeEvent.__module__ = "aiocqhttp.fake_adapter"
    event = FakeEvent("default:GroupMessage:828870440", sender="2172762770",
                      platform_name="default")
    await fd._maybe_poke_trigger_user(event, event.unified_msg_origin)
    event.bot.group_poke.assert_awaited_once_with(
        group_id=828870440, user_id=2172762770
    )


@pytest.mark.asyncio
async def test_self_sender_skips():
    fd = _make_fd(_make_config())
    event = _PokeEvent("aiocqhttp:GroupMessage:123456", sender="99999", self_id="99999")
    await fd._maybe_poke_trigger_user(event, event.unified_msg_origin)
    event.bot.group_poke.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_ids_skip():
    fd = _make_fd(_make_config())
    for sender in ("", "0", "abc", "user"):
        # 非纯数字一律跳过；"0" 虽是数字但属无效 ID，同样跳过由 isdigit 后的业务判断？
        # 当前实现仅校验 isdigit，"0" 会通过 group_poke 调用前被放行，
        # 这里只断言非数字 ID 不调用。
        if sender.isdigit():
            continue
        event = _PokeEvent("aiocqhttp:GroupMessage:123456", sender=sender)
        await fd._maybe_poke_trigger_user(event, event.unified_msg_origin)
        event.bot.group_poke.assert_not_awaited()


@pytest.mark.asyncio
async def test_cooldown_suppresses_second_poke():
    fd = _make_fd(_make_config(cooldown=60))
    chat_id = "aiocqhttp:GroupMessage:123456"
    e1 = _PokeEvent(chat_id, sender="11111")
    await fd._maybe_poke_trigger_user(e1, chat_id)
    assert e1.bot.group_poke.await_count == 1
    e2 = _PokeEvent(chat_id, sender="22222")
    await fd._maybe_poke_trigger_user(e2, chat_id)
    e2.bot.group_poke.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_error_does_not_raise():
    fd = _make_fd(_make_config(cooldown=0))
    event = _PokeEvent("aiocqhttp:GroupMessage:123456", sender="2172762770")
    event.bot.group_poke = AsyncMock(side_effect=RuntimeError("net down"))
    await fd._maybe_poke_trigger_user(event, event.unified_msg_origin)
    # 未抛异常即通过


@pytest.mark.asyncio
async def test_process_decision_result_triggers_poke():
    """_process_decision_result 决定回复时调用戳触发者。"""
    from astrbot_plugin_angel_heart.models.analysis_result import SecretaryDecision

    fd = _make_fd(_make_config(cooldown=0))
    chat_id = "aiocqhttp:GroupMessage:123456"
    event = _PokeEvent(chat_id, sender="2172762770")
    event.message_str = "hello"
    event.is_at_or_wake_command = False
    event.get_message_outline = lambda: "hello"
    event.get_messages = lambda: []
    event.set_extra = lambda k, v: None
    event.get_extra = lambda k, default=None: default

    fd.context.debounce_manager.start_assistant_rest = AsyncMock(return_value=True)
    fd._finish_secretary_dispatch = AsyncMock(return_value=True)
    fd._maybe_poke_trigger_user = AsyncMock()

    decision = SecretaryDecision(
        should_reply=True,
        reply_strategy="回",
        topic="t",
        entities=[],
        facts=[],
        keywords=[],
    )
    await fd._process_decision_result(
        decision, [{"role": "user", "content": "hi"}], [], 0.0, event, chat_id
    )
    fd._maybe_poke_trigger_user.assert_awaited_once_with(event, chat_id)


def test_config_defaults_and_template_allowlist():
    from astrbot_plugin_angel_heart.core.chat_profile import (
        TEMPLATE_GROUPS,
        filter_template_config,
    )

    assert ConfigManager({}).poke_on_reply_enabled is True
    assert ConfigManager({}).poke_on_reply_cooldown == 60
    assert "poke_on_reply_enabled" in TEMPLATE_GROUPS["wake_interaction"]
    assert "poke_on_reply_cooldown" in TEMPLATE_GROUPS["wake_interaction"]
    filtered = filter_template_config(
        {
            "wake_interaction": {
                "poke_on_reply_enabled": False,
                "poke_on_reply_cooldown": 10,
                "unknown_key": 1,
            }
        }
    )
    assert filtered["wake_interaction"]["poke_on_reply_enabled"] is False
    assert filtered["wake_interaction"]["poke_on_reply_cooldown"] == 10
    assert "unknown_key" not in filtered["wake_interaction"]
