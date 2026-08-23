from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parent))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger


def _ledger_with_image(chat_id: str, path: str) -> ConversationLedger:
    ledger = object.__new__(ConversationLedger)
    ledger._lock = threading.Lock()
    ledger._compression_locks = {}
    ledger._ledgers = {
        chat_id: {
            "messages": [
                {
                    "role": "user",
                    "timestamp": 1.0,
                    "content": [
                        {"type": "text", "text": "看看这张图"},
                        {"type": "image_url", "cache_path": path},
                    ],
                }
            ],
            "current_summary": "",
        }
    }
    return ledger


def test_describe_image_uses_requested_ledger_image_and_focus():
    chat_id = "aiocqhttp:GroupMessage:10000"
    path = "file:///tmp/ledger-image.png"
    ledger = _ledger_with_image(chat_id, path)
    calls = []

    async def load_image_bytes(value: str) -> bytes:
        assert value == path
        return b"image-bytes"

    class Provider:
        async def text_chat(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(completion_text="右下角文字是 ERROR 42")

    ledger._load_image_bytes = load_image_bytes
    ledger._build_caption_image_data_url = lambda _data: "data:image/webp;base64,TEST"

    result = asyncio.run(
        ledger.describe_image(
            chat_id=chat_id,
            path=path,
            focus="读取右下角的报错文字",
            caption_provider_id="vision",
            astr_context=SimpleNamespace(get_provider_by_id=lambda _id: Provider()),
        )
    )

    assert result == "右下角文字是 ERROR 42"
    assert calls == [
        {
            "prompt": (
                "请只依据这张图片回答下面的关注点。若图片无法确认，请明确说明无法确认，"
                "不要补充未在图片中出现的内容。\n\n"
                "关注点：读取右下角的报错文字"
            ),
            "image_urls": ["data:image/webp;base64,TEST"],
        }
    ]
    message = ledger._ledgers[chat_id]["messages"][0]
    assert "image_caption" not in message
    assert message["content"][1]["cache_path"] == path


def test_describe_image_accepts_path_outside_current_ledger():
    chat_id = "aiocqhttp:GroupMessage:10000"
    ledger = _ledger_with_image(chat_id, "file:///tmp/ledger-image.png")

    async def load_image_bytes(value: str) -> bytes:
        assert value == "file:///tmp/not-in-ledger.png"
        return b"image-bytes"

    class Provider:
        async def text_chat(self, **kwargs):
            return SimpleNamespace(completion_text="外部路径图片理解成功")

    ledger._load_image_bytes = load_image_bytes
    ledger._build_caption_image_data_url = lambda _data: "data:image/webp;base64,TEST"

    result = asyncio.run(
        ledger.describe_image(
            chat_id=chat_id,
            path="file:///tmp/not-in-ledger.png",
            focus="读取文字",
            caption_provider_id="vision",
            astr_context=SimpleNamespace(get_provider_by_id=lambda _id: Provider()),
        )
    )

    assert result == "外部路径图片理解成功"


def test_describe_image_requires_configured_provider():
    chat_id = "aiocqhttp:GroupMessage:10000"
    ledger = _ledger_with_image(chat_id, "file:///tmp/ledger-image.png")

    result = asyncio.run(
        ledger.describe_image(
            chat_id=chat_id,
            path="file:///tmp/ledger-image.png",
            focus="读取文字",
            caption_provider_id="",
            astr_context=SimpleNamespace(get_provider_by_id=lambda _id: None),
        )
    )

    assert result == "图片理解不可用：未配置 image_caption_provider_id。"


def test_describe_image_rejects_missing_provider():
    chat_id = "aiocqhttp:GroupMessage:10000"
    ledger = _ledger_with_image(chat_id, "file:///tmp/ledger-image.png")

    result = asyncio.run(
        ledger.describe_image(
            chat_id=chat_id,
            path="file:///tmp/ledger-image.png",
            focus="读取文字",
            caption_provider_id="vision",
            astr_context=SimpleNamespace(get_provider_by_id=lambda _id: None),
        )
    )

    assert result == "图片理解不可用：找不到已配置的图片理解 Provider。"


def test_describe_image_returns_provider_failure_as_tool_result():
    chat_id = "aiocqhttp:GroupMessage:10000"
    path = "file:///tmp/ledger-image.png"
    ledger = _ledger_with_image(chat_id, path)

    async def load_image_bytes(_value: str) -> bytes:
        return b"image-bytes"

    class FailingProvider:
        async def text_chat(self, **_kwargs):
            raise RuntimeError("upstream failed")

    ledger._load_image_bytes = load_image_bytes
    ledger._build_caption_image_data_url = lambda _data: "data:image/webp;base64,TEST"

    result = asyncio.run(
        ledger.describe_image(
            chat_id=chat_id,
            path=path,
            focus="读取文字",
            caption_provider_id="vision",
            astr_context=SimpleNamespace(
                get_provider_by_id=lambda _id: FailingProvider()
            ),
        )
    )

    assert result == "图片理解失败：视觉 Provider 调用异常：upstream failed"
