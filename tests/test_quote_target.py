"""引用目标协议测试：纯函数、请求编号、发送装饰与账本清理。

覆盖 PLAN_quote_target_plugin_managed.md 第 6.1 节的必测场景。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from astrbot.core.message.components import At, Image, Plain, Reply
from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger
from astrbot_plugin_angel_heart.core.message_processor import MessageProcessor
from astrbot_plugin_angel_heart.core.quote_target import (
    classify_missing_target,
    collect_known_message_ids,
    count_gap_messages,
    is_group_member_message,
    is_quotable_message,
    parse_prefix_marker,
    register_quote_target,
    strip_markers_from_content,
    strip_quote_markers,
)
from astrbot_plugin_angel_heart.core.utils.context_utils import format_final_prompt
from astrbot_plugin_angel_heart.core.utils.xml_formatter import format_message_to_text
from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

GROUP_CHAT_ID = "aiocqhttp:GroupMessage:10000"


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------


def _user(mid, text, ts=1.0, name="甲"):
    return {
        "role": "user",
        "content": text,
        "sender_name": name,
        "sender_id": "1001",
        "timestamp": ts,
        "chat_id": GROUP_CHAT_ID,
        "source_message_id": mid,
    }


def _assistant(text, ts=1.5, mid="m-a", tool_calls=None):
    message = {
        "role": "assistant",
        "content": text,
        "sender_name": "assistant",
        "sender_id": "bot",
        "timestamp": ts,
        "chat_id": GROUP_CHAT_ID,
        "source_message_id": mid,
    }
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return message


def _contexts_text(contexts):
    parts = []
    for message in contexts:
        content = message.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.append(
                "".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict)
                )
            )
    return "\n".join(parts)


class _RewriteEvent:
    def __init__(self, message_id, platform="aiocqhttp", origin=GROUP_CHAT_ID):
        self.unified_msg_origin = origin
        self.message_str = ""
        self.message_obj = SimpleNamespace(message_id=message_id)
        self._extras = {}
        self._platform = platform

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_platform_name(self):
        return self._platform


def _front_desk(
    quote_enabled=True,
    threshold=3,
    streaming=False,
    platform=True,
    group_enhancement=True,
):
    config = MagicMock()
    config.for_chat.return_value = config
    config.alias = "fairy"
    config.image_caption_provider_id = ""
    config.focus_instructions = "分析 总结 好好想想 为什么 到底"
    config.normal_reply_max_chars = 20
    config.focus_reply_max_chars = 200
    config.quote_target_enabled = quote_enabled
    config.group_chat_enhancement = group_enhancement
    config.quote_min_gap_messages = threshold
    angel = MagicMock()
    angel.work_ledger = WorkLedger()
    angel.astr_context = MagicMock()
    angel.astr_context.get_config.return_value = {
        "provider_settings": {"streaming_response": streaming},
        "platform_settings": {"reply_with_quote": False, "reply_with_mention": False},
    }
    angel.conversation_ledger.get_all_messages.return_value = []
    angel.conversation_ledger.get_quote_messages.return_value = []
    angel.conversation_ledger.get_current_summary.return_value = ""
    fd = FrontDesk(config, angel)
    fd._provider_supports_images = MagicMock(return_value=False)
    fd.filter_images_for_provider = MagicMock(side_effect=lambda _chat_id, ctx: ctx)
    return fd, angel


def _run_rewrite(fd, event, req, recent_dialogue, historical_context=None):
    event.set_extra(
        "angelheart_decision_context",
        {
            "recent_dialogue": recent_dialogue,
            "historical_context": historical_context or [],
            "boundary_ts": 3.0,
        },
    )
    asyncio.run(fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req))


def _req():
    return SimpleNamespace(
        contexts=[],
        prompt="",
        image_urls=[],
        extra_user_content_parts=[],
        system_prompt="BASE SYSTEM",
    )


class _DecorateEvent:
    """装饰钩子事件桩：带 extras、结果链与平台/发送者信息。"""

    def __init__(self, chain, extras=None, streaming=False, origin=GROUP_CHAT_ID):
        self.unified_msg_origin = origin
        self._extras = dict(extras or {})
        self._chain = chain
        self.message_obj = SimpleNamespace(message_id="m-boundary")
        type_name = "STREAMING_RESULT" if streaming else "LLM_RESULT"
        self._result = SimpleNamespace(
            chain=chain, result_content_type=SimpleNamespace(name=type_name)
        )

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_result(self):
        return self._result

    def new_result(self, chain, streaming=False):
        """模拟同一事件的下一条结果（工具链会多次进入装饰钩子）。"""
        type_name = "STREAMING_RESULT" if streaming else "LLM_RESULT"
        self._result = SimpleNamespace(
            chain=chain, result_content_type=SimpleNamespace(name=type_name)
        )
        return self._result

    def get_platform_name(self):
        return "aiocqhttp"

    def get_message_type(self):
        return SimpleNamespace(name="GROUP_MESSAGE")

    def get_sender_id(self):
        return "1001"

    def get_sender_name(self):
        return "甲"


def _quote_extras(mapping, threshold=3, known_ids=None):
    state = {
        "threshold": threshold,
        "processed": False,
    }
    if known_ids is not None:
        state["known_ids"] = list(known_ids)
    return {
        "angelheart_quote_map": dict(mapping),
        "angelheart_quote_state": state,
    }


def _plugin(
    threshold=3,
    ledger=None,
    reply_with_mention=False,
    reply_with_quote=False,
    quote_enabled=True,
    host_config_extra=None,
    session_config=None,
):
    from astrbot_plugin_angel_heart.main import AngelHeartPlugin

    plugin = object.__new__(AngelHeartPlugin)
    plugin.config_manager = MagicMock()
    plugin.config_manager.strip_markdown_enabled = False
    plugin.config_manager.strip_period_before_newline = False
    plugin.config_manager.quote_target_enabled = quote_enabled
    plugin.config_manager.quote_min_gap_messages = threshold
    plugin.config_manager.for_chat.return_value = plugin.config_manager
    plugin.context = MagicMock()
    host_config = {
        "platform_settings": {
            "reply_with_mention": reply_with_mention,
            "reply_with_quote": reply_with_quote,
        },
        "provider_settings": {"streaming_response": False},
    }
    host_config.update(host_config_extra or {})
    plugin.context.get_config.return_value = host_config
    if session_config is not None:
        # 宿主按会话绑定配置档案：带 umo 的读取与会话档案一致，不带 umo 才是默认配置
        plugin.context.get_config.side_effect = (
            lambda umo=None: dict(session_config) if umo else host_config
        )
    plugin.angel_context = MagicMock()
    plugin.angel_context.conversation_ledger.get_quote_messages.return_value = list(
        ledger or []
    )
    plugin.angel_context.debounce_manager.charge_reply_energy = AsyncMock()
    plugin._quote_conflict_warned = set()
    plugin._is_upstream_command_event = MagicMock(return_value=False)
    plugin._is_astrbot_error_message = MagicMock(return_value=False)

    class _FakeRuntimeTasks:
        async def run(self, event, fn):
            return await fn()

    plugin._runtime_tasks = _FakeRuntimeTasks()
    return plugin


def _ledger_with_gap(gap, target_ts=None, future=0):
    """构造目标消息 + gap 条后续群友消息的账本快照。"""
    now = time.time()
    target_ts = target_ts if target_ts is not None else now - 60
    messages = [_user("m-target", "目标消息", target_ts)]
    for index in range(gap):
        messages.append(_user(f"m-next-{index}", f"后续{index}", target_ts + 1 + index))
    for index in range(future):
        messages.append(
            _user(f"m-future-{index}", f"未来{index}", now + 1000 + index)
        )
    return messages


@pytest.fixture
def quote_ledger(tmp_path):
    ledger = ConversationLedger(
        ConfigManager({"max_conversation_tokens": 100000, "context_forgetting_timeout": 0}),
        tmp_path,
    )
    try:
        yield ledger
    finally:
        ledger.close()


# ---------------------------------------------------------------------------
# 编号与映射
# ---------------------------------------------------------------------------


def test_numbering_covers_only_quotable_group_messages():
    msg1 = _user("m1", "第一条", 1.0)
    assistant = _assistant("好的")
    tool_old = {
        "role": "user",
        "content": "工具结果",
        "sender_name": "tool_result",
        "timestamp": 1.2,
        "chat_id": GROUP_CHAT_ID,
        "source_message_id": "m-tool",
    }
    summary = {
        "role": "system",
        "content": "摘要",
        "sender_name": "context_summary",
        "kind": "context_summary",
        "timestamp": 0.5,
        "source_message_id": "m-summary",
    }
    msg2 = _user("m2", "第二条", 2.0)
    no_id = {
        "role": "user",
        "content": "无ID消息",
        "sender_name": "乙",
        "timestamp": 2.5,
        "chat_id": GROUP_CHAT_ID,
    }

    quote_map = {}
    texts = [
        format_message_to_text(message, "fairy", quote_map=quote_map)
        for message in (msg1, assistant, tool_old, summary, msg2, no_id)
    ]

    assert quote_map == {1: "m1", 2: "m2"}
    assert texts[0].startswith("[1] ")
    assert "第一条" in texts[0]
    assert texts[1] == "好的"
    assert texts[2].startswith("[系统工具]")
    assert not texts[2].startswith("[1]")
    assert texts[3].startswith("[系统通知]")
    assert texts[4].startswith("[2] ")
    assert "无ID消息" in texts[5] and not texts[5].startswith("[1]")


def test_numbering_does_not_mutate_source_message():
    message = _user("m1", "第一条")
    before = dict(message)
    quote_map = {}
    format_message_to_text(message, "fairy", quote_map=quote_map)
    assert message == before


def test_message_processor_numbers_regular_messages_only():
    processor = MessageProcessor("fairy")
    quote_map = {}

    user_out = processor.process_message(_user("m1", "第一条"), quote_map=quote_map)
    assistant_out = processor.process_message(
        {"role": "assistant", "content": "你好"}, quote_map=quote_map
    )

    assert user_out["content"][0]["text"].startswith("[1] ")
    assert assistant_out["content"] == "你好"
    assert quote_map == {1: "m1"}


def test_message_processor_without_map_keeps_original_format():
    processor = MessageProcessor("fairy")
    out = processor.process_message(_user("m1", "第一条"))
    assert "[1] " not in out["content"][0]["text"]


def test_final_prompt_continues_existing_mapping():
    quote_map = {1: "m1"}
    text = format_final_prompt([_user("m2", "第二条")], None, "fairy", quote_map=quote_map)
    assert text.startswith("[2] ")
    assert quote_map == {1: "m1", 2: "m2"}


# ---------------------------------------------------------------------------
# 请求编排
# ---------------------------------------------------------------------------


def test_group_rewrite_numbers_contexts_then_current_prompt():
    fd, _ = _front_desk()
    req = _req()
    event = _RewriteEvent("m3")
    recent = [_user("m1", "第一条", 1.0), _user("m2", "第二条", 2.0), _user("m3", "第三条", 3.0)]

    _run_rewrite(fd, event, req, recent)

    joined_context = _contexts_text(req.contexts)
    assert "[1] " in joined_context and "第一条" in joined_context
    assert "[2] " in joined_context and "第二条" in joined_context
    assert "第三条" not in joined_context
    assert "[3] " in req.prompt and "第三条" in req.prompt
    assert "<引用说明>" in req.prompt
    assert event.get_extra("angelheart_quote_map") == {1: "m1", 2: "m2", 3: "m3"}
    state = event.get_extra("angelheart_quote_state")
    assert state["threshold"] == 3
    assert state["processed"] is False


def test_group_rewrite_skips_numbering_when_switch_disabled():
    fd, _ = _front_desk(quote_enabled=False)
    req = _req()
    event = _RewriteEvent("m2")
    recent = [_user("m1", "第一条", 1.0), _user("m2", "第二条", 2.0)]

    _run_rewrite(fd, event, req, recent)

    assert "[1] " not in _contexts_text(req.contexts)
    assert not req.prompt.startswith("[2]")
    assert "<引用说明>" not in req.prompt
    assert event.get_extra("angelheart_quote_map") is None
    assert event.get_extra("angelheart_quote_state") is None


def test_group_rewrite_skips_protocol_for_unsupported_platform():
    fd, _ = _front_desk()
    req = _req()
    event = _RewriteEvent("m2", platform="qq_official")
    recent = [_user("m1", "第一条", 1.0), _user("m2", "第二条", 2.0)]

    _run_rewrite(fd, event, req, recent)

    assert "<引用说明>" not in req.prompt
    assert event.get_extra("angelheart_quote_map") is None


def test_group_rewrite_skips_protocol_when_host_streaming_enabled():
    fd, _ = _front_desk(streaming=True)
    req = _req()
    event = _RewriteEvent("m2")
    recent = [_user("m1", "第一条", 1.0), _user("m2", "第二条", 2.0)]

    _run_rewrite(fd, event, req, recent)

    assert "<引用说明>" not in req.prompt
    assert event.get_extra("angelheart_quote_map") is None


def test_rewrite_without_context_publishes_no_mapping():
    fd, _ = _front_desk()
    req = _req()
    event = _RewriteEvent("m3")
    fd._get_decision_context_for_rewrite = MagicMock(return_value=None)

    asyncio.run(fd.rewrite_prompt_for_llm(GROUP_CHAT_ID, event, req))

    assert event.get_extra("angelheart_quote_map") is None
    assert event.get_extra("angelheart_quote_state") is None


def test_invalid_current_prompt_leaves_no_half_mapping():
    fd, _ = _front_desk()
    req = _req()
    event = _RewriteEvent("m-missing")
    recent = [_user("m1", "第一条", 1.0), _user("m2", "第二条", 2.0)]

    _run_rewrite(fd, event, req, recent)

    joined_context = _contexts_text(req.contexts)
    assert "[1] " not in joined_context
    assert "<引用说明>" not in req.prompt
    assert event.get_extra("angelheart_quote_map") is None
    assert event.get_extra("angelheart_quote_state") is None


def test_new_request_restarts_numbering_instead_of_appending():
    fd, _ = _front_desk()
    event = _RewriteEvent("m2")
    first_req = _req()
    _run_rewrite(fd, event, first_req, [_user("m1", "第一条", 1.0), _user("m2", "第二条", 2.0)])
    assert event.get_extra("angelheart_quote_map") == {1: "m1", 2: "m2"}

    second_req = _req()
    _run_rewrite(fd, event, second_req, [_user("m9", "新一条", 9.0), _user("m2", "第二条", 10.0)])

    assert event.get_extra("angelheart_quote_map") == {1: "m9", 2: "m2"}


def test_private_rewrite_never_injects_protocol():
    fd, _ = _front_desk()
    chat_id = "aiocqhttp:FriendMessage:10000"
    fd._ensure_minimum_context = AsyncMock()
    fd._get_decision_context_for_rewrite = MagicMock(
        return_value=([_user("m1", "私聊消息", 1.0)], [], 1.0)
    )
    req = _req()
    event = _RewriteEvent("m1", origin=chat_id)

    asyncio.run(fd.rewrite_prompt_for_llm(chat_id, event, req))

    assert "<引用说明>" not in req.prompt
    assert event.get_extra("angelheart_quote_map") is None


# ---------------------------------------------------------------------------
# 协议解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, number, cleaned",
    [
        ("[引用:2]你好", 2, "你好"),
        ("【引用：2】你好", 2, "你好"),
        ("[引用: 2 ] 你好", 2, "你好"),
        ("  [引用：12]  你好", 12, "你好"),
        ("[引用:0]你好", None, "你好"),
        ("[引用:abc]你好", None, "你好"),
        ("[引用:-1]你好", None, "你好"),
        ("你好", None, "你好"),
        ("我[引用:2]觉得", None, "我[引用:2]觉得"),
        ("", None, ""),
    ],
)
def test_parse_prefix_marker(text, number, cleaned):
    parsed_number, parsed_text = parse_prefix_marker(text)
    assert parsed_number == number
    assert parsed_text == cleaned


@pytest.mark.parametrize(
    "text, cleaned",
    [
        ("[引用:2]你好[引用:x]世界", "你好世界"),
        ("【引用：3】 好", " 好"),
        ("正文提到引用:2但没有括号", "正文提到引用:2但没有括号"),
        ("普通[引用]文字", "普通[引用]文字"),
        ("", ""),
    ],
)
def test_strip_quote_markers(text, cleaned):
    assert strip_quote_markers(text) == cleaned


def test_strip_markers_from_content_keeps_structure():
    content = [
        {"type": "text", "text": "[引用:1]你好"},
        {"type": "image_url", "image_url": {"url": "x"}},
        {"type": "text", "text": "【引用：2】再见"},
    ]
    cleaned = strip_markers_from_content(content)
    assert cleaned[0]["text"] == "你好"
    assert cleaned[1] == {"type": "image_url", "image_url": {"url": "x"}}
    assert cleaned[2]["text"] == "再见"
    assert content[0]["text"] == "[引用:1]你好"


def test_is_group_member_message_excludes_non_member_sources():
    assert is_group_member_message(_user("m1", "hi"))
    assert not is_group_member_message(_assistant("hi"))
    assert not is_group_member_message({"role": "tool", "content": "x"})
    assert not is_group_member_message(
        {"role": "user", "sender_name": "tool_result", "content": "x"}
    )
    assert not is_group_member_message(
        {"role": "system", "kind": "context_summary", "content": "x"}
    )
    assert not is_quotable_message({"role": "user", "content": "x", "sender_name": "甲"})


def test_register_quote_target_requires_id_and_map():
    assert register_quote_target(None, _user("m1", "x")) is None
    assert register_quote_target({}, {"role": "user", "content": "x"}) is None
    quote_map = {}
    assert register_quote_target(quote_map, _user("m1", "x")) == 1
    assert quote_map == {1: "m1"}


# ---------------------------------------------------------------------------
# 间隔计算
# ---------------------------------------------------------------------------


def test_count_gap_counts_only_real_group_messages():
    now = time.time()
    messages = [
        _user("m1", "目标", now - 30),
        _assistant("bot 消息", now - 29),
        {"role": "tool", "content": "tool", "timestamp": now - 28},
        {
            "role": "user",
            "content": "旧式工具",
            "sender_name": "tool_result",
            "timestamp": now - 27,
        },
        {"role": "system", "kind": "context_summary", "content": "摘要", "timestamp": now - 26},
        _user("m2", "群友1", now - 25),
        _user("m3", "群友2", now - 24),
    ]
    assert count_gap_messages(messages, "m1", now) == 2


def test_count_gap_zero_for_latest_message():
    now = time.time()
    messages = [_user("m1", "目标", now - 5), _user("m2", "最新", now - 4)]
    assert count_gap_messages(messages, "m2", now) == 0


def test_count_gap_returns_none_when_target_missing():
    now = time.time()
    assert count_gap_messages([_user("m2", "x", now - 1)], "missing", now) is None
    assert count_gap_messages([], "m1", now) is None
    assert count_gap_messages(None, "m1", now) is None


def test_count_gap_handles_same_and_missing_timestamps():
    now = time.time()
    same_ts = now - 10
    messages = [
        _user("m1", "目标", same_ts),
        _user("m2", "同秒1", same_ts),
        _user("m3", "同秒2", same_ts),
        {"role": "user", "content": "无时间戳", "sender_name": "乙", "source_message_id": "m4"},
        {"role": "user", "content": "零时间戳", "sender_name": "丙", "timestamp": 0, "source_message_id": "m5"},
    ]
    assert count_gap_messages(messages, "m1", now) == 4


def test_count_gap_excludes_future_timestamps():
    now = time.time()
    messages = _ledger_with_gap(3, target_ts=now - 60, future=2)
    assert count_gap_messages(messages, "m-target", now) == 3


def test_collect_known_message_ids_reads_source_message_id():
    now = time.time()
    messages = [
        _user("m1", "甲", now - 5),
        _assistant("bot", now - 4, mid="m-a"),
        {"role": "system", "kind": "context_summary", "content": "摘要", "timestamp": now - 3},
        {"role": "user", "content": "无ID", "sender_name": "乙", "timestamp": now - 2},
        {"role": "user", "content": "空ID", "source_message_id": "  ", "timestamp": now - 1},
        "not-a-dict",
    ]
    assert collect_known_message_ids(messages) == {"m1", "m-a"}
    assert collect_known_message_ids(None) == set()


def test_classify_missing_target_splits_index_eviction_and_missing():
    assert classify_missing_target(["m1", "m2"], "m2") == "quote_index_evicted"
    assert classify_missing_target({"m3"}, "m3") == "quote_index_evicted"
    assert classify_missing_target(["m1"], "m9") == "quote_index_missing"
    assert classify_missing_target([], "m1") == "quote_index_missing"
    assert classify_missing_target(None, "m1") == "quote_index_missing"
    assert classify_missing_target(["m1"], "") == "quote_index_missing"


def test_publish_state_records_only_index_backed_targets():
    fd, angel = _front_desk()
    angel.conversation_ledger.get_quote_messages.return_value = [
        _user("m-keep", "甲", time.time() - 5),
    ]
    event = _RewriteEvent("m-current")

    fd._publish_quote_target_state(
        event, {1: "m-keep", 2: "m-gone"}, 4, GROUP_CHAT_ID
    )

    state = event.get_extra("angelheart_quote_state")
    assert state["threshold"] == 4 and state["known_ids"] == ["m-keep"]
    assert event.get_extra("angelheart_quote_map") == {1: "m-keep", 2: "m-gone"}


def test_publish_state_falls_back_to_empty_when_index_unavailable():
    fd, angel = _front_desk()
    angel.conversation_ledger.get_quote_messages.side_effect = RuntimeError("boom")
    event = _RewriteEvent("m-current")

    fd._publish_quote_target_state(event, {1: "m-keep"}, 3, GROUP_CHAT_ID)

    assert event.get_extra("angelheart_quote_state")["known_ids"] == []


@pytest.mark.parametrize("compact_before_request", [False, True])
def test_quote_survives_compaction_of_target_and_later_messages(
    quote_ledger, compact_before_request
):
    recent = _ledger_with_gap(4)
    quote_ledger.add_messages(GROUP_CHAT_ID, recent)
    fd, angel = _front_desk(threshold=5)
    angel.conversation_ledger = quote_ledger
    request_event = _RewriteEvent(recent[-1]["source_message_id"])

    if compact_before_request:
        assert quote_ledger.organize_context(
            GROUP_CHAT_ID, keep_from_timestamp=time.time()
        )
        assert "m-target" not in collect_known_message_ids(quote_ledger.get_all_messages(GROUP_CHAT_ID))
    _run_rewrite(fd, request_event, _req(), recent)
    state = request_event.get_extra("angelheart_quote_state")
    assert "m-target" in state["known_ids"]

    # 生成期间新到达的消息也被整理，仍须计入间隔。
    quote_ledger.add_messages(GROUP_CHAT_ID, [
        _user("m-new-1", "新消息一", time.time() - 2),
        _user("m-new-2", "新消息二", time.time() - 1),
    ])
    assert quote_ledger.organize_context(
        GROUP_CHAT_ID, keep_from_timestamp=time.time()
    )
    assert not any(is_group_member_message(m) for m in quote_ledger.get_all_messages(GROUP_CHAT_ID))

    plugin = _plugin(threshold=5)
    plugin.angel_context.conversation_ledger = quote_ledger
    chain = [Plain(text="[引用:1]回应原消息")]
    event = _DecorateEvent(chain, request_event._extras)
    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert isinstance(chain[0], Reply) and chain[0].id == "m-target"
    assert chain[1].text == "回应原消息"
    assert event.get_extra("angelheart_quote_state")["gap"] == 6


def test_quote_index_keeps_only_group_member_metadata_and_orders_by_time(quote_ledger):
    now = time.time()
    quote_ledger.add_messages(GROUP_CHAT_ID, [
        _user("later", "后到", now - 1),
        _assistant("机器人", now - 3),
        {"role": "tool", "content": "工具", "timestamp": now - 3},
        _user("tool", "旧工具", now - 3, name="tool_result"),
        dict(_user("summary", "摘要", now - 3), kind="context_summary"),
        _user("target", "先到", now - 4),
        _user("", "没有 ID 也计数", now - 2),
        _user("future", "未来", now + 100),
    ])
    quote_ledger.add_message("aiocqhttp:GroupMessage:other", _user("target", "另一个群", now))
    quote_ledger.add_message("aiocqhttp:FriendMessage:1001", _user("private", "私聊", now))

    snapshot = quote_ledger.get_quote_messages(GROUP_CHAT_ID)
    assert [m["source_message_id"] for m in snapshot] == ["target", "", "later", "future"]
    assert all(set(m) == {"role", "source_message_id", "timestamp"} for m in snapshot)
    assert count_gap_messages(snapshot, "target", now) == 2
    snapshot[0]["source_message_id"] = "changed"
    assert quote_ledger.get_quote_messages(GROUP_CHAT_ID)[0]["source_message_id"] == "target"
    assert quote_ledger.get_quote_messages("aiocqhttp:FriendMessage:1001") == []


def test_quote_index_imports_seed_history_without_recounting_current_messages(quote_ledger):
    now = time.time()
    current = _user("current", "当前消息", now - 1)
    no_id = _user("", "无 ID 消息", now)
    quote_ledger.add_messages(GROUP_CHAT_ID, [current, no_id])
    seeded = [_user("history", "历史", now - 2), dict(current), no_id]

    quote_ledger.set_messages(GROUP_CHAT_ID, seeded)
    quote_ledger.set_messages(GROUP_CHAT_ID, seeded)

    snapshot = quote_ledger.get_quote_messages(GROUP_CHAT_ID)
    assert [m["source_message_id"] for m in snapshot] == ["history", "current", ""]
    assert count_gap_messages(snapshot, "history", now) == 2


@pytest.mark.parametrize("synthesized_timestamps", [False, True])
def test_quote_index_deduplicates_rebuilt_fallback_history(quote_ledger, synthesized_timestamps):
    fd, _ = _front_desk()
    history = [
        {"role": "user", "content": "重复发言"},
        {"role": "user", "content": "重复发言"},
        {"role": "user", "content": "另一条发言"},
    ]
    target = _user("target", "可引用的原始消息", 0)
    quote_ledger.add_message(GROUP_CHAT_ID, target)

    for now in (100.0, 200.0):
        with patch("astrbot_plugin_angel_heart.roles.front_desk.time.time", return_value=now):
            if synthesized_timestamps:
                rebuilt = fd._convert_astrbot_history_to_angelheart_format(history, 19)
            else:
                rebuilt = [fd._convert_astrbot_history_record(record) for record in history]
        quote_ledger.set_messages(GROUP_CHAT_ID, [target, *rebuilt])
        snapshot = quote_ledger.get_quote_messages(GROUP_CHAT_ID)
        assert len(snapshot) == 4
        assert count_gap_messages(snapshot, "target", 300.0) == 3
        assert all(not is_quotable_message(message) for message in rebuilt)
        # 索引去重不能只依赖当前正文对象仍在内存中。
        quote_ledger.set_messages(GROUP_CHAT_ID, [])


@pytest.mark.parametrize("synthesized_timestamps", [False, True])
def test_repeated_seed_preserves_fallback_history(quote_ledger, synthesized_timestamps):
    fd, angel = _front_desk()
    angel.conversation_ledger = quote_ledger
    current = _user("current", "当前消息", time.time())
    quote_ledger.add_message(GROUP_CHAT_ID, current)
    history = [
        {"role": "user", "content": "历史甲"},
        {"role": "user", "content": "重复历史"},
        {"role": "user", "content": "重复历史"},
    ]
    fd._fetch_database_history = AsyncMock()
    event = _RewriteEvent("current")

    for attempt in range(2):
        with patch(
            "astrbot_plugin_angel_heart.roles.front_desk.time.time",
            return_value=current["timestamp"] - 10 + attempt,
        ):
            if synthesized_timestamps:
                rebuilt = fd._convert_astrbot_history_to_angelheart_format(history, 19)
            else:
                rebuilt = [fd._convert_astrbot_history_record(record) for record in history]
        fd._fetch_database_history.return_value = rebuilt
        asyncio.run(fd._ensure_minimum_context(GROUP_CHAT_ID, event))
        messages = quote_ledger.get_all_messages(GROUP_CHAT_ID)
        assert [message["content"] for message in messages] == [
            "历史甲", "重复历史", "重复历史", "当前消息"
        ]
        assert len(quote_ledger.get_quote_messages(GROUP_CHAT_ID)) == 4
    assert fd._fetch_database_history.await_count == 2


def test_seed_preserves_same_timestamp_messages_and_prefers_current_copy(quote_ledger):
    fd, angel = _front_desk()
    angel.conversation_ledger = quote_ledger
    now = time.time()
    current = _user("current", "当前正文", now)
    quote_ledger.add_message(GROUP_CHAT_ID, current)
    fd._fetch_database_history = AsyncMock(return_value=[
        _user("history-1", "同秒历史甲", now),
        _user("history-2", "同秒历史乙", now),
        _user("current", "历史里的旧正文", now - 1),
    ])

    asyncio.run(fd._ensure_minimum_context(GROUP_CHAT_ID, _RewriteEvent("current")))

    messages = quote_ledger.get_all_messages(GROUP_CHAT_ID)
    assert [message["source_message_id"] for message in messages] == [
        "history-1", "history-2", "current"
    ]
    assert messages[-1] is current
    assert len(quote_ledger.get_quote_messages(GROUP_CHAT_ID)) == 3


def test_quote_index_survives_total_message_pruning(quote_ledger):
    quote_ledger.TOTAL_MESSAGE_LIMIT = 2
    quote_ledger.add_messages(GROUP_CHAT_ID, _ledger_with_gap(4))

    assert "m-target" not in collect_known_message_ids(quote_ledger.get_all_messages(GROUP_CHAT_ID))
    assert count_gap_messages(quote_ledger.get_quote_messages(GROUP_CHAT_ID), "m-target", time.time()) == 4


def test_quote_index_capacity_is_shared_across_chats_and_missing_target_stays_unquoted(quote_ledger):
    quote_ledger._quote_messages = deque(maxlen=3)
    quote_ledger.add_messages(GROUP_CHAT_ID, _ledger_with_gap(2))
    quote_ledger.add_message("aiocqhttp:GroupMessage:other", _user("other", "其他群", time.time()))
    assert len(quote_ledger._quote_messages) == 3

    plugin = _plugin(threshold=0)
    plugin.angel_context.conversation_ledger = quote_ledger
    chain = [Plain(text="[引用:1]正文照常发送")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}, known_ids=["m-target"]))
    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert len(chain) == 1 and chain[0].text == "正文照常发送"
    assert event.get_extra("angelheart_quote_state")["gap"] is None
    assert event.get_extra("angelheart_quote_state")["reason"] == "quote_index_evicted"


def test_quote_index_is_cleared_on_close(quote_ledger):
    quote_ledger.add_message(GROUP_CHAT_ID, _user("target", "消息", time.time()))
    quote_ledger.close()
    assert quote_ledger.get_quote_messages(GROUP_CHAT_ID) == []


# ---------------------------------------------------------------------------
# 发送装饰
# ---------------------------------------------------------------------------


def test_decorate_inserts_reply_when_gap_exceeds_threshold():
    plugin = _plugin(threshold=3, ledger=_ledger_with_gap(4))
    chain = [Plain(text="[引用:1]我同意")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert isinstance(chain[0], Reply) and chain[0].id == "m-target"
    assert chain[1].text == "我同意"
    state = event.get_extra("angelheart_quote_state")
    assert state["reason"] == "quoted" and state["gap"] == 4 and state["injected"] is True


def test_decorate_does_not_quote_when_gap_equals_threshold():
    plugin = _plugin(threshold=3, ledger=_ledger_with_gap(3))
    chain = [Plain(text="[引用:1]我同意")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert chain[0].text == "我同意"
    assert event.get_extra("angelheart_quote_state")["reason"] == "gap_below_threshold"


def test_decorate_without_marker_never_quotes_even_with_large_gap():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    chain = [Plain(text="顺着话题说")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert event.get_extra("angelheart_quote_state")["reason"] == "no_marker"


def test_decorate_cleans_invalid_number_without_fallback():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    chain = [Plain(text="[引用:9]嗯")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert chain[0].text == "嗯"
    assert event.get_extra("angelheart_quote_state")["reason"] == "unknown_number"


def test_decorate_skips_when_target_missing_from_index():
    plugin = _plugin(threshold=0, ledger=[_user("m-other", "别的", time.time() - 5)])
    chain = [Plain(text="[引用:1]嗯")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-missing"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert chain[0].text == "嗯"
    assert event.get_extra("angelheart_quote_state")["reason"] == "quote_index_missing"


def test_decorate_reports_eviction_when_target_left_index():
    plugin = _plugin(threshold=0, ledger=[_user("m-other", "别的", time.time() - 5)])
    chain = [Plain(text="[引用:1]嗯")]
    event = _DecorateEvent(
        chain, _quote_extras({1: "m-missing"}, known_ids=["m-missing"])
    )

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert event.get_extra("angelheart_quote_state")["reason"] == "quote_index_evicted"


def test_decorate_keeps_existing_reply():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    chain = [Reply(id="m-existing"), Plain(text="[引用:1]嗯")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    replies = [component for component in chain if isinstance(component, Reply)]
    assert len(replies) == 1 and replies[0].id == "m-existing"
    assert chain[1].text == "嗯"
    assert event.get_extra("angelheart_quote_state")["reason"] == "reply_present"


def test_decorate_skips_non_plain_image_components():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    at = At(qq="2002", name="乙")
    chain = [Plain(text="[引用:1]听我说"), at]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert len(chain) == 2
    assert isinstance(chain[0], Plain) and chain[0].text == "听我说"
    assert chain[1] is at
    assert event.get_extra("angelheart_quote_state")["reason"] == "unsupported_components"


def test_decorate_clears_chain_when_only_marker_remains():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    chain = [Plain(text="[引用:1]")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert chain == []


def test_decorate_keeps_image_alongside_reply():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    image = Image()
    chain = [Plain(text="[引用:1]看图"), image]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert isinstance(chain[0], Reply)
    assert chain[1].text == "看图"
    assert chain[2] is image


def test_decorate_adds_mention_after_reply_when_host_configured():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9), reply_with_mention=True)
    chain = [Plain(text="[引用:1]好")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert isinstance(chain[0], Reply)
    assert isinstance(chain[1], At) and chain[1].qq == "1001" and chain[1].name == "甲"
    assert chain[2].text == "\n好"


def test_decorate_is_idempotent_and_does_not_recount_gap():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    chain = [Plain(text="[引用:1]好")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)
    # 首次装饰后账本再变化也不重算
    plugin.angel_context.conversation_ledger.get_quote_messages.return_value = []
    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    replies = [component for component in chain if isinstance(component, Reply)]
    assert len(replies) == 1
    assert chain[-1].text == "好"


def test_decorate_handles_second_result_on_same_event():
    """工具链同一事件会有多条结果：前一段不能挡掉真正带标记的回答。"""
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    preamble = [Plain(text="正在调用工具…")]
    event = _DecorateEvent(preamble, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, preamble)
    assert not any(isinstance(component, Reply) for component in preamble)

    answer = [Plain(text="[引用:1]答案")]
    event.new_result(answer)
    plugin._apply_quote_target(event, GROUP_CHAT_ID, answer)

    assert isinstance(answer[0], Reply) and answer[0].id == "m-target"
    assert answer[1].text == "答案"


def test_decorate_keeps_file_only_result():
    from astrbot.core.message.components import File

    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    attachment = File(name="a.txt", file="a.txt", url="")
    chain = [attachment]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert chain == [attachment]
    # 没有文本标记时本就不该引用；关键是文件组件不能被当作「无内容」清空
    assert event.get_extra("angelheart_quote_state")["reason"] == "no_marker"


def test_decorate_skips_injection_when_host_t2i_would_trigger():
    plugin = _plugin(
        threshold=0,
        ledger=_ledger_with_gap(9),
        host_config_extra={"t2i": True, "t2i_word_threshold": 50},
    )
    long_text = "长" * 80
    chain = [Plain(text=f"[引用:1]{long_text}")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert chain[0].text == long_text
    assert event.get_extra("angelheart_quote_state")["reason"] == "t2i_pending"


def test_decorate_still_quotes_short_reply_when_t2i_enabled():
    plugin = _plugin(
        threshold=0,
        ledger=_ledger_with_gap(9),
        host_config_extra={"t2i": True, "t2i_word_threshold": 50},
    )
    chain = [Plain(text="[引用:1]好")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert isinstance(chain[0], Reply)


def test_t2i_prediction_uses_session_config_profile():
    """默认配置关闭转图、会话档案开启转图时，必须按会话档案判断。"""
    session_config = {
        "t2i": True,
        "t2i_word_threshold": 50,
        "platform_settings": {"reply_with_mention": False, "reply_with_quote": False},
        "provider_settings": {"streaming_response": False},
    }
    plugin = _plugin(
        threshold=0, ledger=_ledger_with_gap(9), session_config=session_config
    )
    long_text = "长" * 80
    chain = [Plain(text=f"[引用:1]{long_text}")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert chain[0].text == long_text
    assert event.get_extra("angelheart_quote_state")["reason"] == "t2i_pending"


def test_t2i_prediction_counts_host_reply_prefix():
    """正文 45 字 + 前缀 5 字 + 2 个分隔符 = 52 > 50，宿主会转图。"""
    session_config = {
        "t2i": True,
        "t2i_word_threshold": 50,
        "platform_settings": {"reply_prefix": "BOT: "},
        "provider_settings": {},
    }
    plugin = _plugin(
        threshold=0, ledger=_ledger_with_gap(9), session_config=session_config
    )
    body = "字" * 45
    chain = [Plain(text=f"[引用:1]{body}")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert chain[0].text == body
    assert event.get_extra("angelheart_quote_state")["reason"] == "t2i_pending"


def test_t2i_prediction_without_prefix_still_quotes():
    """同样的 45 字正文，没有回复前缀时 47 <= 50，仍应插入引用。"""
    session_config = {
        "t2i": True,
        "t2i_word_threshold": 50,
        "platform_settings": {"reply_prefix": ""},
        "provider_settings": {},
    }
    plugin = _plugin(
        threshold=0, ledger=_ledger_with_gap(9), session_config=session_config
    )
    body = "字" * 45
    chain = [Plain(text=f"[引用:1]{body}")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert isinstance(chain[0], Reply)
    assert event.get_extra("angelheart_quote_state")["reason"] == "quoted"


def test_t2i_prediction_counts_reasoning_text():
    """宿主会在转图前把推理文本插到链首，长度口径要算进去。"""
    session_config = {
        "t2i": True,
        "t2i_word_threshold": 50,
        "platform_settings": {},
        "provider_settings": {"display_reasoning_text": True},
    }
    plugin = _plugin(
        threshold=0, ledger=_ledger_with_gap(9), session_config=session_config
    )
    extras = _quote_extras({1: "m-target"})
    extras["_llm_reasoning_content"] = "思" * 60
    chain = [Plain(text="[引用:1]好")]
    event = _DecorateEvent(chain, extras)

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert event.get_extra("angelheart_quote_state")["reason"] == "t2i_pending"


def test_decorate_skips_streaming_results():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    chain = [Plain(text="[引用:1]好")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}), streaming=True)

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert event.get_extra("angelheart_quote_state")["processed"] is False


def test_decorate_quotes_with_threshold_zero_after_one_message():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(1))
    chain = [Plain(text="[引用:1]好")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}, threshold=0))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert isinstance(chain[0], Reply)


def test_decorate_ignores_marker_inside_body_as_target():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    chain = [Plain(text="我说[引用:1]这个")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert not any(isinstance(component, Reply) for component in chain)
    assert chain[0].text == "我说这个"
    assert event.get_extra("angelheart_quote_state")["reason"] == "no_marker"


@pytest.mark.asyncio
async def test_decorating_hook_cleans_marker_before_energy_charge():
    plugin = _plugin(threshold=0, ledger=_ledger_with_gap(9))
    chain = [Plain(text="[引用:1]好")]
    event = _DecorateEvent(chain, _quote_extras({1: "m-target"}))

    await plugin.strip_markdown_on_decorating_result(event)

    charged_chain = plugin.angel_context.debounce_manager.charge_reply_energy.call_args[0][1]
    assert isinstance(charged_chain[0], Reply)
    assert charged_chain[1].text == "好"
    assert "[引用" not in charged_chain[1].text


# ---------------------------------------------------------------------------
# 账本入账清理
# ---------------------------------------------------------------------------


def test_completed_agent_messages_strip_marker_before_ledger():
    plugin = _plugin()
    event = _DecorateEvent([], {"angelheart_quote_state": {"processed": False}})
    run_context = SimpleNamespace(
        messages=[
            SimpleNamespace(
                role="assistant",
                content=[{"type": "text", "text": "[引用:2]好的"}],
                tool_calls=None,
            ),
            SimpleNamespace(
                role="tool",
                content=[{"type": "text", "text": "[引用:2]工具结果"}],
                tool_call_id="call-1",
            ),
        ]
    )

    asyncio.run(plugin.capture_completed_agent_messages(event, run_context, None))

    added = plugin.angel_context.conversation_ledger.add_messages.call_args[0][1]
    assert added[0]["content"][0]["text"] == "好的"
    assert added[1]["content"][0]["text"] == "[引用:2]工具结果"
    # 原始运行消息不被改写
    assert run_context.messages[0].content[0]["text"] == "[引用:2]好的"


def test_completed_agent_messages_keep_tool_calls_and_skip_when_disabled():
    plugin = _plugin()
    tool_calls = [{"function": {"name": "search", "arguments": '{"q":"[引用:1]"}'}}]
    message = SimpleNamespace(
        role="assistant",
        content=[{"type": "text", "text": "[引用:1]查一下"}],
        tool_calls=tool_calls,
    )

    event_disabled = _DecorateEvent([], {})
    asyncio.run(
        plugin.capture_completed_agent_messages(
            event_disabled, SimpleNamespace(messages=[message]), None
        )
    )
    kept = plugin.angel_context.conversation_ledger.add_messages.call_args[0][1]
    assert kept[0]["content"][0]["text"] == "[引用:1]查一下"

    plugin.angel_context.conversation_ledger.add_messages.reset_mock()
    event_enabled = _DecorateEvent([], {"angelheart_quote_state": {"processed": False}})
    asyncio.run(
        plugin.capture_completed_agent_messages(
            event_enabled, SimpleNamespace(messages=[message]), None
        )
    )
    cleaned = plugin.angel_context.conversation_ledger.add_messages.call_args[0][1]
    assert cleaned[0]["content"][0]["text"] == "查一下"
    assert cleaned[0]["tool_calls"] == tool_calls


# ---------------------------------------------------------------------------
# 状态清理与配置
# ---------------------------------------------------------------------------


def test_clear_quote_target_extras_resets_event_state():
    plugin = _plugin()
    event = _DecorateEvent(
        [],
        {
            "angelheart_quote_map": {1: "m1"},
            "angelheart_quote_state": {"processed": True},
        },
    )

    plugin._clear_quote_target_extras(event)

    assert event.get_extra("angelheart_quote_map") is None
    assert event.get_extra("angelheart_quote_state") is None


def test_quote_config_defaults_nested_and_flat():
    assert ConfigManager({}).quote_target_enabled is False
    assert ConfigManager({}).quote_min_gap_messages == 3

    nested = ConfigManager(
        {
            "output_rewrite": {
                "quote_target_enabled": True,
                "quote_min_gap_messages": 5,
            }
        }
    )
    assert nested.quote_target_enabled is True
    assert nested.quote_min_gap_messages == 5

    flat = ConfigManager({"quote_min_gap_messages": 7})
    assert flat.quote_min_gap_messages == 7


@pytest.mark.parametrize("bad", [-1, "abc", True, None, 2.5])
def test_quote_gap_invalid_values_fall_back_to_default(bad):
    manager = ConfigManager({"output_rewrite": {"quote_min_gap_messages": bad}})
    assert manager.quote_min_gap_messages == 3


def test_conflict_warning_is_deduped_and_reset():
    from astrbot.api import logger

    plugin = _plugin(reply_with_quote=True, quote_enabled=True)
    logger.warning.reset_mock()

    plugin._check_quote_target_conflict(GROUP_CHAT_ID)
    first_calls = logger.warning.call_count
    assert first_calls >= 1
    assert GROUP_CHAT_ID in plugin._quote_conflict_warned

    plugin._check_quote_target_conflict(GROUP_CHAT_ID)
    assert logger.warning.call_count == first_calls

    plugin.context.get_config.return_value = {
        "platform_settings": {"reply_with_quote": False},
        "provider_settings": {"streaming_response": False},
    }
    plugin._check_quote_target_conflict(GROUP_CHAT_ID)
    assert GROUP_CHAT_ID not in plugin._quote_conflict_warned


def test_conflict_warning_silent_when_plugin_switch_off():
    from astrbot.api import logger

    plugin = _plugin(reply_with_quote=True, quote_enabled=False)
    logger.warning.reset_mock()

    plugin._check_quote_target_conflict(GROUP_CHAT_ID)

    assert logger.warning.call_count == 0
    assert GROUP_CHAT_ID not in plugin._quote_conflict_warned
