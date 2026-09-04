from __future__ import annotations

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


def _window_front_desk() -> FrontDesk:
    front_desk = _front_desk(supports_image=True)
    front_desk.context.debounce_manager = SimpleNamespace(
        get_start_message_id=lambda event: event.get("start_id", ""),
        get_end_message_id=lambda event: event.get("end_id", ""),
    )
    return front_desk


def _image(url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url}}


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
    front_desk = _window_front_desk()
    req = _request(["file:///tmp/current.png"])
    recent_dialogue = [
        {
            "source_message_id": "old-event",
            "content": [
                {"type": "text", "text": "前一条"},
                _image("data:image/png;base64,OLD_IMAGE"),
            ],
        },
        {
            "source_message_id": "start-event",
            "content": [
                {"type": "text", "text": "防抖起点"},
                _image("data:image/png;base64,AGGREGATED_IMAGE"),
            ],
        },
        {
            "source_message_id": "current-event",
            "content": [
                {"type": "text", "text": "当前条"},
                _image("data:image/png;base64,CURRENT_LEDGER_IMAGE"),
            ],
        },
    ]
    event = {"start_id": "start-event", "end_id": "current-event"}

    # 防抖窗口 = start-event ~ current-event，窗口外 old-event 的图不应被收集
    extra_urls = front_desk._collect_debounce_window_extra_image_urls(
        recent_dialogue, event, "current-event"
    )
    front_desk._update_request(
        req,
        contexts=[],
        final_prompt="防抖起点 [图片1]\n当前条 [图片2]",
        alias="AngelHeart",
        preserve_current_image_urls=True,
        extra_image_urls=extra_urls,
    )

    assert req.image_urls == ["file:///tmp/current.png"]
    # 只补防抖窗口内非当前的 AGGREGATED_IMAGE，窗口外 OLD_IMAGE 不应被搬到附件
    assert [p.image_url.url for p in req.extra_user_content_parts] == [
        "data:image/png;base64,AGGREGATED_IMAGE"
    ]


def test_window_history_images_are_not_moved_to_current_attachment():
    """离场应答的根因回归：防抖窗口收窄后，窗口外历史图片不得再挂到最新附件。"""
    front_desk = _window_front_desk()
    recent_dialogue = [
        {"source_message_id": "old-1", "content": [{"type": "text", "text": "一小时前的聊天"}]},
        {
            "source_message_id": "old-2",
            "content": [
                {"type": "text", "text": "老图"},
                _image("data:image/png;base64,OLD_HISTORY_IMAGE"),
            ],
        },
        {"source_message_id": "start-event", "content": [{"type": "text", "text": "防抖窗口开始"}]},
        {"source_message_id": "current-event", "content": [{"type": "text", "text": "这一个"}]},
    ]
    event = {"start_id": "start-event", "end_id": "current-event"}

    extra_urls = front_desk._collect_debounce_window_extra_image_urls(
        recent_dialogue, event, "current-event"
    )

    # 窗口内不含图片，历史里的 OLD_HISTORY_IMAGE 属于窗口外，必须被排除
    assert extra_urls == []
