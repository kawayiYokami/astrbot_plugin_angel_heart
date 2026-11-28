"""同一人自说自话没人接：第三种离场应答的判定。

只认字面意义上的纯文字：带引用、带 @、带图片表情的消息一律不计入。
同一个人当天已经被接过一次就不再接。
"""

from types import SimpleNamespace

import pytest
from astrbot_plugin_angel_heart.core import angel_heart_status as status_module
from astrbot_plugin_angel_heart.core.angel_heart_status import StatusChecker

NOW = 1000.0
TODAY = "2026-11-28"


def message(
    identity,
    content=None,
    *,
    sender="u1",
    role="user",
    timestamp=NOW,
    lone_eligible=True,
    **extra,
):
    item = {
        "role": role,
        "source_message_id": identity,
        "sender_id": sender,
        "content": content if content is not None else f"第{identity}句",
        "timestamp": timestamp,
        **extra,
    }
    if lone_eligible is not None:
        item["lone_eligible"] = lone_eligible
    return item


def checker(
    monkeypatch,
    messages,
    *,
    enabled=True,
    given=None,
    cooldown=False,
    echo=False,
    dense=False,
):
    monkeypatch.setattr(
        status_module,
        "time",
        SimpleNamespace(
            time=lambda: NOW,
            strftime=lambda _fmt: TODAY,
        ),
    )
    config = SimpleNamespace(
        leave_echo_reply=echo,
        leave_dense_reply=dense,
        leave_lone_reply=enabled,
        echo_detection_threshold=3,
        echo_detection_window=90,
        dense_conversation_window=90,
        dense_conversation_threshold=1,
        min_participant_count=1,
    )
    context = SimpleNamespace(
        conversation_ledger=SimpleNamespace(get_all_messages=lambda _chat: messages),
        is_leave_reply_in_cooldown=lambda _chat: cooldown,
        lone_reply_given=given if given is not None else {},
    )
    return StatusChecker(SimpleNamespace(for_chat=lambda _chat: config), context)


def test_three_plain_messages_from_one_person_triggers(monkeypatch):
    messages = [message("1"), message("2"), message("3")]
    value = checker(monkeypatch, messages)
    assert value.get_leave_reply_trigger("g") == "lone_speaker"


def test_two_messages_are_not_enough(monkeypatch):
    messages = [message("1"), message("2")]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_switch_off_disables_detection(monkeypatch):
    messages = [message("1"), message("2"), message("3")]
    assert (
        checker(monkeypatch, messages, enabled=False).get_leave_reply_trigger("g") == ""
    )


def test_cooldown_blocks_before_detection(monkeypatch):
    messages = [message("1"), message("2"), message("3")]
    assert (
        checker(monkeypatch, messages, cooldown=True).get_leave_reply_trigger("g") == ""
    )


def test_another_person_in_the_window_cancels(monkeypatch):
    messages = [
        message("1"),
        message("2"),
        message("other", sender="u2"),
        message("3"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_other_person_speaking_before_the_run_does_not_cancel(monkeypatch):
    """别人在这段连发之前说过的话，不算「期间被打断」。"""
    messages = [
        message("old", sender="u2"),
        message("1"),
        message("2"),
        message("3"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == "lone_speaker"


@pytest.mark.parametrize(
    "extra",
    [
        {"lone_eligible": False},  # 带图 / 带表情 / 带 @ / 带引用的消息都是 False
    ],
)
def test_non_plain_message_does_not_count(monkeypatch, extra):
    messages = [
        message("1"),
        message("2"),
        message("3", **extra),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_non_plain_message_in_the_middle_breaks_the_run(monkeypatch):
    messages = [
        message("1"),
        message("2"),
        message("image", lone_eligible=False),
        message("3"),
        message("4"),
    ]
    # 带图那条把连续纯文字打断了，前面两条不算
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_message_longer_than_limit_does_not_count(monkeypatch):
    messages = [
        message("1"),
        message("2"),
        message("3", content="字" * 51),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_long_plain_message_is_skipped_but_does_not_break_the_run(monkeypatch):
    messages = [
        message("1"),
        message("2", content="字" * 80),
        message("3"),
    ]
    # 中间那条太长被跳过，前后两条仍是纯文字，一共两条，不够三条
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""

    messages.append(message("4"))
    # 四条里跳过长的一条，剩三条纯文字 → 触发
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == "lone_speaker"


def test_repeated_content_is_treated_as_spam(monkeypatch):
    messages = [
        message("1", content="加我微信"),
        message("2", content="加我微信"),
        message("3", content="加我微信"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_distinct_short_messages_are_not_spam(monkeypatch):
    messages = [
        message("1", content="在吗"),
        message("2", content="有人吗"),
        message("3", content="算了"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == "lone_speaker"


def test_partly_repeated_messages_are_not_spam(monkeypatch):
    """自说自话里重复一个应答词很常见，只有全都一样才算刷屏。"""
    messages = [
        message("1", content="好"),
        message("2", content="好"),
        message("3", content="那我去看了"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == "lone_speaker"


def test_messages_outside_the_window_do_not_count(monkeypatch):
    messages = [
        message("1", timestamp=NOW - 5000),
        message("2", timestamp=NOW - 4000),
        message("3", timestamp=NOW),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_same_person_is_helped_only_once_per_day(monkeypatch):
    messages = [message("1"), message("2"), message("3")]
    given = {"g": {"u1": TODAY}}
    assert (
        checker(monkeypatch, messages, given=given).get_leave_reply_trigger("g") == ""
    )


def test_helped_once_marks_the_sender_for_the_day(monkeypatch):
    messages = [message("1"), message("2"), message("3")]
    given = {}
    value = checker(monkeypatch, messages, given=given)
    assert value.get_leave_reply_trigger("g") == "lone_speaker"
    assert given["g"]["u1"] == TODAY
    # 同一批消息再问一次，已经不再触发
    assert value.get_leave_reply_trigger("g") == ""


def test_another_person_can_still_be_helped_same_day(monkeypatch):
    messages = [
        message("1", sender="u2"),
        message("2", sender="u2"),
        message("3", sender="u2"),
    ]
    given = {"g": {"u1": TODAY}}
    assert (
        checker(monkeypatch, messages, given=given).get_leave_reply_trigger("g")
        == "lone_speaker"
    )
    assert given["g"]["u2"] == TODAY


def test_assistant_and_summary_rows_are_ignored(monkeypatch):
    messages = [
        message("1"),
        message("bot", sender="bot", role="assistant"),
        message("2"),
        message("summary", kind="context_summary", sender=""),
        message("3"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == "lone_speaker"


def test_current_message_must_itself_be_plain(monkeypatch):
    messages = [
        message("1"),
        message("2"),
        message("3", lone_eligible=False),
    ]
    # 触发消息本身带图/引用时，不当场接
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_echo_takes_precedence_over_lone_speaker(monkeypatch):
    messages = [message(str(i), content="复读") for i in range(3)]
    value = checker(monkeypatch, messages, echo=True)
    assert value.get_leave_reply_trigger("g") == "echo_chamber"


def test_unknown_message_id_fails_closed(monkeypatch):
    messages = [message("1"), message("2"), message("3")]
    value = checker(monkeypatch, messages)
    assert value.get_leave_reply_trigger("g", message_id="missing") == ""


def test_text_parts_are_joined_from_multimodal_list(monkeypatch):
    messages = [
        message(
            "1",
            content=[{"type": "text", "text": "在"}, {"type": "text", "text": "吗"}],
        ),
        message("2", content=[{"type": "text", "text": "有人"}]),
        message("3", content=[{"type": "text", "text": "算了"}]),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == "lone_speaker"


def test_config_defaults_and_template_inheritance():
    """开关要能被 ConfigManager 读到，也要能随全局配置复制进群模板。

    第二段是回归：新增的离场应答键漏进 TEMPLATE_GROUPS，新建群模板时会静默丢掉。
    """
    from astrbot_plugin_angel_heart.core.chat_profile import ChatProfileStore
    from astrbot_plugin_angel_heart.core.config_manager import ConfigManager

    config = ConfigManager({"leave_reply": {"leave_lone_reply": True}})
    assert config.leave_lone_reply is True

    empty = ConfigManager({})
    assert empty.leave_lone_reply is False

    group = ChatProfileStore.template_from_global(config).get("leave_reply") or {}
    assert "leave_lone_reply" in group, "新建群模板时会丢掉 leave_lone_reply"


def test_missing_lone_eligible_is_treated_as_non_plain(monkeypatch):
    """缺失 lone_eligible 的消息按非纯文字处理。

    冷启动补种的消息不带这个字段，且 content 是把原始消息压平后的字符串，
    图片与 @ 段在压平中就丢了。放行等于把「图 + 一句话」当成纯文字接住。
    """
    messages = [
        message("1"),
        message("2"),
        message("3", lone_eligible=None),  # 字段缺失，不是显式 False
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_missing_lone_eligible_in_the_middle_breaks_the_run(monkeypatch):
    """补种消息夹在中间时打断连发段，不让它被算作纯文字。"""
    messages = [
        message("1"),
        message("seed", lone_eligible=None),
        message("2"),
        message("3"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""


def test_message_id_locates_the_target_instead_of_the_last_message(monkeypatch):
    """给了 message_id 就只统计到那条为止，走按 id 精确匹配的分支。

    账本尾部是他后来单独说的两句，若按「取最后一条」只数得到两条；
    指定到第三句时，前面连续三条才是完整的一轮。
    """
    messages = [
        message("1"),
        message("2"),
        message("3"),
        message("other", sender="u2"),
        message("4"),
        message("5"),
    ]
    value = checker(monkeypatch, messages)
    assert value.get_leave_reply_trigger("g", message_id="3") == "lone_speaker"
    # 不指定 id 时取最后一条，往前被别人的消息切断，只剩两条
    assert value.get_leave_reply_trigger("g") == ""


def test_unknown_message_id_does_not_trigger(monkeypatch):
    messages = [message("1"), message("2"), message("3")]
    assert (
        checker(monkeypatch, messages).get_leave_reply_trigger(
            "g", message_id="not-there"
        )
        == ""
    )


def test_distinct_emoji_messages_are_not_spam(monkeypatch):
    """三条各不相同的表情不算刷屏。

    归一化只留字母数字，纯 emoji 压完都是空串，直接按集合去重会误判成刷屏。
    """
    messages = [
        message("1", content="😂"),
        message("2", content="😅"),
        message("3", content="😊"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == "lone_speaker"


def test_identical_emoji_messages_are_spam(monkeypatch):
    """三条一模一样的表情仍按刷屏挡掉。"""
    messages = [
        message("1", content="😂"),
        message("2", content="😂"),
        message("3", content="😂"),
    ]
    assert checker(monkeypatch, messages).get_leave_reply_trigger("g") == ""
