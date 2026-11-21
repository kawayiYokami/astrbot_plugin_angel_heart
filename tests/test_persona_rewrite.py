"""Persona examples survive ledger takeover without becoming chat history."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


class _BaseResolver:
    """Shared call recording, side_effect and assertion helpers for fake resolvers."""

    def __init__(self):
        self.return_value = None
        self.side_effect = None
        self.calls = []

    def assert_not_called(self):
        assert not self.calls, f"expected no resolver calls, got {self.calls}"

    def assert_awaited_with(self, **kwargs):
        assert self.calls, "expected the resolver to be called, but it was not"
        assert self.calls[-1] == kwargs, f"expected {kwargs}, got {self.calls[-1]}"

    @property
    def await_args(self):
        assert self.calls, "expected the resolver to be called, but it was not"
        return SimpleNamespace(kwargs=self.calls[-1])

    def _record(self, payload):
        self.calls.append(payload)
        effect = self.side_effect
        if effect is None:
            return self.return_value
        if isinstance(effect, list):
            return effect.pop(0)
        raise effect


class _LegacyResolver(_BaseResolver):
    """Stand-in for the pre-4.30 resolver: takes conversation_persona_id, returns 4-tuple."""

    async def __call__(self, *, umo, conversation_persona_id, platform_name, **kwargs):
        return self._record(
            {
                "umo": umo,
                "conversation_persona_id": conversation_persona_id,
                "platform_name": platform_name,
                **kwargs,
            }
        )


class _ModernResolver(_BaseResolver):
    """Stand-in for the 4.30 resolver: umo / platform_name only, returns 3-tuple."""

    async def __call__(self, *, umo, platform_name):
        return self._record({"umo": umo, "platform_name": platform_name})


def _scenario(chat_type="GroupMessage", conversation_persona_id=None, resolver=None):
    chat_id = f"aiocqhttp:{chat_type}:10000"
    config = MagicMock()
    config.for_chat.return_value = config
    config.alias = "test persona"
    config.focus_instructions = "分析"
    config.normal_reply_max_chars = 20
    config.focus_reply_max_chars = 200
    angel = MagicMock()
    angel.work_ledger = WorkLedger()
    resolver = resolver or _LegacyResolver()
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


@pytest.mark.asyncio
async def test_default_persona_resolution_uses_effective_session_provider_settings():
    fd, event, req, _ = _scenario()
    settings = {"default_personality": "session-default"}
    original = copy.deepcopy(settings)
    get_config = MagicMock(return_value={"provider_settings": settings})
    fd.astr_context.get_config = get_config
    examples = _examples("会话默认人格")

    async def resolve(*, umo, conversation_persona_id, platform_name, provider_settings=None):
        assert umo == event.unified_msg_origin
        assert conversation_persona_id is None
        assert platform_name == "aiocqhttp"
        selected = (provider_settings or {}).get("default_personality")
        return selected, {"_begin_dialogs_processed": examples} if selected else None, None, False

    fd.astr_context.persona_manager.resolve_selected_persona = resolve

    await fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req)

    # #95 之后，_persona_begin_dialogs_for_request 也会读同一份会话配置，
    # 这里只要求解析器拿到的是本会话的配置，不限制调用次数。
    get_config.assert_called_with(event.unified_msg_origin)
    assert req.contexts[:2] == examples
    assert req.contexts[0] is not examples[0]
    assert settings == original


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


def test_rewrite_marks_unflagged_examples_without_mutating_persona():
    fd, event, req, resolver = _scenario()
    examples = [
        {"role": "user", "content": "示例提问"},
        {"role": "assistant", "content": "示例回答"},
    ]
    snapshot = copy.deepcopy(examples)
    resolver.return_value = (
        "selected", {"_begin_dialogs_processed": examples}, None, False
    )

    asyncio.run(fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req))

    assert req.contexts[:2] == [{**message, "_no_save": True} for message in snapshot]
    assert examples == snapshot
    assert all("_no_save" not in message for message in examples)
    assert all(copy is not original for copy, original in zip(req.contexts, examples))


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


@pytest.mark.parametrize("chat_type", ["GroupMessage", "FriendMessage"])
def test_modern_resolver_keeps_examples_without_legacy_kwargs(chat_type):
    """AstrBot 4.30 收窄签名后，预设对话仍被保留，且不再传旧参数。"""
    resolver = _ModernResolver()
    fd, event, req, _ = _scenario(chat_type, resolver=resolver)
    examples = _examples("4.30 人格")
    resolver.return_value = ("persona-id", {"_begin_dialogs_processed": examples}, False)

    asyncio.run(fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req))

    assert req.contexts[:2] == examples
    assert req.contexts[0] is not examples[0]
    assert resolver.calls == [
        {"umo": event.unified_msg_origin, "platform_name": "aiocqhttp"}
    ]


def test_modern_resolver_failure_still_preserves_ledger_reply():
    """4.30 签名下解析失败，同样退回账本内容而不是旧人格示例。"""
    resolver = _ModernResolver()
    fd, event, req, _ = _scenario(resolver=resolver)
    resolver.side_effect = RuntimeError("persona unavailable")

    asyncio.run(fd.rewrite_prompt_for_llm(event.unified_msg_origin, event, req))

    assert "旧人格示例" not in str(req.contexts)
    assert "账本中的真实回复" in str(req.contexts)
