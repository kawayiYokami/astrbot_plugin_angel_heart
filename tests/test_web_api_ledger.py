"""账本查看接口：只读、一次全量、图片转述要带出来。"""

import pytest

from astrbot_plugin_angel_heart import web_api
from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger
from astrbot_plugin_angel_heart.web_api import ProfileAPI

CHAT = "aiocqhttp:GroupMessage:ledger"


class DummyConfig(ConfigManager):
    def __init__(self):
        super().__init__(
            {
                "context_compression": {
                    "max_conversation_tokens": 100000,
                    "context_compression_threshold": 0.9,
                    "content_retain_tokens": 100000,
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
    value = ConversationLedger(DummyConfig(), tmp_path, astr_context=None)
    yield value
    value.close()


def _api(ledger):
    """只测账本读取，绕过 ProfileAPI 的完整初始化。"""
    api = object.__new__(ProfileAPI)
    api.conversation_ledger = ledger
    return api


def _query(monkeypatch, **params):
    """桩掉请求上下文相关的三件套，只测数据逻辑。"""
    monkeypatch.setattr(
        web_api, "_get_query", lambda name, default="": params.get(name, default)
    )
    monkeypatch.setattr(
        web_api,
        "_ok",
        lambda data=None, message="": {"status": "ok", "message": message, "data": data},
    )
    monkeypatch.setattr(
        web_api,
        "_err",
        lambda message, status_code=400: {
            "status": "error",
            "message": message,
            "data": {},
        },
    )


def _msg(i, text, *, role="user", chat_id=CHAT):
    return {
        "role": role,
        "content": text,
        "sender_id": f"u{i}",
        "sender_name": f"user{i}",
        "timestamp": float(i),
        "chat_id": chat_id,
        "source_message_id": f"src-{i}",
    }


@pytest.mark.asyncio
async def test_returns_every_message_in_one_response(ledger, monkeypatch):
    for i in range(1, 31):
        ledger.add_message(CHAT, _msg(i, f"m{i}"))
    _query(monkeypatch, chat_id=CHAT)

    result = await _api(ledger).ledger_messages()

    assert result["status"] == "ok"
    data = result["data"]
    assert data["total"] == 30
    assert len(data["messages"]) == 30
    # 一次全量，不分页
    assert "offset" not in data
    assert "limit" not in data


@pytest.mark.asyncio
async def test_image_caption_is_returned(ledger, monkeypatch):
    """图片消息的 content 会被清空，转述在 image_caption 里，必须带出来。"""
    msg = _msg(1, "")
    msg["content"] = [{"type": "image_url", "image_url": {"url": "/cache/a.jpg"}}]
    ledger.add_message(CHAT, msg)
    ledger.add_caption_to_message(CHAT, 1.0, "这是一张猫在键盘上的照片")
    _query(monkeypatch, chat_id=CHAT)

    result = await _api(ledger).ledger_messages()

    item = result["data"]["messages"][0]
    assert item["content"] == ""
    assert item["image_caption"] == "这是一张猫在键盘上的照片"


@pytest.mark.asyncio
async def test_missing_chat_id_is_error(ledger, monkeypatch):
    _query(monkeypatch)

    result = await _api(ledger).ledger_messages()

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_unknown_chat_returns_empty_not_error(ledger, monkeypatch):
    """账本内存态，来源登记过的会话可能还没消息：返回空列表，不报错。"""
    ledger.add_message(CHAT, _msg(1, "hi"))
    _query(monkeypatch, chat_id="aiocqhttp:GroupMessage:missing")

    result = await _api(ledger).ledger_messages()

    assert result["status"] == "ok"
    assert result["data"]["total"] == 0
    assert result["data"]["messages"] == []


@pytest.mark.asyncio
async def test_long_content_is_truncated_with_original_length(ledger, monkeypatch):
    ledger.add_message(CHAT, _msg(1, "x" * 5000))
    _query(monkeypatch, chat_id=CHAT)

    result = await _api(ledger).ledger_messages()

    content = result["data"]["messages"][0]["content"]
    assert content.startswith("x" * 4000)
    assert "原长 5000 字符" in content
