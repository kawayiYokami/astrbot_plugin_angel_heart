"""工具直发消息的文本清理。

`send_message_to_user` 由 ToolExecutor 直接发送，不经过发送前的 decorating 钩子：
`[#n]` 标记会原样出现在群里，Markdown 也不清洗，闭嘴也拦不住。这里钉住
`on_using_llm_tool` 上的补偿：钩子拿到的就是待执行的参数对象，就地改写生效。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrbot_plugin_angel_heart.main import AngelHeartPlugin

CHAT = "aiocqhttp:GroupMessage:1001"


def _plugin(silenced_until=None, strip_markdown=True, strip_period=False):
    plugin = object.__new__(AngelHeartPlugin)
    config = MagicMock()
    config.strip_markdown_enabled = strip_markdown
    config.strip_period_before_newline = strip_period
    plugin.config_manager = config
    plugin.angel_context = MagicMock()
    plugin.angel_context.silenced_until = silenced_until or {}
    return plugin


def _event():
    event = MagicMock()
    event.unified_msg_origin = CHAT
    return event


def _tool(name="send_message_to_user"):
    return SimpleNamespace(name=name)


def _args(*texts):
    return {
        "messages": [{"type": "plain", "text": text} for text in texts],
    }


@pytest.mark.asyncio
async def test_prefix_marker_is_stripped():
    """正文开头的 [#n] 必须剥掉，不能出现在群里。"""
    plugin = _plugin()
    tool_args = _args("[#3167] 这版改到开衩大腿和薄纱长袜都给足了")

    await plugin.clean_tool_sent_text(_event(), _tool(), tool_args)

    assert tool_args["messages"][0]["text"] == "这版改到开衩大腿和薄纱长袜都给足了"


@pytest.mark.asyncio
async def test_marker_mid_text_is_kept():
    """正文中段的 [#n] 是普通文字，不动。"""
    plugin = _plugin()
    tool_args = _args("看这条 [#3167] 说的是")

    await plugin.clean_tool_sent_text(_event(), _tool(), tool_args)

    assert tool_args["messages"][0]["text"] == "看这条 [#3167] 说的是"


@pytest.mark.asyncio
async def test_markdown_is_cleaned():
    """与常规回复同口径：Markdown 清洗在开关打开时生效。"""
    plugin = _plugin()
    tool_args = _args("**加粗** 和 `代码`")

    await plugin.clean_tool_sent_text(_event(), _tool(), tool_args)

    cleaned = tool_args["messages"][0]["text"]
    assert "**" not in cleaned and "`" not in cleaned


@pytest.mark.asyncio
async def test_markdown_untouched_when_disabled():
    plugin = _plugin(strip_markdown=False)
    tool_args = _args("**加粗**")

    await plugin.clean_tool_sent_text(_event(), _tool(), tool_args)

    assert tool_args["messages"][0]["text"] == "**加粗**"


@pytest.mark.asyncio
async def test_silenced_chat_drops_tool_messages():
    """闭嘴期间工具直发也不许出去。"""
    plugin = _plugin(silenced_until={CHAT: time.time() + 60})
    tool_args = _args("在的")

    await plugin.clean_tool_sent_text(_event(), _tool(), tool_args)

    assert tool_args["messages"] == []


@pytest.mark.asyncio
async def test_other_tools_are_untouched():
    plugin = _plugin()
    tool_args = _args("[#1] 不动")

    await plugin.clean_tool_sent_text(_event(), _tool("web_search"), tool_args)

    assert tool_args["messages"][0]["text"] == "[#1] 不动"


@pytest.mark.asyncio
async def test_image_component_is_untouched():
    plugin = _plugin()
    tool_args = {
        "messages": [
            {"type": "plain", "text": "[#2] 图来了"},
            {"type": "image", "path": "/tmp/a.png"},
        ]
    }

    await plugin.clean_tool_sent_text(_event(), _tool(), tool_args)

    assert tool_args["messages"][0]["text"] == "图来了"
    assert tool_args["messages"][1] == {"type": "image", "path": "/tmp/a.png"}
