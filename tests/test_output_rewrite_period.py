"""句末句号清理（换行符之前的中文句号）测试。"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
_PARENT = str(PLUGIN_ROOT.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

for _mod_path in (
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.api.star",
    "astrbot.api.provider",
    "astrbot.core",
    "astrbot.core.agent",
    "astrbot.core.agent.message",
    "astrbot.core.message",
    "astrbot.core.message.components",
    "astrbot.core.star",
    "astrbot.core.star.context",
    "astrbot.core.star.register",
    "astrbot.core.star.star_tools",
    "astrbot.core.star.filter",
    "astrbot.core.star.filter.command",
    "astrbot.core.star.filter.command_group",
):
    sys.modules.setdefault(_mod_path, types.ModuleType(_mod_path))

sys.modules["astrbot.api"].logger = MagicMock()
sys.modules["astrbot.api.event"].MessageChain = MagicMock
sys.modules["astrbot.core.message.components"].Plain = type("Plain", (), {})
sys.modules["astrbot.core.message.components"].At = type("At", (), {})
sys.modules["astrbot.core.message.components"].AtAll = type("AtAll", (), {})
sys.modules["astrbot.core.message.components"].Reply = type("Reply", (), {})
sys.modules["astrbot.core.star.context"].Context = type("Context", (), {})

# main.py 依赖的 AstrBot 类桩
sys.modules["astrbot.api.star"].Star = type("Star", (), {"__init__": lambda self, ctx: None})
sys.modules["astrbot.api.star"].Context = type("Context", (), {})
sys.modules["astrbot.api.star"].register = lambda *a, **k: (lambda f: f)
sys.modules["astrbot.api.event"].filter = type(
    "Filter",
    (),
    {
        "EventMessageType": type(
            "EventMessageType",
            (),
            {
                "GROUP_MESSAGE": 1,
                "PRIVATE_MESSAGE": 2,
            },
        ),
        "event_message_type": lambda *a, **k: (lambda f: f),
        "on_llm_request": lambda *a, **k: (lambda f: f),
        "on_decorating_result": lambda *a, **k: (lambda f: f),
        "after_message_sent": lambda *a, **k: (lambda f: f),
    },
)()
sys.modules["astrbot.api.provider"].ProviderRequest = type("ProviderRequest", (), {})
sys.modules["astrbot.api.provider"].LLMResponse = type("LLMResponse", (), {})
sys.modules["astrbot.core.star.register"].register_on_agent_done = lambda *a, **k: (lambda f: f)
sys.modules["astrbot.core.star.star_tools"].StarTools = type(
    "StarTools", (), {"get_data_dir": staticmethod(lambda name: PLUGIN_ROOT)}
)
sys.modules["astrbot.core.star.filter.command"].CommandFilter = type("CommandFilter", (), {})
sys.modules["astrbot.core.star.filter.command_group"].CommandGroupFilter = type(
    "CommandGroupFilter", (), {}
)


class _Plain:
    """带 text 属性的 Plain 桩，供 hook 测试构造消息链。"""

    def __init__(self, text: str = ""):
        self.text = text


sys.modules["astrbot.core.message.components"].Plain = _Plain
sys.modules["astrbot.core.message.components"].File = type(
    "File", (), {"__init__": lambda self, name="", file="", url="": None}
)
sys.modules["astrbot.core.message.components"].Image = type("Image", (), {})

from astrbot_plugin_angel_heart.core.utils import strip_period_before_newline


class TestStripPeriodBeforeNewline:
    def test_removes_period_before_lf(self):
        assert strip_period_before_newline("你好。\n明天见。") == "你好\n明天见。"

    def test_removes_period_before_crlf(self):
        assert strip_period_before_newline("第一行。\r\n第二行。") == "第一行\r\n第二行。"

    def test_keeps_period_not_before_newline(self):
        # 句号后不是换行符，保留
        assert strip_period_before_newline("你好。") == "你好。"
        assert strip_period_before_newline("你好。明天见。") == "你好。明天见。"

    def test_keeps_other_punctuation(self):
        assert strip_period_before_newline("你好！\n明天见。") == "你好！\n明天见。"

    def test_empty_text(self):
        assert strip_period_before_newline("") == ""

    def test_multiple_lines(self):
        text = "第一。\n第二。\n第三。"
        assert strip_period_before_newline(text) == "第一\n第二\n第三。"


class TestHookStripsPeriodBeforeNewline:
    @pytest.mark.asyncio
    async def test_hook_cleans_period_when_enabled(self):
        from astrbot.core.message.components import Plain
        from astrbot_plugin_angel_heart.main import AngelHeartPlugin

        plugin = object.__new__(AngelHeartPlugin)

        class _FakeRuntimeTasks:
            async def run(self, event, fn):
                return await fn()

        plugin._runtime_tasks = _FakeRuntimeTasks()
        plugin.config_manager = MagicMock()
        plugin.config_manager.strip_period_before_newline = True
        plugin.config_manager.strip_markdown_enabled = False
        plugin._is_upstream_command_event = MagicMock(return_value=False)
        plugin._is_astrbot_error_message = MagicMock(return_value=False)
        plugin.angel_context = MagicMock()
        plugin.angel_context.debounce_manager.charge_reply_energy = AsyncMock()

        event = MagicMock()
        event.unified_msg_origin = "aiocqhttp:GroupMessage:1"
        result = MagicMock()
        result.chain = [Plain(text="你好。\n明天见。")]
        event.get_result.return_value = result

        await plugin.strip_markdown_on_decorating_result(event)

        assert result.chain[0].text == "你好\n明天见。"

    @pytest.mark.asyncio
    async def test_hook_keeps_period_when_disabled(self):
        from astrbot.core.message.components import Plain
        from astrbot_plugin_angel_heart.main import AngelHeartPlugin

        plugin = object.__new__(AngelHeartPlugin)

        class _FakeRuntimeTasks:
            async def run(self, event, fn):
                return await fn()

        plugin._runtime_tasks = _FakeRuntimeTasks()
        plugin.config_manager = MagicMock()
        plugin.config_manager.strip_period_before_newline = False
        plugin.config_manager.strip_markdown_enabled = False
        plugin._is_upstream_command_event = MagicMock(return_value=False)
        plugin._is_astrbot_error_message = MagicMock(return_value=False)
        plugin.angel_context = MagicMock()
        plugin.angel_context.debounce_manager.charge_reply_energy = AsyncMock()

        event = MagicMock()
        event.unified_msg_origin = "aiocqhttp:GroupMessage:1"
        result = MagicMock()
        result.chain = [Plain(text="你好。\n明天见。")]
        event.get_result.return_value = result

        await plugin.strip_markdown_on_decorating_result(event)

        assert result.chain[0].text == "你好。\n明天见。"

    @pytest.mark.asyncio
    async def test_hook_skips_upstream_command(self):
        from astrbot.core.message.components import Plain
        from astrbot_plugin_angel_heart.main import AngelHeartPlugin

        plugin = object.__new__(AngelHeartPlugin)

        class _FakeRuntimeTasks:
            async def run(self, event, fn):
                return await fn()

        plugin._runtime_tasks = _FakeRuntimeTasks()
        plugin.config_manager = MagicMock()
        plugin.config_manager.strip_period_before_newline = True
        plugin._is_upstream_command_event = MagicMock(return_value=True)
        plugin.angel_context = MagicMock()
        plugin.angel_context.debounce_manager.charge_reply_energy = AsyncMock()

        event = MagicMock()
        event.unified_msg_origin = "aiocqhttp:GroupMessage:1"
        result = MagicMock()
        result.chain = [Plain(text="你好。\n明天见。")]
        event.get_result.return_value = result

        await plugin.strip_markdown_on_decorating_result(event)

        # 上游指令事件直接返回，不清理
        assert result.chain[0].text == "你好。\n明天见。"
        plugin.angel_context.debounce_manager.charge_reply_energy.assert_not_awaited()


class TestStreamingSendState:
    @pytest.mark.asyncio
    async def test_empty_result_chain_with_real_delivery_enters_observation(self):
        from types import SimpleNamespace

        from astrbot_plugin_angel_heart.main import AngelHeartPlugin

        plugin = object.__new__(AngelHeartPlugin)

        class _FakeRuntimeTasks:
            async def run(self, event, fn):
                return await fn()

        plugin._runtime_tasks = _FakeRuntimeTasks()
        plugin._finish_secretary_dispatch = AsyncMock(return_value=True)
        plugin._extract_sent_message_content = MagicMock(return_value="streamed reply")
        plugin.front_desk = SimpleNamespace(
            _get_event_message_id=MagicMock(return_value="message-1")
        )
        plugin.angel_context = SimpleNamespace(
            debounce_manager=SimpleNamespace(
                get_leave_reply_trigger=MagicMock(return_value=None)
            ),
            handle_message_sent=AsyncMock(),
            work_ledger=SimpleNamespace(complete_work=MagicMock()),
        )

        event = MagicMock()
        event.unified_msg_origin = "whatsapp:GroupMessage:1203631"
        event._has_send_oper = True
        event.get_result.return_value = SimpleNamespace(chain=[])
        event.get_extra.return_value = "work-1"

        await plugin.handle_message_sent(event)

        plugin.angel_context.handle_message_sent.assert_awaited_once_with(
            event.unified_msg_origin,
            keep_not_present=False,
        )
        plugin._finish_secretary_dispatch.assert_awaited_once()
        plugin.angel_context.work_ledger.complete_work.assert_called_once_with(
            event.unified_msg_origin,
            "work-1",
            status="done",
            result_summary="streamed reply",
        )
