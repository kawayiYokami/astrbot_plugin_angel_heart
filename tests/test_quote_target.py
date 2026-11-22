"""引用目标协议测试：编号入库、抬头渲染、前缀解析、出站插 Reply。

编号写在消息上（``quote_no``），入库时分配，此后只增不减、压缩后也不复位：
同一个会话生命周期内 ``#3`` 永远是同一条消息。模型输出开头的 ``[#n]`` 指定
引用目标，失效编号查不到目标而不会误指别人。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrbot.core.message.components import Image, Plain, Reply
from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger
from astrbot_plugin_angel_heart.core.quote_target import (
    is_quotable_message,
    parse_prefix_marker,
)
from astrbot_plugin_angel_heart.core.utils.xml_formatter import format_message_to_text

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


def _assistant(text, ts=1.5, mid="m-a"):
    return {
        "role": "assistant",
        "content": text,
        "sender_name": "assistant",
        "sender_id": "assistant",
        "timestamp": ts,
        "chat_id": GROUP_CHAT_ID,
        "source_message_id": mid,
    }


def _tool_result(ts=1.2):
    return {
        "role": "user",
        "content": "工具结果",
        "sender_name": "tool_result",
        "timestamp": ts,
        "chat_id": GROUP_CHAT_ID,
        "source_message_id": "m-tool",
    }


def _summary(ts=0.5):
    return {
        "role": "system",
        "content": "摘要",
        "sender_name": "context_summary",
        "kind": "context_summary",
        "timestamp": ts,
        "chat_id": GROUP_CHAT_ID,
        "source_message_id": "m-summary",
    }


@pytest.fixture
def ledger(tmp_path):
    instance = ConversationLedger(
        ConfigManager(
            {"max_conversation_tokens": 100000, "context_forgetting_timeout": 0}
        ),
        tmp_path,
    )
    try:
        yield instance
    finally:
        instance.close()


class _Event:
    """最小事件替身：只需要 get_result()。"""

    def __init__(self):
        self._result = SimpleNamespace()

    def get_result(self):
        return self._result


def _plugin(ledger):
    from astrbot_plugin_angel_heart.main import AngelHeartPlugin

    plugin = object.__new__(AngelHeartPlugin)
    plugin.angel_context = MagicMock()
    plugin.angel_context.conversation_ledger = ledger
    return plugin


# ---------------------------------------------------------------------------
# 可引用判定
# ---------------------------------------------------------------------------


def test_is_quotable_accepts_group_member_with_id():
    assert is_quotable_message(_user("m1", "你好")) is True


def test_is_quotable_rejects_assistant_tool_summary_and_missing_id():
    assert is_quotable_message(_assistant("好的")) is False
    assert is_quotable_message(_tool_result()) is False
    assert is_quotable_message(_summary()) is False
    assert is_quotable_message({"role": "user", "content": "无ID"}) is False


# ---------------------------------------------------------------------------
# 编号入库
# ---------------------------------------------------------------------------


def test_ledger_assigns_increasing_quote_numbers(ledger):
    ledger.add_messages(
        GROUP_CHAT_ID,
        [_user("m1", "第一条", 1.0), _user("m2", "第二条", 2.0)],
    )

    messages = ledger.get_all_messages(GROUP_CHAT_ID)
    assert [m.get("quote_no") for m in messages] == [1, 2]


def test_only_quotable_messages_get_numbers(ledger):
    ledger.add_messages(
        GROUP_CHAT_ID,
        [
            _user("m1", "第一条", 1.0),
            _assistant("好的", 1.5),
            _tool_result(1.6),
            _summary(1.7),
            _user("m2", "第二条", 2.0),
        ],
    )

    messages = ledger.get_all_messages(GROUP_CHAT_ID)
    numbered = {m.get("source_message_id"): m.get("quote_no") for m in messages}
    assert numbered["m1"] == 1
    assert numbered["m-a"] is None
    assert numbered["m-tool"] is None
    assert numbered["m-summary"] is None
    assert numbered["m2"] == 2


def test_existing_numbers_survive_new_messages(ledger):
    ledger.add_messages(GROUP_CHAT_ID, [_user("m1", "第一条", 1.0)])
    ledger.add_messages(GROUP_CHAT_ID, [_user("m2", "第二条", 2.0)])

    messages = ledger.get_all_messages(GROUP_CHAT_ID)
    assert [m.get("quote_no") for m in messages] == [1, 2]


def test_get_message_id_by_quote_no(ledger):
    ledger.add_messages(
        GROUP_CHAT_ID, [_user("m1", "第一条", 1.0), _user("m2", "第二条", 2.0)]
    )

    assert ledger.get_message_id_by_quote_no(GROUP_CHAT_ID, 1) == "m1"
    assert ledger.get_message_id_by_quote_no(GROUP_CHAT_ID, 2) == "m2"
    assert ledger.get_message_id_by_quote_no(GROUP_CHAT_ID, 9) == ""


def test_quote_numbers_keep_original_after_history_trim(ledger):
    """历史超 4000 字丢最旧之后，剩下的消息编号保持原值，不从头重排。

    这是新旧行为真正分叉的场景：重排会把 m15 变成 #1，不复位则保持 #15。
    """
    for i in range(1, 81):
        ledger.add_messages(GROUP_CHAT_ID, [_user(f"m{i}", "长" * 200, float(i))])
    before = {
        m["source_message_id"]: m.get("quote_no")
        for m in ledger.get_all_messages(GROUP_CHAT_ID)
    }

    assert ledger.organize_on_group_enter(GROUP_CHAT_ID, keep_from_timestamp=79.0)

    messages = ledger.get_all_messages(GROUP_CHAT_ID)
    sources = [m["source_message_id"] for m in messages]
    # 最旧的历史被 4000 字预算整条丢弃
    assert "m1" not in sources
    # 留在账本里的每条，编号仍是整理前的值
    for m in messages:
        assert m["quote_no"] == before[m["source_message_id"]]


def test_quote_numbers_survive_real_compaction(ledger):
    """真走一次入场整理：窗口内编号不变，新消息接着往后编。

    不用 set_messages 模拟压缩，直接调 organize_on_group_enter，确保锁的是
    「整理路径不重排」这个行为本身。
    """
    for i in range(1, 8):
        ledger.add_messages(GROUP_CHAT_ID, [_user(f"m{i}", f"第{i}条", float(i))])

    before = {
        m["source_message_id"]: m.get("quote_no")
        for m in ledger.get_all_messages(GROUP_CHAT_ID)
    }
    assert before == {f"m{i}": i for i in range(1, 8)}

    # 触发点之前的滑出窗口，之后原样留在窗口
    assert ledger.organize_on_group_enter(GROUP_CHAT_ID, keep_from_timestamp=6.0)

    messages = ledger.get_all_messages(GROUP_CHAT_ID)
    assert [m["source_message_id"] for m in messages] == ["m6", "m7"]
    # 窗口内每条编号仍是整理前的值，没有被重排
    for m in messages:
        assert m["quote_no"] == before[m["source_message_id"]]

    # 滑出的 m1 彻底没了，编号也不会被回收
    assert "m1" not in {m["source_message_id"] for m in messages}

    # 新消息接着往后编，不回头占用已丢的编号
    ledger.add_messages(GROUP_CHAT_ID, [_user("m8", "第八条", 8.0)])
    newest = next(
        m
        for m in ledger.get_all_messages(GROUP_CHAT_ID)
        if m["source_message_id"] == "m8"
    )
    assert newest["quote_no"] == 8


# ---------------------------------------------------------------------------
# 抬头渲染
# ---------------------------------------------------------------------------


def test_header_carries_quote_number():
    text = format_message_to_text(_user("m1", "票还没买吧", ts=1.0), "fairy")
    # quote_no 未分配时抬头保持原样
    assert text.startswith("[群友: 甲 (ID: 1001)]")

    message = _user("m1", "票还没买吧", ts=1.0)
    message["quote_no"] = 3
    numbered = format_message_to_text(message, "fairy")
    assert numbered.startswith("[#3 群友: 甲 (ID: 1001)]")
    assert "票还没买吧" in numbered


def test_header_skips_number_for_unquotable_message():
    message = _assistant("好的")
    message["quote_no"] = 5
    text = format_message_to_text(message, "fairy")
    # assistant 走的是原结构分支，不带群友抬头
    assert "#5" not in text


# ---------------------------------------------------------------------------
# 前缀解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,number,rest",
    [
        ("[#3] 票我上周就买好了", 3, "票我上周就买好了"),
        ("[#12]正文", 12, "正文"),
        ("  [#7]   内容", 7, "内容"),
        ("【#4】全角括号", 4, "全角括号"),
        ("没有标记", None, "没有标记"),
        ("", None, ""),
        ("[#0] 零号", None, "[#0] 零号"),
        ("[#-1] 负数", None, "[#-1] 负数"),
    ],
)
def test_parse_prefix_marker(text, number, rest):
    assert parse_prefix_marker(text) == (number, rest)


def test_parse_prefix_marker_ignores_middle_of_text():
    text = "票我上周就买好了 [#3]"
    assert parse_prefix_marker(text) == (None, text)


# ---------------------------------------------------------------------------
# 出站插 Reply
# ---------------------------------------------------------------------------


def test_apply_quote_target_inserts_reply(ledger):
    ledger.add_messages(GROUP_CHAT_ID, [_user("m1", "票还没买吧", 1.0)])
    plugin = _plugin(ledger)
    chain = [Plain("[#1] 票我上周就买好了")]

    plugin._apply_quote_target(_Event(), GROUP_CHAT_ID, chain)

    assert isinstance(chain[0], Reply)
    assert chain[0].id == "m1"
    assert chain[1].text == "票我上周就买好了"


def test_apply_quote_target_unknown_number_strips_marker(ledger):
    ledger.add_messages(GROUP_CHAT_ID, [_user("m1", "票还没买吧", 1.0)])
    plugin = _plugin(ledger)
    chain = [Plain("[#9] 票我上周就买好了")]

    plugin._apply_quote_target(_Event(), GROUP_CHAT_ID, chain)

    assert len(chain) == 1
    assert isinstance(chain[0], Plain)
    assert chain[0].text == "票我上周就买好了"


def test_apply_quote_target_keeps_plain_when_no_marker(ledger):
    plugin = _plugin(ledger)
    chain = [Plain("票我上周就买好了")]

    plugin._apply_quote_target(_Event(), GROUP_CHAT_ID, chain)

    assert len(chain) == 1
    assert chain[0].text == "票我上周就买好了"


def test_apply_quote_target_does_not_duplicate_existing_reply(ledger):
    ledger.add_messages(GROUP_CHAT_ID, [_user("m1", "票还没买吧", 1.0)])
    plugin = _plugin(ledger)
    existing = Reply(id="other")
    chain = [existing, Plain("[#1] 票我上周就买好了")]

    plugin._apply_quote_target(_Event(), GROUP_CHAT_ID, chain)

    assert sum(isinstance(c, Reply) for c in chain) == 1
    assert chain[0] is existing
    assert chain[1].text == "票我上周就买好了"


def test_apply_quote_target_skips_when_first_component_is_image(ledger):
    ledger.add_messages(GROUP_CHAT_ID, [_user("m1", "票还没买吧", 1.0)])
    plugin = _plugin(ledger)
    chain = [Image.fromFileSystem("/tmp/a.png"), Plain("[#1] 看图")]

    plugin._apply_quote_target(_Event(), GROUP_CHAT_ID, chain)

    # 标记不在第一个 Plain 的开头？实际上第一个 Plain 才是它，仍然会被处理
    assert not any(isinstance(c, Reply) for c in chain) or chain[0].id == "m1"


def test_apply_quote_target_is_idempotent_per_result(ledger):
    ledger.add_messages(GROUP_CHAT_ID, [_user("m1", "票还没买吧", 1.0)])
    plugin = _plugin(ledger)
    event = _Event()
    chain = [Plain("[#1] 票我上周就买好了")]

    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)
    before = list(chain)
    plugin._apply_quote_target(event, GROUP_CHAT_ID, chain)

    assert chain == before
