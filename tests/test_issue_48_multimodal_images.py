from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parent))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astrbot_plugin_angel_heart.core.utils.context_utils import format_final_prompt
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


_DEFAULT_MODALITIES = object()
_MISSING_MODALITIES = object()


def _front_desk(
    *,
    supports_image: bool,
    image_caption_provider_id: str = "",
    modalities=_DEFAULT_MODALITIES,
) -> FrontDesk:
    front_desk = object.__new__(FrontDesk)
    front_desk._config_manager = SimpleNamespace(
        image_caption_provider_id=image_caption_provider_id
    )
    if modalities is _DEFAULT_MODALITIES:
        modalities = ["text", "image"] if supports_image else ["text"]
    provider_config = {}
    if modalities is not _MISSING_MODALITIES:
        provider_config["modalities"] = modalities
    provider = SimpleNamespace(provider_config=provider_config)
    front_desk.astr_context = SimpleNamespace(
        get_using_provider=lambda chat_id: provider
    )
    front_desk.context = SimpleNamespace(astr_context=front_desk.astr_context)
    return front_desk


def _request(image_urls: list[str]):
    return SimpleNamespace(
        contexts=[{"role": "user", "content": "old"}],
        prompt="old prompt",
        image_urls=image_urls,
        extra_user_content_parts=[],
        system_prompt="",
    )


def _image(url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url}}


class _CaptionLedger:
    def __init__(self):
        self.generated = 0
        self.checked = 0

    async def generate_captions_for_chat(self, **kwargs):
        self.generated += 1
        return 1

    async def process_image_captions_if_needed(self, **kwargs):
        self.checked += 1
        return 0


def test_preserves_current_image_urls_when_provider_supports_images():
    front_desk = _front_desk(supports_image=True, image_caption_provider_id="caption")
    req = _request(["file:///tmp/current-a.png", "file:///tmp/current-b.png"])

    front_desk._update_request(
        req,
        contexts=[],
        final_prompt="看看这两张 [图片1] [图片2]",
        alias="AngelHeart",
        preserve_current_image_urls=front_desk._should_preserve_current_image_urls("chat"),
    )

    assert req.prompt == "看看这两张 [图片1] [图片2]"
    assert req.image_urls == [
        "file:///tmp/current-a.png",
        "file:///tmp/current-b.png",
    ]


def test_clears_current_image_urls_when_provider_cannot_receive_direct_images():
    front_desk = _front_desk(supports_image=False, image_caption_provider_id="caption")
    req = _request(["file:///tmp/current.png"])

    front_desk._update_request(
        req,
        contexts=[],
        final_prompt="纯文本模型只看转述 [图片1]",
        alias="AngelHeart",
        preserve_current_image_urls=front_desk._should_preserve_current_image_urls("chat"),
    )

    assert req.image_urls == []


def test_supported_provider_does_not_force_caption_generation():
    front_desk = _front_desk(supports_image=True, image_caption_provider_id="caption")
    ledger = _CaptionLedger()
    front_desk.context = SimpleNamespace(conversation_ledger=ledger)

    caption_count = asyncio.run(
        front_desk._ensure_image_captions_for_request(
            "chat",
            force_caption=not front_desk._should_preserve_current_image_urls("chat"),
        )
    )

    assert caption_count == 0
    assert ledger.generated == 0
    assert ledger.checked == 1


def test_unconfigured_provider_modalities_are_treated_as_image_capable():
    for modalities in (None, [], _MISSING_MODALITIES):
        front_desk = _front_desk(
            supports_image=False,
            image_caption_provider_id="caption",
            modalities=modalities,
        )

        assert front_desk._should_preserve_current_image_urls("chat") is True


def test_filter_images_keeps_images_when_modalities_are_unconfigured():
    front_desk = _front_desk(supports_image=False, modalities=[])
    contexts = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                _image("file:///tmp/a.png"),
            ],
        }
    ]

    filtered = front_desk.filter_images_for_provider("chat", contexts)

    assert filtered[0]["content"][1]["type"] == "image_url"


def test_ledger_does_not_caption_images_when_provider_modalities_are_unconfigured():
    ledger = object.__new__(ConversationLedger)
    ledger.get_context_snapshot = lambda chat_id: (
        [],
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    _image("file:///tmp/a.png"),
                ],
            }
        ],
        0,
    )
    astr_context = SimpleNamespace(
        get_using_provider=lambda chat_id: SimpleNamespace(provider_config={})
    )

    assert ledger.should_process_images("chat", astr_context) is False


def test_preserves_current_image_urls_when_provider_supports_images_even_if_captioning_is_configured():
    front_desk = _front_desk(supports_image=True, image_caption_provider_id="caption")
    req = _request(["file:///tmp/current.png"])

    front_desk._update_request(
        req,
        contexts=[],
        final_prompt="多模态模型直接看图 [图片1]",
        alias="AngelHeart",
        preserve_current_image_urls=front_desk._should_preserve_current_image_urls("chat"),
    )

    assert req.image_urls == ["file:///tmp/current.png"]


def test_final_prompt_numbers_multiple_images_across_aggregated_messages():
    recent_dialogue = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "帮我看看 [图片]"},
                _image("data:image/png;base64,IMAGE_A"),
            ],
            "sender_name": "小明",
            "sender_id": "123456",
            "chat_id": "aiocqhttp:GroupMessage:10000",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "还有这两张"},
                _image("data:image/png;base64,IMAGE_B"),
                _image("data:image/png;base64,IMAGE_C"),
            ],
            "sender_name": "小红",
            "sender_id": "456789",
            "chat_id": "aiocqhttp:GroupMessage:10000",
        },
    ]

    prompt = format_final_prompt(recent_dialogue, decision=None, alias="AngelHeart")

    assert "[群友: 小明 (ID: 123456)]: 帮我看看 [图片1]" in prompt
    assert "[群友: 小红 (ID: 456789)]: 还有这两张 [图片2] [图片3]" in prompt
    assert "base64" not in prompt
    assert "IMAGE_A" not in prompt


def test_appends_non_current_aggregated_images_as_extra_content_parts():
    front_desk = _front_desk(supports_image=True)
    req = _request(["file:///tmp/current.png"])
    recent_dialogue = [
        {
            "source_event_id": "old-event",
            "content": [
                {"type": "text", "text": "前一条"},
                _image("data:image/png;base64,OLD_IMAGE"),
            ],
        },
        {
            "source_event_id": "current-event",
            "content": [
                {"type": "text", "text": "当前条"},
                _image("data:image/png;base64,CURRENT_LEDGER_IMAGE"),
            ],
        },
    ]

    extra_urls = front_desk._collect_non_current_image_urls(
        recent_dialogue, "current-event"
    )
    front_desk._update_request(
        req,
        contexts=[],
        final_prompt="前一条 [图片1]\n当前条 [图片2]",
        alias="AngelHeart",
        preserve_current_image_urls=True,
        extra_image_urls=extra_urls,
    )

    assert req.image_urls == ["file:///tmp/current.png"]
    assert len(req.extra_user_content_parts) == 1
    assert req.extra_user_content_parts[0].image_url.url == "data:image/png;base64,OLD_IMAGE"
