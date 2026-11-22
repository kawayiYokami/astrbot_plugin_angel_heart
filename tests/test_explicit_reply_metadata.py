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
        "role": "user",
        "source_message_id": source,
        "sender_id": sender,
        "sender_name": "成员",
        "timestamp": timestamp,
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
        max_conversation_tokens=0,
        context_compression_threshold=0.8,
        context_content_retain_tokens=100,
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
    front.config_manager.for_chat.return_value = SimpleNamespace(
        alias="机器人", focus_instructions=""
    )
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


@pytest.mark.parametrize(
    "value",
    [None, True, False, 0, "0", "None", "unknown", {}, [], "x\ny", "<xml>", "x" * 65],
)
def test_invalid_ids_cannot_become_model_reference_metadata(value):
    assert normalize_reference_id(value) == ""


@pytest.mark.parametrize(
    "value, expected",
    [(123, "123"), (-12, "-12"), (" 123 ", "123"), ("msg:abcd-1", "msg:abcd-1")],
)
def test_actual_ids_are_normalized_without_changing_identity(value, expected):
    assert normalize_reference_id(value) == expected


def test_reply_targets_are_deduplicated_and_bounded():
    assert normalize_reply_targets([1, "1", "2", None, "3", "4", "5"]) == [
        "1",
        "2",
        "3",
        "4",
    ]
    assert normalize_reply_targets("100") == []


def test_main_model_sees_current_id_and_target_with_distinct_labels():
    original = message()
    text = processed_text(original)
    assert "[本消息 ID: 200]" in text
    assert "[本消息引用的目标消息 ID: 100]" in text
    assert original["source_message_id"] == "200"
    assert original["reply_to_message_ids"] == ["100"]
    assert "reply_to_message_ids" not in MessageProcessor("机器人").process_message(
        original
    )


def test_unquoted_legacy_message_has_no_fabricated_target():
    text = processed_text(message(target=None))
    assert "引用的目标" not in text
    assert "把这张做成 GIF" in text


@pytest.mark.parametrize("role", ["tool", "assistant"])
def test_native_tool_and_assistant_payloads_remain_unchanged(role):
    payload = {
        "role": role,
        "content": "工具结果或回复",
        "reply_to_message_ids": ["100"],
    }
    if role == "tool":
        payload["tool_call_id"] = "call-1"
    else:
        payload["tool_calls"] = [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "draw", "arguments": "{}"},
            }
        ]
    assert MessageProcessor("机器人").process_message(payload) == payload


def test_rebuilt_image_message_retains_single_original_media_part():
    source = message()
    source["content"].append(
        {"type": "image_url", "image_url": {"url": "/tmp/fixture.webp"}}
    )
    result = MessageProcessor("机器人").process_message(source)
    assert sum(part["type"] == "image_url" for part in result["content"]) == 1
    assert "引用的目标消息 ID: 100" in result["content"][0]["text"]


def test_group_compaction_preserves_explicit_mapping_and_sender(ledger):
    """窗口内的消息原样保留：ID、引用目标、发送者都不动。"""
    ledger.add_message(CHAT, message("200", "100", timestamp=1))
    ledger.add_message(CHAT, message("201", "200", timestamp=2))
    assert ledger.organize_on_group_enter(CHAT, keep_from_timestamp=2)
    retained = ledger.get_formal_context(CHAT)
    # 触发点之前的滑出，之后原样留在窗口
    assert [m["source_message_id"] for m in retained] == ["201"]
    assert retained[0]["reply_to_message_ids"] == ["200"]
    assert retained[0]["sender_id"] == "20001"
    assert "本消息引用的目标消息 ID: 200" in processed_text(retained[0])


def test_private_summary_keeps_link_even_if_llm_omits_it(ledger):
    """摘要消息自己带引用关系，不依赖模型写出来。"""
    summary = ledger._make_summary_message(
        "用户正在制作动图", 2.0, None, [message()]
    )
    assert "消息 ID=200; 引用目标消息 ID=100" in processed_text(summary)
    assert "20001" in processed_text(summary)


def test_repeated_compaction_keeps_each_link_on_its_own_message(ledger):
    """多次整理后，每条消息只带自己的引用目标，不跨消息聚合。"""
    ledger.add_message(CHAT, message(timestamp=1))
    for i in range(2, 28):
        ledger.add_message(CHAT, message(str(200 + i), str(100 + i), timestamp=i))
    assert ledger.organize_on_group_enter(CHAT, keep_from_timestamp=27)
    retained = ledger.get_formal_context(CHAT)
    assert retained
    # 每条消息只带自己的一个引用目标，不递归膨胀
    assert all(len(m.get("reply_to_message_ids", [])) == 1 for m in retained)
    targets = [m["reply_to_message_ids"][0] for m in retained]
    assert len(set(targets)) == len(targets)
    # 别的群的引用不会混进来
    other = "aiocqhttp:GroupMessage:another"
    ledger.add_message(other, message("900", "800", sender="other-user"))
    text = "".join(processed_text(m) for m in retained)
    assert "800" not in text


def test_summary_link_collection_deduplicates_without_recursive_expansion():
    source = message()
    inherited = {"reply_relations": collect_reply_relations([source])}
    assert collect_reply_relations(
        [source, inherited, source]
    ) == collect_reply_relations([source])


def test_group_rule_compaction_clears_old_summary(ledger):
    """群聊走滑动窗口：旧摘要不再参与上下文，current_summary 被清空。"""
    ledger._get_or_create_ledger(CHAT)["current_summary"] = "earlier facts"
    for i in range(1, 21):
        ledger.add_message(CHAT, message(str(200 + i), str(100 + i), timestamp=i))
    assert ledger.organize_context(CHAT)
    assert ledger.get_current_summary(CHAT) == ""
    formal = ledger.get_formal_context(CHAT)
    assert formal
    # 不再产出任何摘要形态的消息，引用关系也不聚合
    assert all(m.get("kind") not in ledger.SUMMARY_KINDS for m in formal)
    assert all("reply_relations" not in m for m in formal)


def test_rendered_reply_metadata_is_counted_by_both_estimators(ledger):
    source = ledger._make_summary_message(
        "earlier facts", 1, reply_relations=[message()]
    )
    without = {k: v for k, v in source.items() if k != "reply_relations"}
    single_delta = ledger._count_message_tokens(source) - ledger._count_message_tokens(
        without
    )
    assert single_delta > 0
    ledger.set_messages(CHAT, [without])
    before = ledger._estimate_tokens(CHAT)
    ledger.set_messages(CHAT, [source])
    assert ledger._estimate_tokens(CHAT) - before == single_delta


def test_reply_targets_follow_retained_messages_only(ledger):
    """引用关系跟着保留的消息走，滑出的连引用一起消失。"""
    ledger.config_manager.context_content_retain_tokens = 60
    for i in range(1, 11):
        ledger.add_message(CHAT, message(str(200 + i), str(100 + i), timestamp=i))
    assert ledger.organize_context(CHAT)
    retained = ledger.get_all_messages(CHAT)
    assert retained
    # 留下的每条都带自己的引用目标
    for m in retained:
        assert m["reply_to_message_ids"] == [str(int(m["source_message_id"]) - 100)]
    # 被滑出的那条查不到
    assert "201" not in {m["source_message_id"] for m in retained}


def test_assistant_reference_target_has_id_and_body_without_native_payload_changes():
    from astrbot_plugin_angel_heart.core.utils.xml_formatter import (
        format_message_to_text,
    )

    source = {
        "role": "assistant",
        "source_message_id": "100",
        "content": "synthetic answer",
    }
    assert "[本消息 ID: 100]" in format_message_to_text(source)
    assert "synthetic answer" in format_message_to_text(source)
    result = MessageProcessor("机器人").process_message(source)
    assert result["role"] == "assistant"
    assert "[本消息 ID: 100]" in result["content"]
    assert result["content"].endswith("synthetic answer")
    parts = [
        {"type": "think", "think": "synthetic reasoning"},
        {"type": "text", "text": "synthetic answer"},
    ]
    result = MessageProcessor("机器人").process_message({**source, "content": parts})
    assert result["content"][1:] == parts
    assert source["content"] == "synthetic answer"


@pytest.mark.parametrize(
    "parts, expected",
    [
        (
            [
                {"type": "reply", "data": {"id": "100"}},
                {"type": "reply", "data": {"id": "100"}},
            ],
            ["100"],
        ),
        (
            [{"type": "reply", "data": None}, {"type": "reply", "data": {"id": True}}],
            [],
        ),
    ],
)
def test_qq_history_preserves_valid_explicit_targets(parts, expected):
    front = FrontDesk.__new__(FrontDesk)
    raw = {
        "sender": {"user_id": "20001", "nickname": "synthetic member"},
        "self_id": "30001",
        "message_id": "200",
        "time": 1,
        "message": parts + [{"type": "text", "data": {"text": "synthetic body"}}],
    }
    converted = front._convert_raw_qq_message_to_angelheart_format(raw)
    assert converted["content"] == "synthetic body"
    assert converted.get("reply_to_message_ids", []) == expected
