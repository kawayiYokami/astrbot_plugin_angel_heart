"""引用自己消息视为点名：front_desk 入库命中与 is_event_wake 唤醒判定。

回归场景：用户在群里「回复引用」机器人自己发的消息提问（QQ 上比 @ 更常用的
召唤方式），main._should_process 已把「Reply 且 sender_id == self_id」视为点名，
但 front_desk.cache_message 写入 metadata 与 StatusChecker.is_event_wake 判定
此前只认 At 组件，导致引用召唤被当成未唤醒消息只入库不进场。
本测试锁定三处口径一致：入库 is_at_self=True、metadata 含 at_self、wake=True。
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HERE = Path(__file__).resolve().parent
_PARENT = str(HERE.parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from astrbot.core.message.components import Reply

from astrbot_plugin_angel_heart.core.angel_heart_status import StatusChecker
from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


def make_config(**wake_overrides):
    wake_interaction = {
        "alias": "草王|纳西妲",
        "force_reply_when_summoned": True,
        "reply_even_not_questioned": False,
        "enter_on_mention_only": True,
    }
    wake_interaction.update(wake_overrides)
    return ConfigManager(
        {
            "analyzer_model": "mock-model",
            "timing": {},
            "energy": {},
            "wake_interaction": wake_interaction,
            "leave_reply": {},
            "access_control": {},
            "context_compression": {},
            "output_rewrite": {},
            "personality": {"ai_self_identity": "test", "reply_strategy_guide": ""},
        }
    )


def make_reply(sender_id):
    reply = Reply()
    reply.id = "568345449"
    reply.sender_id = sender_id
    reply.sender_nickname = "bot" if sender_id == "bot1" else "someone"
    reply.message_str = "老师，当我的fairy，好处没有，坑我先替你踩"
    return reply


class ReplyEvent:
    """模拟「Reply 引用 + 纯文本」的群聊事件。"""

    def __init__(self, reply_sender_id, text="你怎么神出鬼没？", chat_id="g1"):
        self.unified_msg_origin = chat_id
        self.extras = {}
        self._result = MagicMock()
        self._result.chain = ["x"]
        self.reply = make_reply(reply_sender_id)
        self.text = text
        self.message_obj = types.SimpleNamespace(
            group=types.SimpleNamespace(group_id="1", group_name="测试群"),
            sender=types.SimpleNamespace(user_id="u1", nickname="用户"),
        )
        self._get_messages_used = False
        self.angelheart_context = None

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def get_sender_id(self):
        return "u1"

    def get_sender_name(self):
        return "用户"

    def get_self_id(self):
        return "bot1"

    def get_message_outline(self):
        return self.text

    def get_messages(self):
        self._get_messages_used = True
        return [self.reply]

    def get_result(self):
        return self._result

    def get_timestamp(self):
        return 1786978435.0


def make_front_desk(config_manager):
    angel = MagicMock()
    angel.conversation_ledger = MagicMock()
    angel.conversation_ledger.add_message = MagicMock()
    angel.astr_context = MagicMock()
    fd = FrontDesk(config_manager, angel)
    fd.chat_sources = None
    fd._normalize_sender_name = lambda *a: "用户"
    fd._ensure_message_id = lambda event: "mid-1"
    fd._build_cached_image_item = MagicMock(return_value=None)
    fd._build_cached_file_text_item = MagicMock(return_value=None)
    fd._get_event_message_id = MagicMock(return_value="mid-1")
    return fd


def make_status_checker(config_manager):
    angel = MagicMock()
    angel.silenced_until = {}
    return StatusChecker(config_manager, angel)


@pytest.mark.asyncio
async def test_cache_message_reply_to_self_marks_at_self():
    """引用自己消息入库：is_at_self=True，metadata 含 at_self 命中。"""
    config = make_config()
    fd = make_front_desk(config)
    event = ReplyEvent(reply_sender_id="bot1")

    await fd.cache_message(event.unified_msg_origin, event)

    args, _ = fd.context.conversation_ledger.add_message.call_args
    new_message = args[1]
    assert new_message["is_at_self"] is True
    hits = new_message["metadata"]["hits"]
    assert {"type": "at_self"} in hits
    # 调度侧立刻可读的 extra 与入库 metadata 同源
    extra_metadata = event.extras["angelheart_message_metadata"]
    assert {"type": "at_self"} in extra_metadata["hits"]


@pytest.mark.asyncio
async def test_cache_message_reply_to_other_not_at_self():
    """引用他人消息不算点名。"""
    config = make_config()
    fd = make_front_desk(config)
    event = ReplyEvent(reply_sender_id="someone_else")

    await fd.cache_message(event.unified_msg_origin, event)

    args, _ = fd.context.conversation_ledger.add_message.call_args
    new_message = args[1]
    assert new_message["is_at_self"] is False
    hits = new_message["metadata"]["hits"]
    assert all(hit["type"] != "at_self" for hit in hits)


@pytest.mark.asyncio
async def test_is_event_wake_reply_to_self_after_cache():
    """真实链路：cache_message 后 is_event_wake 必须返回 True。"""
    config = make_config()
    fd = make_front_desk(config)
    checker = make_status_checker(config)
    event = ReplyEvent(reply_sender_id="bot1")

    await fd.cache_message(event.unified_msg_origin, event)
    assert checker.is_event_wake(event) is True


@pytest.mark.asyncio
async def test_is_event_wake_reply_to_self_metadata_missing():
    """兜底路径：即使 metadata 缺失，事件组件含 Reply 引用自己也应判唤醒。"""
    config = make_config()
    checker = make_status_checker(config)
    event = ReplyEvent(reply_sender_id="bot1")

    assert checker.is_event_wake(event) is True


@pytest.mark.asyncio
async def test_is_event_wake_reply_to_other_metadata_missing():
    """兜底路径：引用他人消息不判唤醒。"""
    config = make_config()
    checker = make_status_checker(config)
    event = ReplyEvent(reply_sender_id="someone_else")

    assert checker.is_event_wake(event) is False