"""未接管上下文时的媒体收集。

图片下载与转述是主要开销，缓存还会在 URL 过期后变成喂给模型的失效内容。
未接管时只记文本占位，文本照常入账，秘书与记忆检索不缺素材。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.message.components import File, Image, Plain

from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

PRIVATE = "aiocqhttp:FriendMessage:999"
GROUP = "aiocqhttp:GroupMessage:84"


def _front(*, takeover=False, enhancement=True, whitelist=(), enabled=False):
    front = FrontDesk.__new__(FrontDesk)
    front.context = MagicMock()
    front.config_manager = MagicMock()
    front.config_manager.takeover_private_chat_context = takeover
    front.config_manager.group_chat_enhancement = enhancement
    front.config_manager.whitelist_enabled = enabled
    front.config_manager.chat_ids = list(whitelist)
    front.config_manager.for_chat.return_value = SimpleNamespace(
        alias="机器人", focus_instructions=""
    )
    front._get_event_message_id = lambda event: "1"
    front._extract_event_sender_role = lambda event: "群友"
    front._record_chat_source = MagicMock()
    front._build_cached_image_item = AsyncMock(
        return_value={"type": "image_url", "image_url": {"url": "cached.png"}}
    )
    front._build_cached_file_text_item = AsyncMock(
        return_value={"type": "text", "text": "[文件: a.txt]\nFILE-BODY"}
    )
    return front


def _event(chain, outline="看图"):
    event = MagicMock()
    event.get_messages.return_value = chain
    event.get_message_outline.return_value = outline
    event.get_sender_id.return_value = "human"
    event.get_sender_name.return_value = "小明"
    event.get_extra.return_value = None
    return event


def _image_chain():
    return [Plain("看图"), Image.fromFileSystem("x.png")]


def _saved(front):
    return front.context.conversation_ledger.add_message.call_args.args[1]


@pytest.mark.asyncio
async def test_unmanaged_private_chat_records_placeholder_without_download():
    front = _front(takeover=False)

    await front.cache_message(PRIVATE, _event(_image_chain()))

    front._build_cached_image_item.assert_not_awaited()
    assert {"type": "text", "text": "[图片]"} in _saved(front)["content"]
    assert not any(i.get("type") == "image_url" for i in _saved(front)["content"])


@pytest.mark.asyncio
async def test_unmanaged_group_chat_records_placeholder_without_download():
    front = _front(enhancement=False)

    await front.cache_message(GROUP, _event(_image_chain()))

    front._build_cached_image_item.assert_not_awaited()
    assert not any(i.get("type") == "image_url" for i in _saved(front)["content"])


@pytest.mark.asyncio
async def test_managed_private_chat_downloads_image():
    front = _front(takeover=True)

    await front.cache_message(PRIVATE, _event(_image_chain()))

    front._build_cached_image_item.assert_awaited_once()
    assert any(i.get("type") == "image_url" for i in _saved(front)["content"])


@pytest.mark.asyncio
async def test_managed_group_chat_downloads_image():
    front = _front(enhancement=True)

    await front.cache_message(GROUP, _event(_image_chain()))

    front._build_cached_image_item.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_outside_whitelist_skips_media_even_when_managed():
    """@ 唤醒会绕过入口白名单检查，媒体收集必须自己再挡一次。"""
    front = _front(enhancement=True, enabled=True, whitelist={"100"})

    await front.cache_message(GROUP, _event(_image_chain()))

    front._build_cached_image_item.assert_not_awaited()
    assert not any(i.get("type") == "image_url" for i in _saved(front)["content"])


@pytest.mark.asyncio
async def test_chat_inside_whitelist_downloads_image():
    front = _front(enhancement=True, enabled=True, whitelist={"84"})

    await front.cache_message(GROUP, _event(_image_chain()))

    front._build_cached_image_item.assert_awaited_once()


@pytest.mark.asyncio
async def test_unmanaged_file_keeps_name_only():
    front = _front(takeover=False)
    chain = [Plain("看这个"), File(name="a.txt", file="a.txt")]

    await front.cache_message(PRIVATE, _event(chain))

    front._build_cached_file_text_item.assert_not_awaited()
    content = _saved(front)["content"]
    assert content == [{"type": "text", "text": "看这个[文件: a.txt]"}]


@pytest.mark.asyncio
async def test_pure_image_outline_is_not_duplicated():
    """纯图片消息的 outline 本身就是「[图片]」，别追加成两遍。"""
    front = _front(takeover=False)

    await front.cache_message(
        PRIVATE, _event([Image.fromFileSystem("x.png")], outline="[图片]")
    )

    assert _saved(front)["content"] == [{"type": "text", "text": "[图片]"}]
