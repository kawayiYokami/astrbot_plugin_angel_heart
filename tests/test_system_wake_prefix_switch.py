"""系统级唤醒词开关边界测试。

开关关闭：沿用现有行为，不把系统级前缀消息当点名唤醒。
开关开启：消息以 AstrBot wake_prefix 开头时等价点名唤醒。
白名单仍先于前缀判定：白名单外群聊完全不受影响。
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

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
components_mod.Plain = type("Plain", (), {})
components_mod.At = type("At", (), {})
components_mod.Reply = type("Reply", (), {})

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

    def get_result(self):
        return self._result

    def get_timestamp(self):
        return time.time()


def _make_plugin(
    *,
    enable_system_wake_prefix: bool = False,
    provider_wake_prefix: str | list[str] = "/",
    extra_chat_wake_prefix: str = "",
    whitelist_enabled: bool = False,
    chat_ids: tuple = ("1",),
) -> AngelHeartPlugin:
    plugin = AngelHeartPlugin.__new__(AngelHeartPlugin)
    plugin.context = SimpleNamespace(
        get_config=lambda chat_id: {
            "wake_prefix": (
                provider_wake_prefix
                if isinstance(provider_wake_prefix, list)
                else [provider_wake_prefix]
            ),
            "provider_settings": {"wake_prefix": extra_chat_wake_prefix},
        }
    )
    plugin.config_manager = SimpleNamespace(
        enable_system_wake_prefix=enable_system_wake_prefix,
        whitelist_enabled=whitelist_enabled,
        chat_ids=list(chat_ids),
        takeover_private_chat_context=False,
        group_chat_enhancement=True,
    )
    plugin._whitelist_cache = {str(cid) for cid in plugin.config_manager.chat_ids}
    plugin._runtime_tasks = RuntimeTaskTracker()
    return plugin


def test_switch_off_does_not_mark_prefix_event():
    plugin = _make_plugin(enable_system_wake_prefix=False, provider_wake_prefix="/")
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._is_provider_wake_prefix_event(event) is False
    assert plugin._should_process(event) is True
    assert event.get_extra("angelheart_provider_wake_prefix") is None


def test_switch_on_marks_prefix_event_as_wake():
    plugin = _make_plugin(enable_system_wake_prefix=True, provider_wake_prefix="/")
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._is_provider_wake_prefix_event(event) is True
    assert plugin._should_process(event) is True
    assert event.get_extra("angelheart_provider_wake_prefix") is True


def test_switch_on_supports_arbitrary_prefix():
    plugin = _make_plugin(enable_system_wake_prefix=True, provider_wake_prefix="bot")
    event = DummyEvent("bot 你好", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._is_provider_wake_prefix_event(event) is True
    assert plugin._should_process(event) is True
    assert event.get_extra("angelheart_provider_wake_prefix") is True


def test_switch_on_supports_multiple_prefixes():
    plugin = _make_plugin(
        enable_system_wake_prefix=True,
        provider_wake_prefix=["/", "bot"],
    )
    event = DummyEvent("bot 帮我看下", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._is_provider_wake_prefix_event(event) is True
    assert plugin._should_process(event) is True


def test_reads_top_level_wake_prefix_not_provider_settings():
    """系统级唤醒词读取顶层 wake_prefix，不误用 provider_settings.wake_prefix。"""
    plugin = AngelHeartPlugin.__new__(AngelHeartPlugin)
    plugin.context = SimpleNamespace(
        get_config=lambda chat_id: {
            "wake_prefix": ["/"],
        }
    )
    plugin.config_manager = SimpleNamespace(
        enable_system_wake_prefix=True,
        whitelist_enabled=False,
        chat_ids=["1"],
        takeover_private_chat_context=False,
        group_chat_enhancement=True,
    )
    plugin._whitelist_cache = {"1"}
    plugin._runtime_tasks = RuntimeTaskTracker()

    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")
    assert plugin._is_provider_wake_prefix_event(event) is True

    # 顶层为空时不回退 provider_settings.wake_prefix
    plugin.context = SimpleNamespace(
        get_config=lambda chat_id: {
            "wake_prefix": [],
            "provider_settings": {"wake_prefix": "bot"},
        }
    )
    event2 = DummyEvent("bot hi", chat_id="aiocqhttp:GroupMessage:1")
    assert plugin._is_provider_wake_prefix_event(event2) is False


def test_switch_on_requires_wake_and_configured_prefix():
    plugin = _make_plugin(enable_system_wake_prefix=True, provider_wake_prefix="/")

    not_woken = DummyEvent(
        "/hello",
        chat_id="aiocqhttp:GroupMessage:1",
        is_at_or_wake_command=False,
    )
    assert plugin._is_provider_wake_prefix_event(not_woken) is False

    empty_prefix = _make_plugin(enable_system_wake_prefix=True, provider_wake_prefix="")
    woken = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")
    assert empty_prefix._is_provider_wake_prefix_event(woken) is False


def test_switch_on_strips_leading_whitespace_like_astrbot():
    plugin = _make_plugin(enable_system_wake_prefix=True, provider_wake_prefix="/")
    event = DummyEvent(" /hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._is_provider_wake_prefix_event(event) is True


def test_system_prefix_is_not_blocked_by_extra_chat_prefix():
    """命中系统级前缀后，不再被 provider_settings.wake_prefix 拦截。"""
    plugin = _make_plugin(
        enable_system_wake_prefix=True,
        provider_wake_prefix="/",
        extra_chat_wake_prefix="bot",
    )
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._is_provider_wake_prefix_event(event) is True
    assert plugin._should_process(event) is True
    assert event.get_extra("angelheart_provider_wake_prefix") is True
    assert event.get_extra("angelheart_blocked_by_provider_wake_prefix") is False


def test_outside_whitelist_group_system_prefix_not_processed():
    """白名单外群聊即使命中系统级前缀，也不进入插件。"""
    plugin = _make_plugin(
        enable_system_wake_prefix=True,
        provider_wake_prefix="/",
        whitelist_enabled=True,
        chat_ids=("2",),
    )
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._should_process(event) is False
    assert event.get_extra("angelheart_provider_wake_prefix") is None


def test_whitelisted_group_system_prefix_still_processed():
    plugin = _make_plugin(
        enable_system_wake_prefix=True,
        provider_wake_prefix="/",
        whitelist_enabled=True,
        chat_ids=("1",),
    )
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._should_process(event) is True
    assert event.get_extra("angelheart_provider_wake_prefix") is True


def test_status_checker_recognizes_provider_wake_flag():
    plugin = _make_plugin(enable_system_wake_prefix=True)
    angel_context = SimpleNamespace(silenced_until={})
    checker = StatusChecker(plugin.config_manager, angel_context)
    event = DummyEvent("/hello", chat_id="aiocqhttp:GroupMessage:1")
    event.set_extra("angelheart_provider_wake_prefix", True)

    assert checker.is_event_wake(event) is True
