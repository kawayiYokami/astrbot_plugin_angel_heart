"""空正文回复的工作账本收口。

空内容会被框架跳过整个发送环节，after_message_sent 不再触发，账本里那条
running 无人收尾，同会话后续消息会一直撞 assistant_busy 直到超时。这三处
提前收口：Agent 结束无可发内容、错误信息清空消息链、发送前消息链已空。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger

CHAT = "aiocqhttp:GroupMessage:1001"
WORK = "work-1"


class _ImmediateTasks:
    """把追踪装饰器换成直接执行，避免测试里起真实子任务。"""

    async def run(self, event, fn, *, owns_pipeline=False):
        return await fn()


def _config():
    config = MagicMock()
    config.strip_markdown_enabled = False
    config.strip_period_before_newline = False
    return config


def _plugin():
    from astrbot_plugin_angel_heart.main import AngelHeartPlugin

    plugin = object.__new__(AngelHeartPlugin)
    plugin.config_manager = _config()
    plugin.context = MagicMock()
    plugin._runtime_tasks = _ImmediateTasks()
    plugin.angel_context = MagicMock()
    plugin.angel_context.work_ledger = WorkLedger()
    plugin.angel_context.silenced_until = {}
    plugin.angel_context.debounce_manager.charge_reply_energy = AsyncMock()
    plugin.front_desk = MagicMock()
    plugin.front_desk._get_event_message_id.return_value = WORK
    plugin._is_upstream_command_event = lambda event: False
    plugin._apply_quote_target = MagicMock()
    plugin._try_convert_long_reply = AsyncMock()
    return plugin


def _event(chain=None):
    event = MagicMock()
    event.unified_msg_origin = CHAT
    event.get_extra.return_value = WORK
    event.get_result.return_value = SimpleNamespace(
        chain=[] if chain is None else chain
    )
    return event


def _start(plugin):
    plugin.angel_context.work_ledger.start_work(
        chat_id=CHAT,
        work_id=WORK,
        trigger_message_id="m1",
        trigger_summary="在吗",
    )


def _active(plugin):
    return plugin.angel_context.work_ledger.get_active_works(CHAT)


@pytest.mark.parametrize(
    "chain, expected",
    [
        ([], False),
        ([Plain(text="")], False),
        ([Plain(text="   \n\t ")], False),
        ([Reply(id="1")], False),
        ([At(qq="123", name="小明")], False),
        ([Reply(id="1"), At(qq="123", name="小明")], False),
        ([Plain(text="票我上周就买好了")], True),
        ([Plain(text=""), Image.fromFileSystem("x.png")], True),
    ],
)
def test_has_sendable_content(chain, expected):
    from astrbot_plugin_angel_heart.main import AngelHeartPlugin

    assert AngelHeartPlugin._has_sendable_content(chain) is expected


@pytest.mark.asyncio
async def test_agent_done_without_content_closes_work():
    """Agent 跑完但没有任何可发内容：立刻收口，不等发送钩子。"""
    plugin = _plugin()
    _start(plugin)
    response = SimpleNamespace(completion_text="", result_chain=[])

    with patch(
        "astrbot_plugin_angel_heart.main.extract_completed_agent_messages",
        return_value=[],
    ):
        await plugin.capture_completed_agent_messages(
            _event(), SimpleNamespace(messages=[]), response
        )

    assert _active(plugin) == []


@pytest.mark.asyncio
async def test_agent_done_with_content_keeps_work_open():
    """有正文时不动账本，交给发送后的钩子收口。"""
    plugin = _plugin()
    _start(plugin)
    response = SimpleNamespace(
        completion_text="票我上周就买好了",
        result_chain=[Plain(text="票我上周就买好了")],
    )

    with patch(
        "astrbot_plugin_angel_heart.main.extract_completed_agent_messages",
        return_value=[],
    ):
        await plugin.capture_completed_agent_messages(
            _event(), SimpleNamespace(messages=[]), response
        )

    assert len(_active(plugin)) == 1


@pytest.mark.asyncio
async def test_agent_done_none_response_closes_work():
    """框架允许 response 为 None（无最终回复就退出），同样要收口。"""
    plugin = _plugin()
    _start(plugin)

    with patch(
        "astrbot_plugin_angel_heart.main.extract_completed_agent_messages",
        return_value=[],
    ):
        await plugin.capture_completed_agent_messages(
            _event(), SimpleNamespace(messages=[]), None
        )

    assert _active(plugin) == []


@pytest.mark.asyncio
async def test_error_message_clears_chain_and_closes_work():
    """命中错误信息时清空消息链，同时收口。"""
    plugin = _plugin()
    _start(plugin)
    event = _event([Plain(text="AstrBot 请求失败\n错误类型: x\n错误信息: y")])

    await plugin.strip_markdown_on_decorating_result(event)

    assert event.get_result.return_value.chain == []
    assert _active(plugin) == []


@pytest.mark.asyncio
async def test_blank_chain_before_send_closes_work():
    """清洗之后消息链已无可发内容：发送会被跳过，这里先收口。"""
    plugin = _plugin()
    _start(plugin)
    event = _event([Plain(text="   ")])

    await plugin.strip_markdown_on_decorating_result(event)

    assert _active(plugin) == []


@pytest.mark.asyncio
async def test_normal_reply_keeps_work_open():
    """正常正文不动账本，交给发送后钩子。"""
    plugin = _plugin()
    _start(plugin)
    event = _event([Plain(text="票我上周就买好了")])

    await plugin.strip_markdown_on_decorating_result(event)

    assert len(_active(plugin)) == 1


@pytest.mark.asyncio
async def test_silenced_chat_clears_chain_and_closes_work():
    """回复生成期间被掌嘴：这一条不外发，同时收口。"""
    plugin = _plugin()
    _start(plugin)
    plugin.angel_context.silenced_until[CHAT] = time.time() + 60
    event = _event([Plain(text="票我上周就买好了")])

    await plugin.strip_markdown_on_decorating_result(event)

    assert event.get_result.return_value.chain == []
    assert _active(plugin) == []
    plugin.angel_context.debounce_manager.charge_reply_energy.assert_not_awaited()


@pytest.mark.asyncio
async def test_silence_expired_does_not_block_send():
    """闭嘴已过期：照常发送，不误拦。"""
    plugin = _plugin()
    _start(plugin)
    plugin.angel_context.silenced_until[CHAT] = time.time() - 1
    event = _event([Plain(text="票我上周就买好了")])

    await plugin.strip_markdown_on_decorating_result(event)

    assert event.get_result.return_value.chain
    plugin.angel_context.debounce_manager.charge_reply_energy.assert_awaited()
