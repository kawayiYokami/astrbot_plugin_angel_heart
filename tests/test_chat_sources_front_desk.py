"""FrontDesk 来源登记路径测试：cache_message 时用上游同步字段登记。"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HERE = Path(__file__).resolve().parent
_PARENT = str(HERE.parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from core.chat_sources import ChatSourcesStore
from astrbot.core.message.components import File, Image, Reply
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


class SourceEvent:
    """模拟携带上游同步字段的群聊事件。"""

    def __init__(self, message_str: str = "hello", chat_id: str = "g1"):
        self.message_str = message_str
        self.unified_msg_origin = chat_id
        self.extras = {}
        self._result = MagicMock()
        self._result.chain = ["x"]
        self._messages = None

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def get_sender_id(self):
        return "u1"

    def get_sender_name(self):
        return "user1"

    def get_self_id(self):
        return "bot1"

    def get_message_outline(self):
        return self.message_str

    def get_messages(self):
        if self._messages is not None:
            return self._messages
        if not self.message_str:
            return []

        class Plain:
            def __init__(self, text):
                self.text = text

        return [Plain(self.message_str)]

    def get_result(self):
        return self._result

    def get_timestamp(self):
        return 1785991141.0


def make_front_desk(store, config_manager=None):
    if config_manager is None:
        config_manager = MagicMock()
    angel = MagicMock()
    angel.conversation_ledger = MagicMock()
    angel.conversation_ledger.add_message = MagicMock()
    angel.astr_context = MagicMock()
    fd = FrontDesk(config_manager, angel)
    fd.chat_sources = store
    fd._normalize_sender_name = lambda *a: "user1"
    fd._ensure_message_id = lambda event: "mid-1"
    fd._build_cached_image_item = MagicMock(return_value=None)
    fd._build_cached_file_text_item = MagicMock(return_value=None)
    fd._get_event_message_id = MagicMock(return_value="mid-1")
    return fd


def test_media_iterator_includes_quoted_media_without_quote_text():
    fd = make_front_desk(None)
    direct_image = Image()
    quoted_image = Image()
    quoted_file = File()
    quoted_file.name = "note.txt"
    reply = Reply(chain=[object(), quoted_image, quoted_file])

    media = list(fd._iter_media_components([direct_image, reply]))

    assert media == [direct_image, quoted_image, quoted_file]


def test_media_iterator_handles_nested_or_cyclic_replies():
    fd = make_front_desk(None)
    quoted_image = Image()
    outer = Reply()
    inner = Reply(chain=[quoted_image, outer])
    outer.chain = [inner]

    assert list(fd._iter_media_components([outer])) == [quoted_image]


@pytest.mark.asyncio
async def test_group_source_recorded_with_group_name(tmp_path):
    store = ChatSourcesStore(str(tmp_path))
    fd = make_front_desk(store)

    event = SourceEvent(chat_id="aiocqhttp:GroupMessage:830624502")
    # 模拟 aiocqhttp 上游：group.group_name 已同步填入
    event.message_obj = types.SimpleNamespace(
        group=types.SimpleNamespace(
            group_id="830624502",
            group_name="绝区零&一条龙开发社群",
        ),
        sender=types.SimpleNamespace(user_id="289104862", nickname="红豆泥"),
    )

    await fd.cache_message(event.unified_msg_origin, event)

    entry = store.get_source("aiocqhttp:GroupMessage:830624502")
    assert entry is not None
    assert entry["kind"] == "group"
    assert entry["display_name"] == "绝区零&一条龙开发社群"


@pytest.mark.asyncio
async def test_private_source_recorded_with_sender_nickname(tmp_path):
    store = ChatSourcesStore(str(tmp_path))
    fd = make_front_desk(store)

    event = SourceEvent(chat_id="aiocqhttp:FriendMessage:289104862")
    # 私聊：group 为 None，取 sender.nickname
    event.message_obj = types.SimpleNamespace(
        group=None,
        sender=types.SimpleNamespace(user_id="289104862", nickname="红豆泥"),
    )

    await fd.cache_message(event.unified_msg_origin, event)

    entry = store.get_source("aiocqhttp:FriendMessage:289104862")
    assert entry is not None
    assert entry["kind"] == "private"
    assert entry["display_name"] == "红豆泥"


@pytest.mark.asyncio
async def test_no_store_no_crash(tmp_path):
    fd = make_front_desk(None)
    event = SourceEvent(chat_id="g1")
    event.message_obj = types.SimpleNamespace(
        group=types.SimpleNamespace(group_id="1", group_name="某群"),
        sender=types.SimpleNamespace(user_id="u1", nickname="某人"),
    )
    # 不注入 store，登记应静默跳过
    await fd.cache_message(event.unified_msg_origin, event)


@pytest.mark.asyncio
async def test_source_update_keeps_kind(tmp_path):
    store = ChatSourcesStore(str(tmp_path))
    fd = make_front_desk(store)

    event = SourceEvent(chat_id="g1")
    event.message_obj = types.SimpleNamespace(
        group=types.SimpleNamespace(group_id="1", group_name="旧群名"),
        sender=types.SimpleNamespace(user_id="u1", nickname="某人"),
    )
    await fd.cache_message("g1", event)

    event2 = SourceEvent(chat_id="g1")
    event2.message_obj = types.SimpleNamespace(
        group=types.SimpleNamespace(group_id="1", group_name="新群名"),
        sender=types.SimpleNamespace(user_id="u1", nickname="某人"),
    )
    await fd.cache_message("g1", event2)

    entry = store.get_source("g1")
    assert entry["display_name"] == "新群名"
    assert entry["kind"] == "group"
