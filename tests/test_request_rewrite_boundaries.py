from __future__ import annotations

import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
_PARENT = str(PLUGIN_ROOT.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

for _mod_path in (
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.core",
    "astrbot.core.agent",
    "astrbot.core.agent.message",
    "astrbot.core.message",
    "astrbot.core.message.components",
    "astrbot.core.star",
    "astrbot.core.star.context",
):
    sys.modules.setdefault(_mod_path, types.ModuleType(_mod_path))

sys.modules["astrbot.api"].logger = MagicMock()
sys.modules["astrbot.core.message.components"].Image = type("Image", (), {})
sys.modules["astrbot.core.message.components"].At = type("At", (), {})
sys.modules["astrbot.core.message.components"].File = type("File", (), {})
sys.modules["astrbot.core.star.context"].Context = type("Context", (), {})
sys.modules["astrbot.core.agent.message"].ImageURLPart = type(
    "ImageURLPart",
    (),
    {"__init__": lambda self, image_url: setattr(self, "image_url", SimpleNamespace(**image_url))},
)

from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk
from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger


def _front_desk():
    config = MagicMock()
    config.alias = "fairy"
    config.image_caption_provider_id = ""
    angel = MagicMock()
    angel.work_ledger = WorkLedger()
    angel.astr_context = MagicMock()
    fd = FrontDesk(config, angel)
    fd._ensure_image_captions_for_request = AsyncMock(return_value=0)
    fd._provider_supports_images = MagicMock(return_value=False)
    fd.filter_images_for_provider = MagicMock(side_effect=lambda _chat_id, contexts: contexts)
    return fd, angel


def _event(message_id: str):
    class E:
        unified_msg_origin = "aiocqhttp:GroupMessage:10000"
        message_str = ""
        message_obj = SimpleNamespace(message_id=message_id)

        def __init__(self):
            self._extras = {}

        def get_extra(self, key, default=None):
            return self._extras.get(key, default)

        def set_extra(self, key, value):
            self._extras[key] = value

    return E()


async def _run_group_rewrite(fd, event, req, recent_dialogue, historical_context):
    event.set_extra(
        "angelheart_decision_context",
        {
            "recent_dialogue": recent_dialogue,
            "historical_context": historical_context,
            "boundary_ts": 3.0,
        },
    )
    await fd.rewrite_prompt_for_llm("aiocqhttp:GroupMessage:10000", event, req)


def test_split_recent_dialogue_uses_message_id_boundary():
    fd, _ = _front_desk()
    before, current = fd._split_recent_dialogue_at_current_message(
        [
            {"source_message_id": "m1", "content": "第一条"},
            {"source_message_id": "m2", "content": "第二条"},
            {"source_message_id": "m3", "content": "第三条"},
        ],
        "m3",
    )

    assert [m["source_message_id"] for m in before] == ["m1", "m2"]
    assert [m["source_message_id"] for m in current] == ["m3"]


def test_work_ledger_context_does_not_repeat_current_work_text():
    fd, angel = _front_desk()
    angel.work_ledger.start_work(
        chat_id="aiocqhttp:GroupMessage:10000",
        work_id="current",
        trigger_message_id="m3",
        trigger_summary="第三条原文",
    )
    angel.work_ledger.start_work(
        chat_id="aiocqhttp:GroupMessage:10000",
        work_id="other",
        trigger_message_id="m1",
        trigger_summary="别的工作",
    )

    class E:
        def get_extra(self, key, default=None):
            if key == "angelheart_work_id":
                return "current"
            return default

    ctx = fd._build_temporary_work_ledger_context("aiocqhttp:GroupMessage:10000", E())
    text = ctx["content"][0]["text"]

    assert ctx["role"] == "user"
    assert ctx["_no_save"] is True
    assert "<system_reminder>" in text
    assert "第三条原文" not in text
    assert "别的工作" in text


def test_group_rewrite_keeps_assistant_history_in_contexts_and_only_current_message_in_prompt():
    import asyncio

    fd, angel = _front_desk()
    angel.work_ledger.start_work(
        chat_id="aiocqhttp:GroupMessage:10000",
        work_id="current",
        trigger_message_id="m3",
        trigger_summary="第三条当前消息",
    )
    angel.work_ledger.start_work(
        chat_id="aiocqhttp:GroupMessage:10000",
        work_id="other",
        trigger_message_id="m0",
        trigger_summary="已有其他工作",
    )

    req = SimpleNamespace(
        contexts=[],
        prompt="",
        image_urls=[],
        extra_user_content_parts=[],
        system_prompt="BASE SYSTEM",
    )
    event = _event("m3")
    event.set_extra("angelheart_work_id", "current")

    recent_dialogue = [
        {
            "role": "user",
            "content": "第一条用户",
            "sender_name": "甲",
            "sender_id": "1001",
            "timestamp": 1.0,
            "chat_id": "aiocqhttp:GroupMessage:10000",
            "source_message_id": "m1",
        },
        {
            "role": "assistant",
            "content": "第二条助理",
            "sender_name": "assistant",
            "sender_id": "bot",
            "timestamp": 2.0,
            "chat_id": "aiocqhttp:GroupMessage:10000",
            "source_message_id": "m2",
        },
        {
            "role": "user",
            "content": "第三条当前消息",
            "sender_name": "丙",
            "sender_id": "1003",
            "timestamp": 3.0,
            "chat_id": "aiocqhttp:GroupMessage:10000",
            "source_message_id": "m3",
        },
    ]

    asyncio.run(_run_group_rewrite(fd, event, req, recent_dialogue, historical_context=[]))

    assert "第三条当前消息" in req.prompt
    assert "第一条用户" not in req.prompt
    assert "第二条助理" not in req.prompt

    context_texts = []
    for message in req.contexts:
        content = message.get("content", "")
        if isinstance(content, str):
            context_texts.append(content)
        elif isinstance(content, list):
            context_texts.append("".join(item.get("text", "") for item in content if isinstance(item, dict)))

    joined_context = "\n".join(context_texts)
    assert "第一条用户" in joined_context
    assert "第二条助理" in joined_context
    assert "第三条当前消息" not in joined_context
    assert any(message.get("role") == "assistant" for message in req.contexts)
    assert req.contexts[-1]["_no_save"] is True
    assert req.contexts[-1]["role"] == "user"
    assert "已有其他工作" in context_texts[-1]
    assert "第三条当前消息" not in context_texts[-1]
    assert req.system_prompt == "BASE SYSTEM"
