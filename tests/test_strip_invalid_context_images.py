from __future__ import annotations

from unittest.mock import MagicMock, patch

from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger
from astrbot_plugin_angel_heart.roles import front_desk as front_desk_module
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


def _front_desk():
    config = MagicMock()
    config.for_chat.return_value = config
    config.alias = "fairy"
    angel = MagicMock()
    angel.work_ledger = WorkLedger()
    angel.astr_context = MagicMock()
    return FrontDesk(config, angel)


def _image_item(url: str, **extra):
    item = {"type": "image_url", "image_url": {"url": url}}
    item.update(extra)
    return item


def test_keeps_existing_local_image(tmp_path):
    fd = _front_desk()
    path = tmp_path / "alive.webp"
    path.write_bytes(b"RIFF----WEBP")

    contexts = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                _image_item(str(path), cache_path=str(path)),
            ],
        }
    ]

    out = fd._strip_invalid_context_images("chat", contexts)

    assert [item["type"] for item in out[0]["content"]] == ["text", "image_url"]


def test_drops_missing_local_image(tmp_path):
    fd = _front_desk()
    missing = tmp_path / "gone.webp"

    contexts = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                _image_item(str(missing), cache_path=str(missing)),
            ],
        }
    ]

    out = fd._strip_invalid_context_images("chat", contexts)

    assert [item["type"] for item in out[0]["content"]] == ["text"]
    assert out[0]["content"][0]["text"] == "看图"


def test_empty_local_image_is_dropped_and_placeholder_added(tmp_path):
    fd = _front_desk()
    path = tmp_path / "empty.webp"
    path.write_bytes(b"")

    contexts = [{"role": "user", "content": [_image_item(str(path), cache_path=str(path))]}]

    out = fd._strip_invalid_context_images("chat", contexts)

    assert out[0]["content"] == [{"type": "text", "text": "[图片已失效]"}]


def test_keeps_data_and_remote_refs_without_touching_disk():
    fd = _front_desk()

    contexts = [
        {
            "role": "user",
            "content": [
                _image_item("data:image/webp;base64,AAAA"),
                _image_item("https://example.com/a.png"),
                _image_item("http://example.com/b.png"),
            ],
        }
    ]

    out = fd._strip_invalid_context_images("chat", contexts)

    assert len(out[0]["content"]) == 3


def test_non_list_content_is_passed_through():
    fd = _front_desk()
    contexts = [{"role": "assistant", "content": "plain text"}]

    out = fd._strip_invalid_context_images("chat", contexts)

    assert out == contexts


def test_dropped_image_logs_ref_source_and_origin(tmp_path):
    fd = _front_desk()
    missing = tmp_path / "gone.webp"

    contexts = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "[群友: 红豆] (2026-09-21 16:20): 看图"},
                _image_item(
                    str(missing),
                    cache_path=str(missing),
                    source_url="http://example.com/origin.png",
                ),
            ],
        }
    ]

    with patch.object(front_desk_module.logger, "warning") as warn:
        fd._strip_invalid_context_images("chat", contexts)

    logged = " | ".join(str(call.args[0]) for call in warn.call_args_list)
    assert "gone.webp" in logged
    assert "http://example.com/origin.png" in logged
    assert "红豆" in logged