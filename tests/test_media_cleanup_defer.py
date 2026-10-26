"""媒体缓存延迟清理测试。

会话仍在调度中时不清理媒体缓存，避免删掉运行中请求与工具续轮正在引用的图片；
调度收口后补做清理。
"""

from __future__ import annotations

import io
from pathlib import Path

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


def _unreferenced_cache_file(ledger):
    """写入一个不被任何账本消息引用的缓存文件。"""
    buf = io.BytesIO()
    PILImage.new("RGB", (8, 8), "blue").save(buf, format="PNG")
    dhash = ledger.image_cache.put(CHAT_ID, buf.getvalue())
    path = Path(ledger.image_cache.get_cached_path(CHAT_ID, dhash))
    assert path.exists()
    return path


def test_busy_session_defers_cleanup(ledger):
    path = _unreferenced_cache_file(ledger)
    ledger.set_cleanup_busy_check(lambda chat_id: True)

    ledger._cleanup_unreferenced_media_cache(CHAT_ID)

    assert path.exists()


def test_flush_after_idle_cleans_up(ledger):
    path = _unreferenced_cache_file(ledger)
    busy = {"value": True}
    ledger.set_cleanup_busy_check(lambda chat_id: busy["value"])
    ledger._cleanup_unreferenced_media_cache(CHAT_ID)

    busy["value"] = False
    ledger.flush_pending_cleanup(CHAT_ID)

    assert not path.exists()


def test_flush_keeps_waiting_while_still_busy(ledger):
    path = _unreferenced_cache_file(ledger)
    ledger.set_cleanup_busy_check(lambda chat_id: True)
    ledger._cleanup_unreferenced_media_cache(CHAT_ID)

    ledger.flush_pending_cleanup(CHAT_ID)

    assert path.exists()


def test_idle_session_cleans_immediately(ledger):
    path = _unreferenced_cache_file(ledger)
    ledger.set_cleanup_busy_check(lambda chat_id: False)

    ledger._cleanup_unreferenced_media_cache(CHAT_ID)

    assert not path.exists()


def test_busy_check_error_falls_back_to_cleaning(ledger):
    path = _unreferenced_cache_file(ledger)

    def _boom(chat_id):
        raise RuntimeError("busy check failed")

    ledger.set_cleanup_busy_check(_boom)

    ledger._cleanup_unreferenced_media_cache(CHAT_ID)

    assert not path.exists()


def test_busy_check_scoped_to_chat(ledger):
    """只延后忙会话的清理，其它会话照旧。"""
    path = _unreferenced_cache_file(ledger)
    ledger.set_cleanup_busy_check(lambda chat_id: chat_id == "other:GroupMessage:9")

    ledger._cleanup_unreferenced_media_cache(CHAT_ID)

    assert not path.exists()
