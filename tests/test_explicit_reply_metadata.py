"""Explicit reply IDs survive request rebuilding and both compaction paths."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger
from astrbot_plugin_angel_heart.core.message_processor import MessageProcessor
from astrbot_plugin_angel_heart.core.utils.reply_metadata import (
    MAX_SUMMARY_REPLY_RELATIONS,
    collect_reply_relations,
    normalize_reference_id,
    normalize_reply_targets,
)
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

from astrbot.core.message.components import Image, Plain, Reply

CHAT = "aiocqhttp:GroupMessage:10001"


def message(source="200", target="100", *, sender="20001", timestamp=1):
    result = {
        "role": "user", "source_message_id": source,
        "sender_id": sender, "sender_name": "成员", "timestamp": timestamp,
        "content": [{"type": "text", "text": "把这张做成 GIF"}],
    }
    if target is not None:
        result["reply_to_message_ids"] = [target]
    return result


def processed_text(msg):
    return MessageProcessor("机器人").process_message(msg)["content"][0]["text"]


@pytest.fixture
def ledger(tmp_path):
    config = SimpleNamespace(
        max_conversation_tokens=0, context_compression_threshold=0.8,
        context_content_retain_tokens=100, context_tool_retain_tokens=50,
        context_forgetting_timeout=0,
    )
    value = ConversationLedger(config, tmp_path)
    yield value
    value.close()


class Event:
    def __init__(self, parts):
        self.parts = parts
        self.extra = {}

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value

    def get_message_outline(self):
        return "[引用] 把这张做成 GIF"

    def get_messages(self):
        return self.parts

    def get_sender_id(self):
        return "20001"

    def get_sender_name(self):
        return "成员"

    def get_self_id(self):
        return "bot1"


def reply(target):
    value = Reply()
    value.id = target
    value.sender_id = "another-user"
    value.message_str = "机器人看看"
    value.chain = [Image()]
    return value


@pytest.mark.asyncio
async def test_cache_records_only_explicit_ids_and_never_downloads_quoted_media():
    front = FrontDesk.__new__(FrontDesk)
    front.context = MagicMock()
    front.config_manager = MagicMock()
    front.config_manager.for_chat.return_value = SimpleNamespace(alias="机器人", focus_instructions="")
    front._get_event_message_id = lambda event: "200"
    front._extract_event_sender_role = lambda event: "群友"
    front._record_chat_source = MagicMock()
    front._build_cached_image_item = AsyncMock()
    front._build_cached_file_text_item = AsyncMock()
    event = Event([reply(" 100 "), Plain("把这张做成 GIF")])

    await front.cache_message(CHAT, event)
    saved = front.context.conversation_ledger.add_message.call_args.args[1]

    assert saved["source_message_id"] == "200"
    assert saved["reply_to_message_ids"] == ["100"]
    assert saved["metadata"]["body_text"] == "把这张做成 GIF"
    assert saved["metadata"]["hits"] == []
    assert saved["is_at_self"] is False
    assert not any(item.get("type") == "image_url" for item in saved["content"])
    front._build_cached_image_item.assert_not_awaited()
    front._build_cached_file_text_item.assert_not_awaited()
    assert "chain" not in saved
    assert "机器人看看" not in str(saved)


@pytest.mark.parametrize("value", [None, True, False, 0, "0", "None", "unknown", {}, [], "x\ny", "<xml>", "x" * 65])
def test_invalid_ids_cannot_become_model_reference_metadata(value):
    assert normalize_reference_id(value) == ""


@pytest.mark.parametrize("value, expected", [(123, "123"), (-12, "-12"), (" 123 ", "123"), ("msg:abcd-1", "msg:abcd-1")])
def test_actual_ids_are_normalized_without_changing_identity(value, expected):
    assert normalize_reference_id(value) == expected


def test_reply_targets_are_deduplicated_and_bounded():
    assert normalize_reply_targets([1, "1", "2", None, "3", "4", "5"]) == ["1", "2", "3", "4"]
    assert normalize_reply_targets("100") == []


def test_main_model_sees_current_id_and_target_with_distinct_labels():
    original = message()
    text = processed_text(original)
    assert "[本消息 ID: 200]" in text
    assert "[本消息引用的目标消息 ID: 100]" in text
    assert original["source_message_id"] == "200"
    assert original["reply_to_message_ids"] == ["100"]
    assert "reply_to_message_ids" not in MessageProcessor("机器人").process_message(original)


def test_unquoted_legacy_message_has_no_fabricated_target():
    text = processed_text(message(target=None))
    assert "引用的目标" not in text
    assert "把这张做成 GIF" in text


@pytest.mark.parametrize("role", ["tool", "assistant"])
def test_native_tool_and_assistant_payloads_remain_unchanged(role):
    payload = {"role": role, "content": "工具结果或回复", "reply_to_message_ids": ["100"]}
    if role == "tool":
        payload["tool_call_id"] = "call-1"
    else:
        payload["tool_calls"] = [{"id": "call-1", "type": "function", "function": {"name": "draw", "arguments": "{}"}}]
    assert MessageProcessor("机器人").process_message(payload) == payload


def test_rebuilt_image_message_retains_single_original_media_part():
    source = message()
    source["content"].append({"type": "image_url", "image_url": {"url": "/tmp/fixture.webp"}})
    result = MessageProcessor("机器人").process_message(source)
    assert sum(part["type"] == "image_url" for part in result["content"]) == 1
    assert "引用的目标消息 ID: 100" in result["content"][0]["text"]


def test_group_compaction_preserves_explicit_mapping_and_sender(ledger):
    ledger.add_message(CHAT, message())
    ledger.add_message(CHAT, message("201", None, timestamp=2))
    assert ledger.organize_on_group_enter(CHAT, keep_from_timestamp=2)
    summary, retained = ledger.get_formal_context(CHAT)
    assert retained["source_message_id"] == "201"
    assert summary["reply_relations"] == [{
        "source_message_id": "200", "reply_to_message_ids": ["100"], "sender_id": "20001",
    }]
    assert "消息 ID=200; 引用目标消息 ID=100" in processed_text(summary)


def test_private_summary_keeps_link_even_if_llm_omits_it(ledger):
    chat = "aiocqhttp:FriendMessage:20001"
    ledger.add_message(chat, message())
    ledger.add_message(chat, message("201", None, timestamp=2))
    assert ledger._commit_summary_and_block(chat, summary_text="用户正在制作动图", keep_tools=True, keep_count=1)
    summary = ledger.get_formal_context(chat)[0]
    assert "消息 ID=200; 引用目标消息 ID=100" in processed_text(summary)
    assert "20001" in processed_text(summary)


def test_repeated_compaction_retains_latest_bounded_links_per_chat(ledger):
    ledger.add_message(CHAT, message())
    ledger.add_message(CHAT, message("201", None, timestamp=2))
    assert ledger.organize_on_group_enter(CHAT, keep_from_timestamp=2)
    for i in range(3, 28):
        ledger.add_message(CHAT, message(str(200 + i), str(100 + i), timestamp=i))
    assert ledger.organize_on_group_enter(CHAT, keep_from_timestamp=27)
    summary = ledger.get_formal_context(CHAT)[0]
    links = summary["reply_relations"]
    assert len(links) == MAX_SUMMARY_REPLY_RELATIONS
    assert links[-1]["source_message_id"] == "226"
    assert links[0]["source_message_id"] == "211"
    other = "aiocqhttp:GroupMessage:another"
    ledger.add_message(other, message("900", "800", sender="other-user"))
    assert "800" not in processed_text(summary)
    assert "100" not in processed_text(summary)


def test_summary_link_collection_deduplicates_without_recursive_expansion():
    source = message()
    inherited = {"reply_relations": collect_reply_relations([source])}
    assert collect_reply_relations([source, inherited, source]) == collect_reply_relations([source])


def test_rule_compaction_keeps_links_from_retained_old_summary(ledger):
    source = message()
    old = ledger._make_summary_message("earlier facts", 1, reply_relations=[source])
    ledger.add_message(CHAT, old)
    ledger.add_message(CHAT, message("201", None, timestamp=2))
    ledger._get_or_create_ledger(CHAT)["current_summary"] = "earlier facts"
    assert ledger.organize_context(CHAT)
    assert ledger.get_formal_context(CHAT)[0]["reply_relations"] == collect_reply_relations([source])


def test_rendered_reply_metadata_is_counted_by_both_estimators(ledger):
    source = ledger._make_summary_message("earlier facts", 1, reply_relations=[message()])
    without = {k: v for k, v in source.items() if k != "reply_relations"}
    single_delta = ledger._count_message_tokens(source) - ledger._count_message_tokens(without)
    assert single_delta > 0
    ledger.set_messages(CHAT, [without])
    before = ledger._estimate_tokens(CHAT)
    ledger.set_messages(CHAT, [source])
    assert ledger._estimate_tokens(CHAT) - before == single_delta


def test_reply_summary_metadata_reserves_content_budget(ledger):
    ledger.MIN_RETAIN_COUNT = 0
    ledger.config_manager.context_content_retain_tokens = 10
    ledger.add_message(CHAT, message())
    ledger.add_message(CHAT, message("201", None, timestamp=2))
    assert ledger.organize_context(CHAT)
    assert len(ledger.get_all_messages(CHAT)) == 1
    assert ledger.get_all_messages(CHAT)[0]["reply_relations"]


def test_assistant_reference_target_has_id_and_body_without_native_payload_changes():
    from astrbot_plugin_angel_heart.core.utils.xml_formatter import format_message_to_text
    source = {"role": "assistant", "source_message_id": "100", "content": "synthetic answer"}
    assert "[本消息 ID: 100]" in format_message_to_text(source)
    assert "synthetic answer" in format_message_to_text(source)
    result = MessageProcessor("机器人").process_message(source)
    assert result["role"] == "assistant"
    assert "[本消息 ID: 100]" in result["content"]
    assert result["content"].endswith("synthetic answer")
    parts = [{"type": "think", "think": "synthetic reasoning"}, {"type": "text", "text": "synthetic answer"}]
    result = MessageProcessor("机器人").process_message({**source, "content": parts})
    assert result["content"][1:] == parts
    assert source["content"] == "synthetic answer"


@pytest.mark.parametrize("parts, expected", [
    ([{"type": "reply", "data": {"id": "100"}}, {"type": "reply", "data": {"id": "100"}}], ["100"]),
    ([{"type": "reply", "data": None}, {"type": "reply", "data": {"id": True}}], []),
])
def test_qq_history_preserves_valid_explicit_targets(parts, expected):
    front = FrontDesk.__new__(FrontDesk)
    raw = {"sender": {"user_id": "20001", "nickname": "synthetic member"}, "self_id": "30001",
           "message_id": "200", "time": 1, "message": parts + [{"type": "text", "data": {"text": "synthetic body"}}]}
    converted = front._convert_raw_qq_message_to_angelheart_format(raw)
    assert converted["content"] == "synthetic body"
    assert converted.get("reply_to_message_ids", []) == expected
