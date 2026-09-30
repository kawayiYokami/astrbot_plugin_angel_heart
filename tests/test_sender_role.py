from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from astrbot_plugin_angel_heart.core.utils.xml_formatter import format_message_to_text
from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

GROUP_CHAT = "aiocqhttp:GroupMessage:10000"
PRIVATE_CHAT = "aiocqhttp:FriendMessage:10001"


def _front_desk():
    config = MagicMock()
    config.for_chat.return_value = config
    config.alias = "fairy"
    angel = MagicMock()
    angel.work_ledger = WorkLedger()
    angel.astr_context = MagicMock()
    return FrontDesk(config, angel)


def test_extract_sender_role_from_dict():
    fd = _front_desk()

    assert fd._extract_sender_role({"role": "owner"}) == "群主"
    assert fd._extract_sender_role({"role": "admin"}) == "管理"
    assert fd._extract_sender_role({"role": "member"}) == "群友"


def test_extract_sender_role_from_object_and_unknown_values():
    fd = _front_desk()

    assert fd._extract_sender_role(SimpleNamespace(role="admin")) == "管理"
    assert fd._extract_sender_role({"role": "guest"}) == ""
    assert fd._extract_sender_role({"role": None}) == ""
    assert fd._extract_sender_role({}) == ""
    assert fd._extract_sender_role(None) == ""


def test_extract_event_sender_role_reads_raw_message():
    fd = _front_desk()

    dict_raw = SimpleNamespace(
        message_obj=SimpleNamespace(raw_message={"sender": {"role": "owner"}})
    )
    assert fd._extract_event_sender_role(dict_raw) == "群主"

    obj_raw = SimpleNamespace(
        message_obj=SimpleNamespace(
            raw_message=SimpleNamespace(sender=SimpleNamespace(role="admin"))
        )
    )
    assert fd._extract_event_sender_role(obj_raw) == "管理"


def test_extract_event_sender_role_without_raw_message():
    fd = _front_desk()

    assert fd._extract_event_sender_role(SimpleNamespace(message_obj=None)) == ""
    assert fd._extract_event_sender_role(SimpleNamespace()) == ""


def test_group_header_contains_role_label():
    msg = {
        "role": "user",
        "content": "早上好",
        "sender_id": "10001",
        "sender_name": "红豆",
        "sender_role": "管理",
        "chat_id": GROUP_CHAT,
        "timestamp": 1758440000,
    }

    text = format_message_to_text(msg, "fairy")

    assert "[群友: 红豆 (ID: 10001, 管理)]" in text


def test_group_header_defaults_to_member_when_role_missing():
    msg = {
        "role": "user",
        "content": "早上好",
        "sender_id": "10001",
        "sender_name": "红豆",
        "chat_id": GROUP_CHAT,
        "timestamp": 1758440000,
    }

    text = format_message_to_text(msg, "fairy")

    assert "[群友: 红豆 (ID: 10001, 群友)]" in text


def test_private_header_has_no_role_label():
    msg = {
        "role": "user",
        "content": "在吗",
        "sender_id": "10001",
        "sender_name": "红豆",
        "sender_role": "管理",
        "chat_id": PRIVATE_CHAT,
        "timestamp": 1758440000,
    }

    text = format_message_to_text(msg, "fairy")

    assert "[红豆 (ID: 10001)]" in text
    assert "管理" not in text