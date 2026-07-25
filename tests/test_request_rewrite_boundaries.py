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
    config.focus_instructions = "分析 总结 好好想想 为什么 到底"
    config.normal_reply_max_chars = 20
    config.focus_reply_max_chars = 200
    angel = MagicMock()
    angel.work_ledger = WorkLedger()
    angel.astr_context = MagicMock()
    fd = FrontDesk(config, angel)
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
    temporary_contexts = [message for message in req.contexts if message.get("_no_save")]
    assert len(temporary_contexts) == 2
    assert temporary_contexts[0]["sender_id"] == "angelheart-work-ledger"
    assert temporary_contexts[1]["sender_id"] == "angelheart-reply-length"
    assert "已有其他工作" in temporary_contexts[0]["content"][0]["text"]
    assert "第三条当前消息" not in temporary_contexts[0]["content"][0]["text"]
    assert "【硬性长度约束】整段回复必须不超过 20 字。" in temporary_contexts[1]["content"][0]["text"]
    assert "只给结论，不要铺垫、解释、列点或补问。超字数视为失败。" in temporary_contexts[1]["content"][0]["text"]
    assert temporary_contexts[1]["angelheart_focus"] is False
    assert req.system_prompt == "BASE SYSTEM"


def test_group_rewrite_uses_focus_reply_length_when_focus_instruction_hits():
    import asyncio

    fd, _ = _front_desk()
    req = SimpleNamespace(
        contexts=[],
        prompt="",
        image_urls=[],
        extra_user_content_parts=[],
        system_prompt="BASE SYSTEM",
    )
    event = _event("m3")
    recent_dialogue = [
        {
            "role": "user",
            "content": "帮我好好想想这个问题",
            "sender_name": "甲",
            "sender_id": "1001",
            "timestamp": 3.0,
            "chat_id": "aiocqhttp:GroupMessage:10000",
            "source_message_id": "m3",
            "metadata": {
                "body_text": "帮我好好想想这个问题",
                "hits": [{"type": "focus", "phrase": "好好想想"}],
            },
        }
    ]

    asyncio.run(_run_group_rewrite(fd, event, req, recent_dialogue, historical_context=[]))

    length_context = next(
        message
        for message in req.contexts
        if message.get("sender_id") == "angelheart-reply-length"
    )
    assert length_context["angelheart_focus"] is True
    assert "【硬性长度约束】请认真回答，但整段回复必须不超过 200 字。" in length_context["content"][0]["text"]
    assert "先结论，后必要依据；不要注水、不要重复。超字数视为失败。" in length_context["content"][0]["text"]


def test_private_rewrite_does_not_inject_reply_length_reminder():
    import asyncio

    fd, _ = _front_desk()
    chat_id = "aiocqhttp:FriendMessage:10000"
    req = SimpleNamespace(
        contexts=[],
        prompt="",
        image_urls=[],
        extra_user_content_parts=[],
        system_prompt="BASE SYSTEM",
    )
    event = _event("m1")
    event.unified_msg_origin = chat_id
    fd._get_decision_context_for_rewrite = MagicMock(
        return_value=(
            [
                {
                    "role": "user",
                    "content": "帮我好好想想这个问题",
                    "sender_name": "甲",
                    "sender_id": "1001",
                    "timestamp": 1.0,
                    "chat_id": chat_id,
                    "source_message_id": "m1",
                }
            ],
            [],
            1.0,
        )
    )
    fd._ensure_minimum_context = AsyncMock()

    async def _run():
        await fd.rewrite_prompt_for_llm(chat_id, event, req)

    asyncio.run(_run())

    assert all(
        message.get("sender_id") != "angelheart-reply-length" for message in req.contexts
    )
    assert "【硬性长度约束】" not in req.prompt
    assert "整段回复必须不超过" not in req.prompt


def test_group_rewrite_replaces_current_astrbot_image_attachment_path_with_ledger_cache_path():
    import asyncio

    fd, _ = _front_desk()
    req = SimpleNamespace(
        contexts=[],
        prompt="",
        image_urls=["/AstrBot/data/temp/compressed_wrong.jpg"],
        extra_user_content_parts=[
            SimpleNamespace(text="[Image Attachment: path /AstrBot/data/temp/compressed_wrong.jpg]"),
            SimpleNamespace(text="[Image Attachment in quoted message: path /AstrBot/data/temp/quoted.jpg]"),
        ],
        system_prompt="BASE SYSTEM",
    )
    event = _event("m-current")
    cache_path = r"E:\github\ai-qq\astrbot\data\plugin_data\astrbot_plugin_angel_heart\media_cache\chat\image.webp"

    recent_dialogue = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "这是真的假的"},
                {
                    "type": "image_url",
                    "image_url": {"url": cache_path},
                    "cache_path": cache_path,
                    "local_file_path": cache_path,
                    "original_url": cache_path,
                    "original_file_url": cache_path,
                },
            ],
            "sender_name": "红豆泥",
            "sender_id": "289104862",
            "timestamp": 3.0,
            "chat_id": "aiocqhttp:GroupMessage:10000",
            "source_message_id": "m-current",
        },
    ]

    asyncio.run(_run_group_rewrite(fd, event, req, recent_dialogue, historical_context=[]))

    assert req.image_urls == []
    assert req.extra_user_content_parts[0].text == f"[Image Attachment: path {cache_path}]"
    assert req.extra_user_content_parts[1].text == "[Image Attachment in quoted message: path /AstrBot/data/temp/quoted.jpg]"
    assert cache_path not in req.prompt
    assert "[图片1]" in req.prompt
