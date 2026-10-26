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
                    "content_retain_tokens": 80,
                    "tool_retain_tokens": 40,
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
    return ConversationLedger(DummyConfig(), tmp_path, astr_context=None)


def _png_bytes(color="red"):
    buf = io.BytesIO()
    PILImage.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


def _cache_path(ledger, chat_id=CHAT_ID, color="red"):
    dhash = ledger.image_cache.put(chat_id, _png_bytes(color))
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


def _summary_message(ledger):
    messages = ledger.get_all_messages(CHAT_ID)
    assert messages
    head = messages[0]
    assert head.get("kind") == "context_summary"
    return head


class TestSummaryKeepsImagePath:
    def test_folded_image_path_appears_in_summary(self, ledger):
        cache_path = _cache_path(ledger)
        ledger._get_or_create_ledger(CHAT_ID)["messages"] = [
            _image_message(1, cache_path),
            _text_message(2, "继续聊"),
        ]

        _organize(ledger, keep_from_timestamp=2.0)

        summary = _summary_message(ledger)
        assert cache_path in summary["content"]

    def test_summary_message_holds_image_refs(self, ledger):
        cache_path = _cache_path(ledger)
        ledger._get_or_create_ledger(CHAT_ID)["messages"] = [
            _image_message(1, cache_path),
            _text_message(2, "继续聊"),
        ]

        _organize(ledger, keep_from_timestamp=2.0)

        summary = _summary_message(ledger)
        assert cache_path in summary.get("image_refs", [])

    def test_cached_file_survives_cleanup(self, ledger):
        cache_path = _cache_path(ledger)
        ledger._get_or_create_ledger(CHAT_ID)["messages"] = [
            _image_message(1, cache_path),
            _text_message(2, "继续聊"),
        ]

        _organize(ledger, keep_from_timestamp=2.0)

        from pathlib import Path

        assert Path(cache_path).exists()

    def test_unreferenced_file_still_removed(self, ledger):
        """没被任何消息引用的缓存文件照旧清理，保留逻辑不扩大范围。"""
        from pathlib import Path

        cache_path = _cache_path(ledger)
        ledger._get_or_create_ledger(CHAT_ID)["messages"] = [
            _text_message(1, "只有文字"),
            _text_message(2, "继续聊"),
        ]

        _organize(ledger, keep_from_timestamp=2.0)

        assert not Path(cache_path).exists()

    def test_second_organize_inherits_refs(self, ledger):
        cache_path = _cache_path(ledger)
        ledger._get_or_create_ledger(CHAT_ID)["messages"] = [
            _image_message(1, cache_path),
            _text_message(2, "继续聊"),
        ]
        _organize(ledger, keep_from_timestamp=2.0)

        ledger.add_message(CHAT_ID, _text_message(3, "再聊一句"), should_prune=False)
        _organize(ledger, keep_from_timestamp=3.0)

        summary = _summary_message(ledger)
        assert cache_path in summary["content"]
        assert cache_path in summary.get("image_refs", [])
