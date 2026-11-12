"""Echoes count matching user text within the triggering event's window."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_angel_heart.core import angel_heart_status as status_module
from astrbot_plugin_angel_heart.core.angel_heart_status import StatusChecker


NOW = 1000.0


def message(identity, content="same", *, role="user", timestamp=NOW, **extra):
    return {
        "role": role,
        "source_message_id": identity,
        "content": content,
        "timestamp": timestamp,
        **extra,
    }


def checker(monkeypatch, messages, *, threshold=3, cooldown=False, dense=False):
    monkeypatch.setattr(status_module, "time", SimpleNamespace(time=lambda: NOW))
    config = SimpleNamespace(
        leave_echo_reply=True,
        leave_dense_reply=dense,
        echo_detection_threshold=threshold,
        echo_detection_window=90,
        dense_conversation_window=90,
        dense_conversation_threshold=1,
        min_participant_count=1,
    )
    context = SimpleNamespace(
        conversation_ledger=SimpleNamespace(get_all_messages=lambda _chat: messages),
        is_leave_reply_in_cooldown=lambda _chat: cooldown,
    )
    return StatusChecker(SimpleNamespace(for_chat=lambda _chat: config), context)


def test_old_repetitions_do_not_trigger_an_unrelated_latest_message(monkeypatch):
    value = checker(monkeypatch, [message(str(i)) for i in range(3)] + [message("new", "new topic")])
    assert value.get_leave_reply_trigger("g") == ""


@pytest.mark.parametrize("threshold", [1, 2, 3, 4])
def test_configured_threshold_includes_current_event(monkeypatch, threshold):
    value = checker(monkeypatch, [message(str(i)) for i in range(threshold)], threshold=threshold)
    assert value.get_leave_reply_trigger("g") == "echo_chamber"


def test_later_concurrent_messages_do_not_raise_current_event_count(monkeypatch):
    value = checker(monkeypatch, [message(str(i)) for i in range(4)])
    assert value.get_leave_reply_trigger("g", message_id="1") == ""
    assert value.get_leave_reply_trigger("g", message_id="2") == "echo_chamber"


@pytest.mark.parametrize("identity", ["", "missing", "assistant", "summary"])
def test_explicit_unknown_or_nonuser_event_fails_closed(monkeypatch, identity):
    messages = [message(str(i)) for i in range(3)] + [
        message("assistant", role="assistant"),
        message("summary", kind="context_summary"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g", message_id=identity) == ""


def test_duplicate_source_ids_only_count_once(monkeypatch):
    messages = [message("one"), message("one"), message("two")]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


@pytest.mark.parametrize("content", [
    "new topic",
    [{"type": "image_url", "image_url": {"url": "synthetic"}}],
    [{"type": "text", "text": "same"}, {"type": "image_url"}],
    " ",
])
def test_other_content_does_not_reset_window_accumulation(monkeypatch, content):
    messages = [message("one"), message("break", content), message("two"), message("three")]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == "echo_chamber"


@pytest.mark.parametrize("extra", [
    {"role": "assistant"},
    {"role": "tool"},
    {"kind": "context_summary"},
    {"kind": "summary_context"},
    {"kind": "context_compaction"},
])
def test_assistant_tools_and_summaries_do_not_change_accumulation(monkeypatch, extra):
    messages = [message("one"), message("ignored", "other", **extra), message("two")]
    assert checker(monkeypatch, messages, threshold=2).get_leave_reply_trigger("g") == "echo_chamber"


def test_expired_current_event_cannot_reuse_fresh_history(monkeypatch):
    messages = [message("one"), message("two"), message("current", timestamp=NOW - 91)]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_expired_history_does_not_raise_the_threshold(monkeypatch):
    messages = [message("old", timestamp=NOW - 91), message("one"), message("two")]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_text_parts_normalize_only_for_detection(monkeypatch):
    messages = [message("one", " same "), message("two", [{"type": "text", "text": "sa"}, {"type": "text", "text": "me"}])]
    assert checker(monkeypatch, messages, threshold=2).get_leave_reply_trigger("g") == "echo_chamber"
    assert messages[0]["content"] == " same "


def test_marked_nontext_event_does_not_trigger_from_text_placeholder(monkeypatch):
    messages = [message(str(i)) for i in range(3)] + [message("image", echo_eligible=False)]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_legacy_rows_without_source_ids_remain_countable(monkeypatch):
    messages = [message(str(i)) for i in range(3)]
    for item in messages:
        item.pop("source_message_id")
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == "echo_chamber"


def test_existing_cooldown_and_dense_fallback_remain(monkeypatch):
    messages = [message(str(i), sender_id="someone") for i in range(3)]
    assert checker(monkeypatch, messages, cooldown=True, dense=True).get_leave_reply_trigger("g") == ""
    messages[-1]["content"] = "other"
    assert checker(monkeypatch, messages, dense=True).get_leave_reply_trigger("g") == "dense_conversation"


@pytest.mark.asyncio
@pytest.mark.parametrize("has_image", [False, True])
async def test_cached_failed_image_is_marked_ineligible(has_image):
    from astrbot.core.message.components import Image, Plain
    from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

    components = [Plain("same")]
    if has_image:
        components.append(Image.fromFileSystem("/tmp/synthetic-image.png"))
    event = SimpleNamespace(
        message_obj=SimpleNamespace(message_id="current"),
        get_message_outline=lambda: "same",
        get_messages=lambda: components,
        get_sender_id=lambda: "sender",
        get_sender_name=lambda: "Sender",
        get_self_id=lambda: "bot",
        get_timestamp=lambda: NOW,
        set_extra=MagicMock(),
    )
    front = object.__new__(FrontDesk)
    front.context = SimpleNamespace(conversation_ledger=MagicMock())
    front.config_manager = SimpleNamespace(for_chat=lambda _chat: SimpleNamespace(alias="", focus_instructions=""))
    front._record_chat_source = MagicMock()
    front._build_cached_image_item = AsyncMock(return_value=None)

    await front.cache_message("g", event)

    persisted = front.context.conversation_ledger.add_message.call_args.args[1]
    assert persisted["echo_eligible"] is not has_image
    assert persisted["source_message_id"] == "current"
    if has_image:
        assert persisted["content"][-1] == {"type": "text", "text": "[图片处理失败]"}


@pytest.mark.asyncio
async def test_scheduler_uses_its_own_event_when_later_messages_already_arrived(monkeypatch):
    from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

    messages = [message(str(i)) for i in range(4)]
    value = checker(monkeypatch, messages)
    value.is_event_wake = lambda _event: False
    schedule = AsyncMock(return_value=None)
    front = object.__new__(FrontDesk)
    front.status_checker = value
    front.context = SimpleNamespace(
        is_present=lambda _chat: False,
        debounce_manager=SimpleNamespace(schedule=schedule),
    )
    front.config_manager = SimpleNamespace(for_chat=lambda _chat: SimpleNamespace(enter_on_mention_only=True))
    front._check_and_handle_timeout = AsyncMock()
    event = SimpleNamespace(
        unified_msg_origin="g",
        message_obj=SimpleNamespace(message_id="1"),
        get_sender_id=lambda: "sender",
        stop_event=MagicMock(),
    )

    await front._schedule_group_debounce(event)

    assert schedule.call_args.kwargs["message_id"] == "1"
    assert schedule.call_args.kwargs["leave_reply_trigger"] == ""
    event.stop_event.assert_called_once()
