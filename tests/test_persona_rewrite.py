"""Persona examples survive ledger takeover without becoming chat history."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


def _scenario(chat_type="GroupMessage", conversation_persona_id=None):
    chat_id = f"aiocqhttp:{chat_type}:10000"
    config = MagicMock()
    config.for_chat.return_value = config
    config.alias = "test persona"
    config.focus_instructions = "分析"
    config.normal_reply_max_chars = 20
    config.focus_reply_max_chars = 200
    angel = MagicMock()
    angel.work_ledger = WorkLedger()
    resolver = AsyncMock()
    angel.astr_context = SimpleNamespace(
        persona_manager=SimpleNamespace(resolve_selected_persona=resolver)
    )
    fd = FrontDesk(config, angel)
    fd._ensure_minimum_context = AsyncMock()
    fd.filter_images_for_provider = lambda _chat_id, contexts: contexts
    fd._should_preserve_current_image_urls = lambda _: False
    fd._get_decision_context_for_rewrite = lambda *_: (
        [{
            "role": "user", "content": "今天怎么样", "sender_name": "同学",
            "sender_id": "1001", "timestamp": 2.0, "chat_id": chat_id,
            "source_message_id": "current",
        }],
        [{
            "role": "assistant", "content": "账本中的真实回复",
            "sender_name": "test persona", "sender_id": "1002",
            "timestamp": 1.0, "chat_id": chat_id, "source_message_id": "history",
        }],
        2.0,
    )
    event = SimpleNamespace(
        unified_msg_origin=chat_id,
        message_obj=SimpleNamespace(message_id="current"),
        get_extra=lambda _key, default=None: default,
        get_messages=lambda: [],
        get_platform_name=lambda: "aiocqhttp",
    )
    req = SimpleNamespace(
        conversation=SimpleNamespace(persona_id=conversation_persona_id),
        contexts=[{"role": "assistant", "content": "旧人格示例，不能沿用"}],
        prompt="今天怎么样", image_urls=[], extra_user_content_parts=[],
        system_prompt="当前人格系统提示词",
    )
    return fd, event, req, resolver


def _examples(label):
    return [
        {"role": "user", "content": f"{label}示例提问", "_no_save": True},
        {"role": "assistant", "content": f"{label}示例回答", "_no_save": True},
    ]


@pytest.mark.parametrize("chat_type", ["GroupMessage", "FriendMessage"])
@pytest.mark.parametrize("conversation_persona_id", [None, "conversation-persona"])
def test_rewrite_keeps_framework_selected_examples_without_reusing_old_prefix(
    chat_type, conversation_persona_id
):
    fd, event, req, resolver = _scenario(chat_type, conversation_persona_id)
    # A forced session persona may differ from both the default and conversation.
    examples = _examples("会话强制人格")
    snapshot = copy.deepcopy(examples)
    resolver.return_value = (
        "forced-persona", {"_begin_dialogs_processed": examples}, "forced-persona", False
    )

    async def run():
        await fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req)
        first = [
            {key: value for key, value in message.items() if key != "timestamp"}
            for message in req.contexts
        ]
        await fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req)
        second = [
            {key: value for key, value in message.items() if key != "timestamp"}
            for message in req.contexts
        ]
        assert second == first  # Only temporary ledger timestamps may change.

    asyncio.run(run())

    assert req.contexts[:2] == snapshot
    assert req.contexts[0] is not examples[0]
    assert examples == snapshot
    assert all(message["_no_save"] for message in req.contexts[:2])
    assert "账本中的真实回复" in str(req.contexts[2:])
    assert "旧人格示例" not in str(req.contexts)
    assert "会话强制人格示例提问" not in req.prompt
    assert req.system_prompt.startswith("当前人格系统提示词")
    resolver.assert_awaited_with(
        umo=event.unified_msg_origin,
        conversation_persona_id=conversation_persona_id,
        platform_name="aiocqhttp",
    )


def test_persona_change_uses_new_examples_on_the_next_rewrite():
    fd, event, req, resolver = _scenario()
    resolver.side_effect = [
        ("first", {"_begin_dialogs_processed": _examples("第一人格")}, None, False),
        ("second", {"_begin_dialogs_processed": _examples("第二人格")}, None, False),
    ]

    async def run():
        await fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req)
        assert req.contexts[:2] == _examples("第一人格")
        req.conversation.persona_id = "second"
        await fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req)

    asyncio.run(run())
    assert req.contexts[:2] == _examples("第二人格")
    assert "第一人格" not in str(req.contexts)
    assert resolver.await_args.kwargs["conversation_persona_id"] == "second"


@pytest.mark.parametrize("selected", [
    ("[%None]", None, None, False),
    ("empty", {"_begin_dialogs_processed": []}, None, False),
])
def test_disabled_or_empty_persona_does_not_reuse_stale_examples(selected):
    fd, event, req, resolver = _scenario(conversation_persona_id=selected[0])
    resolver.return_value = selected
    asyncio.run(fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req))
    assert "旧人格示例" not in str(req.contexts)
    assert "账本中的真实回复" in str(req.contexts)


def test_persona_lookup_failure_preserves_ledger_reply_without_stale_examples():
    fd, event, req, resolver = _scenario()
    resolver.side_effect = RuntimeError("persona unavailable")
    asyncio.run(fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req))
    assert "旧人格示例" not in str(req.contexts)
    assert "账本中的真实回复" in str(req.contexts)
    assert "今天怎么样" in req.prompt


def test_request_without_conversation_does_not_invent_a_default_persona():
    fd, event, req, resolver = _scenario()
    req.conversation = None
    asyncio.run(fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req))
    resolver.assert_not_called()
    assert "旧人格示例" not in str(req.contexts)
    assert "账本中的真实回复" in str(req.contexts)
