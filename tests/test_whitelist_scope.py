"""群聊白名单作用域测试：白名单只管辖群聊，私聊不受白名单约束。"""

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
    whitelist_enabled: bool = True,
    chat_ids: tuple = ("1",),
) -> AngelHeartPlugin:
    plugin = AngelHeartPlugin.__new__(AngelHeartPlugin)
    plugin.context = SimpleNamespace(get_config=lambda chat_id: {})
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


def test_whitelisted_group_still_processed():
    plugin = _make_plugin()
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._should_process(event) is True


def test_outside_whitelist_group_blocked():
    plugin = _make_plugin(chat_ids=("2",))
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")

    assert plugin._should_process(event) is False


def test_whitelist_disabled_processes_all_groups():
    plugin = _make_plugin(whitelist_enabled=False)
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:9")

    assert plugin._should_process(event) is True


def test_private_chat_not_blocked_by_whitelist():
    """白名单只控制群聊：私聊无论是否在白名单中都可进入插件链路。"""
    plugin = _make_plugin(chat_ids=("2",))
    event = DummyEvent("hello", chat_id="aiocqhttp:FriendMessage:1")

    assert plugin._should_process(event) is True


def test_private_message_origin_not_blocked_by_whitelist():
    """PrivateMessage 形态的私聊同样不受白名单约束。"""
    plugin = _make_plugin(chat_ids=("2",))
    event = DummyEvent("hello", chat_id="aiocqhttp:PrivateMessage:1")

    assert plugin._is_private_chat("aiocqhttp:PrivateMessage:1") is True
    assert plugin._should_process(event) is True


def test_whitelist_block_helper_matrix():
    """白名单拦截判断：只拦非白名单群聊，私聊与关白名单均放行。"""
    plugin = _make_plugin(chat_ids=("2",))

    assert plugin._is_whitelist_blocked("aiocqhttp:GroupMessage:1") is True
    assert plugin._is_whitelist_blocked("aiocqhttp:GroupMessage:2") is False
    assert plugin._is_whitelist_blocked("aiocqhttp:FriendMessage:1") is False
    assert plugin._is_whitelist_blocked("aiocqhttp:PrivateMessage:1") is False

    disabled = _make_plugin(whitelist_enabled=False, chat_ids=("2",))
    assert disabled._is_whitelist_blocked("aiocqhttp:GroupMessage:1") is False


class _BoomWhitelistConfig:
    @property
    def whitelist_enabled(self):
        raise RuntimeError("config read failed")


def test_whitelist_helper_fails_closed_on_config_error():
    """白名单判定异常时按拦截处理，避免配置错误导致越权进入插件链路。"""
    plugin = _make_plugin(chat_ids=("2",))
    plugin.config_manager = _BoomWhitelistConfig()

    assert plugin._is_whitelist_blocked("aiocqhttp:GroupMessage:1") is True


@pytest.mark.asyncio
async def test_blocked_send_hook_finishes_existing_secretary_dispatch():
    """白名单拦截发送后处理时，仍收口本事件已持有的秘书调度。"""
    plugin = _make_plugin(chat_ids=("2",))
    plugin.angel_context = SimpleNamespace(
        debounce_manager=SimpleNamespace(finish_secretary_dispatch=AsyncMock(return_value=True)),
        handle_message_sent=AsyncMock(),
    )
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")
    event.set_extra("angelheart_secretary_dispatch_id", "dispatch-1")

    await plugin.handle_message_sent(event)

    plugin.angel_context.debounce_manager.finish_secretary_dispatch.assert_awaited_once_with(
        "aiocqhttp:GroupMessage:1",
        "dispatch-1",
        cooldown_seconds=0.0,
        reason="whitelist_blocked",
    )
    plugin.angel_context.handle_message_sent.assert_not_called()


@pytest.mark.asyncio
async def test_blocked_send_hook_skips_cleanup_without_dispatch_id():
    """没有调度归属时，白名单拦截发送后处理不会凭空收口。"""
    plugin = _make_plugin(chat_ids=("2",))
    plugin.angel_context = SimpleNamespace(
        debounce_manager=SimpleNamespace(finish_secretary_dispatch=AsyncMock(return_value=True)),
        handle_message_sent=AsyncMock(),
    )
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")

    await plugin.handle_message_sent(event)

    plugin.angel_context.debounce_manager.finish_secretary_dispatch.assert_not_called()
    plugin.angel_context.handle_message_sent.assert_not_called()


@pytest.mark.asyncio
async def test_side_hooks_skip_outside_whitelist():
    """三个旁路钩子对非白名单群聊直接跳过，不再产生插件副作用。"""
    plugin = _make_plugin(chat_ids=("2",))
    plugin.angel_context = SimpleNamespace(
        conversation_ledger=SimpleNamespace(add_messages=AsyncMock()),
        debounce_manager=SimpleNamespace(charge_reply_energy=AsyncMock()),
        work_ledger=SimpleNamespace(complete_work=MagicMock()),
        handle_message_sent=AsyncMock(),
    )
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")

    await plugin.capture_completed_agent_messages(event, MagicMock(), MagicMock())
    await plugin.strip_markdown_on_decorating_result(event)
    await plugin.handle_message_sent(event)

    plugin.angel_context.conversation_ledger.add_messages.assert_not_called()
    plugin.angel_context.debounce_manager.charge_reply_energy.assert_not_called()
    plugin.angel_context.work_ledger.complete_work.assert_not_called()
    plugin.angel_context.handle_message_sent.assert_not_called()


@pytest.mark.asyncio
async def test_rewrite_runs_for_whitelisted_group():
    plugin = _make_plugin()
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")

    await plugin.delegate_prompt_rewriting(event, MagicMock())

    plugin.front_desk.rewrite_prompt_for_llm.assert_awaited_once()


@pytest.mark.asyncio
async def test_rewrite_skips_outside_whitelist():
    plugin = _make_plugin(chat_ids=("2",))
    event = DummyEvent("hello", chat_id="aiocqhttp:GroupMessage:1")

    await plugin.delegate_prompt_rewriting(event, MagicMock())

    plugin.front_desk.rewrite_prompt_for_llm.assert_not_awaited()
