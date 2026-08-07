"""系统级 LLM 唤醒前缀（如 "/"）等价点名唤醒的边界测试。"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

HERE = Path(__file__).resolve().parent
_PARENT = str(HERE.parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

for _mod_path in (
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.api.provider",
    "astrbot.api.star",
    "astrbot.core",
    "astrbot.core.agent",
    "astrbot.core.agent.message",
    "astrbot.core.message",
    "astrbot.core.message.components",
    "astrbot.core.star",
    "astrbot.core.star.context",
    "astrbot.core.star.filter",
    "astrbot.core.star.filter.command",
    "astrbot.core.star.filter.command_group",
    "astrbot.core.star.register",
    "astrbot.core.star.star_tools",
):
    sys.modules.setdefault(_mod_path, types.ModuleType(_mod_path))

astrbot_api = sys.modules["astrbot.api"]
astrbot_api.logger = MagicMock()
astrbot_api.FunctionTool = type("FunctionTool", (), {})

event_mod = sys.modules["astrbot.api.event"]
event_mod.AstrMessageEvent = type("AstrMessageEvent", (), {})


class EventMessageType:
    GROUP_MESSAGE = 1
    PRIVATE_MESSAGE = 2
    OTHER_MESSAGE = 4


def _passthrough(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


event_mod.EventMessageType = EventMessageType
event_mod.filter = SimpleNamespace(
    EventMessageType=EventMessageType,
    event_message_type=_passthrough,
    on_llm_request=_passthrough,
    on_decorating_result=_passthrough,
    after_message_sent=_passthrough,
)

provider_mod = sys.modules["astrbot.api.provider"]
provider_mod.ProviderRequest = type("ProviderRequest", (), {})
provider_mod.LLMResponse = type("LLMResponse", (), {})

star_mod = sys.modules["astrbot.api.star"]
star_mod.Star = type("Star", (), {})
star_mod.Context = type("Context", (), {})
star_mod.register = _passthrough

star_context_mod = sys.modules["astrbot.core.star.context"]
star_context_mod.Context = type("Context", (), {})

components_mod = sys.modules["astrbot.core.message.components"]
components_mod.AtAll = type("AtAll", (), {})

star_register_mod = sys.modules["astrbot.core.star.register"]
star_register_mod.register_on_agent_done = _passthrough

star_tools_mod = sys.modules["astrbot.core.star.star_tools"]
star_tools_mod.StarTools = type(
    "StarTools",
    (),
    {"get_data_dir": staticmethod(lambda name: str(HERE / "tmp_data"))},
)

command_mod = sys.modules["astrbot.core.star.filter.command"]
command_mod.CommandFilter = type("CommandFilter", (), {})

command_group_mod = sys.modules["astrbot.core.star.filter.command_group"]
command_group_mod.CommandGroupFilter = type("CommandGroupFilter", (), {})

from astrbot_plugin_angel_heart.core.runtime_task_tracker import RuntimeTaskTracker
from astrbot_plugin_angel_heart.core.angel_heart_status import StatusChecker
from astrbot_plugin_angel_heart.main import AngelHeartPlugin


class DummyEvent:
    def __init__(
        self,
        message_outline: str,
        chat_id: str = "aiocqhttp:GroupMessage:1",
        is_at_or_wake_command: bool = True,
        activated_handlers: list | None = None,
    ):
        self.unified_msg_origin = chat_id
        self.extras = {"activated_handlers": activated_handlers or []}
        self.is_at_or_wake_command = is_at_or_wake_command
        self._outline = message_outline
        self._result = MagicMock()

    def get_message_outline(self):
        return self._outline

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_sender_id(self):
        return "u1"

    def get_sender_name(self):
        return "user1"

    def get_self_id(self):
        return "bot1"

    def get_messages(self):
        return []

    def get_timestamp(self):
        return time.time()


def _make_plugin(
    provider_wake_prefix: str = "/",
    whitelist_enabled: bool = True,
    chat_ids: tuple = ("1",),
) -> AngelHeartPlugin:
    plugin = AngelHeartPlugin.__new__(AngelHeartPlugin)
    plugin.context = SimpleNamespace(
        get_config=lambda chat_id: {
            "provider_settings": {"wake_prefix": provider_wake_prefix}
        }
    )
    plugin.config_manager = SimpleNamespace(
        whitelist_enabled=whitelist_enabled,
        chat_ids=list(chat_ids),
        takeover_private_chat_context=False,
        group_chat_enhancement=True,
    )
    plugin._whitelist_cache = {str(cid) for cid in plugin.config_manager.chat_ids}
    plugin._runtime_tasks = RuntimeTaskTracker()
    plugin.front_desk = SimpleNamespace(rewrite_prompt_for_llm=AsyncMock())
    return plugin


def test_provider_prefix_wakes_in_whitelisted_group():
    plugin = _make_plugin()
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._is_provider_wake_prefix_event(event) is True
    assert plugin._should_process(event) is True
    assert event.get_extra("angelheart_provider_wake_prefix") is True


def test_provider_prefix_wakes_outside_whitelist():
    plugin = _make_plugin(chat_ids=("2",))
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._should_process(event) is True


def test_provider_prefix_normalizes_leading_whitespace_like_astrbot():
    """AstrBot 唤醒检查先 strip 再匹配前缀，前导空白后的 / 同样视为系统级唤醒。"""
    plugin = _make_plugin(chat_ids=("2",))
    event = DummyEvent(" /hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._is_provider_wake_prefix_event(event) is True
    assert plugin._should_process(event) is True


def test_provider_prefix_requires_wake_and_configured_prefix():
    plugin = _make_plugin()

    not_woken = DummyEvent(
        "/hello",
        chat_id="aiocqhttp:GroupMessage:1",
        is_at_or_wake_command=False,
    )
    assert plugin._is_provider_wake_prefix_event(not_woken) is False

    no_prefix_config = _make_plugin(provider_wake_prefix="")
    woken = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")
    assert no_prefix_config._is_provider_wake_prefix_event(woken) is False


def test_non_prefix_wake_still_processed_in_whitelist():
    plugin = _make_plugin()
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._is_provider_wake_prefix_event(event) is False
    assert plugin._should_process(event) is True


def test_non_prefix_wake_blocked_by_whitelist():
    plugin = _make_plugin(chat_ids=("2",))
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._should_process(event) is False


@pytest.mark.asyncio
async def test_rewrite_runs_for_provider_prefix():
    plugin = _make_plugin()
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")

    await plugin.delegate_prompt_rewriting(event, MagicMock())

    plugin.front_desk.rewrite_prompt_for_llm.assert_awaited_once()


@pytest.mark.asyncio
async def test_rewrite_still_runs_for_normal_wake():
    plugin = _make_plugin()
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")

    await plugin.delegate_prompt_rewriting(event, MagicMock())

    plugin.front_desk.rewrite_prompt_for_llm.assert_awaited_once()


def test_status_checker_recognizes_provider_wake_flag():
    plugin = _make_plugin()
    angel_context = SimpleNamespace(silenced_until={})
    checker = StatusChecker(plugin.config_manager, angel_context)
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")
    event.set_extra("angelheart_provider_wake_prefix", True)

    assert checker.is_event_wake(event) is True
