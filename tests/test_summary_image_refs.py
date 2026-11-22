"""摘要附图片路径与媒体保留测试。

对应 issue #78：上下文整理折叠图片消息时，摘要正文必须带上本地缓存路径，
并由摘要消息持有引用，避免缓存文件被清理后上下文里只剩占位文字。
"""

from __future__ import annotations

import io

import pytest
from PIL import Image as PILImage

from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger

CHAT_ID = "aiocqhttp:GroupMessage:1"


class DummyConfig(ConfigManager):
    def __init__(self):
        super().__init__(
            {
                "context_compression": {
                    "max_conversation_tokens": 200,
                    "context_compression_threshold": 0.5,
                    "content_retain_tokens": 4000,
                    "forgetting_timeout": 0,
                },
                "timing": {},
                "wake_interaction": {},
                "leave_reply": {},
                "access_control": {},
                "output_rewrite": {},
                "personality": {},
            }
        )


@pytest.fixture
def ledger(tmp_path):
    lg = ConversationLedger(DummyConfig(), tmp_path, astr_context=None)
    try:
        yield lg
    finally:
        lg.close()


def _png_bytes(color="red"):
    buf = io.BytesIO()
    PILImage.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


async def _cache_path(ledger, chat_id=CHAT_ID, color="red"):
    dhash = await ledger.image_cache.put(chat_id, _png_bytes(color))
    return str(ledger.image_cache.get_cached_path(chat_id, dhash))


def _image_message(timestamp, cache_path, chat_id=CHAT_ID, name="红豆"):
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": "看这张图"},
            {
                "type": "image_url",
                "image_url": {"url": cache_path},
                "cache_path": cache_path,
                "local_file_path": cache_path,
                "original_url": cache_path,
                "original_file_url": cache_path,
            },
        ],
        "sender_id": "u1",
        "sender_name": name,
        "timestamp": float(timestamp),
        "chat_id": chat_id,
    }


def _text_message(timestamp, text, chat_id=CHAT_ID, name="群友"):
    return {
        "role": "user",
        "content": text,
        "sender_id": "u2",
        "sender_name": name,
        "timestamp": float(timestamp),
        "chat_id": chat_id,
    }


def _organize(ledger, keep_from_timestamp):
    assert (
        ledger.organize_on_group_enter(CHAT_ID, keep_from_timestamp=keep_from_timestamp)
        is True
    )
    # 删除在媒体执行域异步完成，等它跑完再断言磁盘状态
    ledger.image_cache.drain()


def _summary_message(ledger):
    """取账本里第一条带图片的消息。

    滑动窗口不再生成摘要：图片消息如果还在窗口内，就原样保留，
    路径跟着消息走。
    """
    messages = ledger.get_all_messages(CHAT_ID)
    assert messages
    for msg in messages:
        if ledger._collect_message_image_refs([msg]):
            return msg
    raise AssertionError("窗口内没有带图片的消息")


class TestSummaryKeepsImagePath:
    @pytest.mark.asyncio
    async def test_image_message_in_window_keeps_path(self, ledger):
        """图片消息留在窗口内时，路径挂在消息上，能被引用收集到。"""
        cache_path = await _cache_path(ledger)
        ledger._get_or_create_ledger(CHAT_ID)["messages"] = [
            _image_message(1, cache_path),
            _text_message(2, "继续聊"),
        ]

        msgs = ledger.get_all_messages(CHAT_ID)
        refs = []
        for msg in msgs:
            refs.extend(ledger._collect_message_image_refs([msg]))
        assert cache_path in refs

    @pytest.mark.asyncio
    async def test_cached_file_survives_cleanup(self, ledger):
        cache_path = await _cache_path(ledger)
        ledger._get_or_create_ledger(CHAT_ID)["messages"] = [
            _image_message(1, cache_path),
            _text_message(2, "继续聊"),
        ]

        ledger._cleanup_unreferenced_media_cache(CHAT_ID)
        ledger.image_cache.drain()

        from pathlib import Path

        assert Path(cache_path).exists()

    @pytest.mark.asyncio
    async def test_unreferenced_file_still_removed(self, ledger):
        """没被任何消息引用的缓存文件照旧清理，保留逻辑不扩大范围。"""
        from pathlib import Path

        cache_path = await _cache_path(ledger)
        ledger._get_or_create_ledger(CHAT_ID)["messages"] = [
            _text_message(1, "只有文字"),
            _text_message(2, "继续聊"),
        ]

        ledger._cleanup_unreferenced_media_cache(CHAT_ID)
        ledger.image_cache.drain()

        assert not Path(cache_path).exists()

    @pytest.mark.asyncio
    async def test_image_evicted_from_window_is_cleaned(self, ledger):
        """图片消息滑出窗口后，文件跟着清理——滑动窗口不再为旧图保底。"""
        cache_path = await _cache_path(ledger)
        ledger._get_or_create_ledger(CHAT_ID)["messages"] = [
            _image_message(1, cache_path),
            _text_message(2, "继续聊"),
        ]

        # 触发点之后没有图片，图片被挤出窗口
        assert ledger.organize_on_group_enter(CHAT_ID, keep_from_timestamp=2.0) is True
        ledger.image_cache.drain()

        from pathlib import Path

        assert not Path(cache_path).exists()
