from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from astrbot_plugin_angel_heart.core.message_processor import MessageProcessor
from astrbot_plugin_angel_heart.core.utils.message_utils import (
    estimate_provider_request_baseline_count,
    extract_completed_agent_messages,
    serialize_agent_run_message,
)
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


class DummyPart:
    def __init__(self, payload: dict):
        self.payload = payload

    def model_dump(self):
        return dict(self.payload)


class DummyMessage:
    def __init__(self, *, role: str, content=None, tool_calls=None, tool_call_id=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls
        self.tool_call_id = tool_call_id


def test_serialize_agent_run_message_keeps_tool_calls_and_think_parts():
    message = DummyMessage(
        role="assistant",
        content=[
            DummyPart({"type": "think", "think": "先想一步"}),
            DummyPart({"type": "text", "text": "最终回答"}),
        ],
        tool_calls=[
            DummyPart(
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            )
        ],
    )

    serialized = serialize_agent_run_message(
        message,
        timestamp=123.0,
        assistant_sender_id="bot_42",
    )

    assert serialized == {
        "role": "assistant",
        "content": [
            {"type": "think", "think": "先想一步"},
            {"type": "text", "text": "最终回答"},
        ],
        "timestamp": 123.0,
        "sender_id": "bot_42",
        "sender_name": "assistant",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "search", "arguments": "{}"},
            }
        ],
        "is_structured_toolcall": True,
    }


def test_serialize_agent_run_message_keeps_tool_result_linkage():
    message = DummyMessage(
        role="tool",
        content="工具结果",
        tool_call_id="call_1",
    )

    serialized = serialize_agent_run_message(
        message,
        timestamp=124.0,
        assistant_sender_id="bot_42",
    )

    assert serialized == {
        "role": "tool",
        "content": "工具结果",
        "timestamp": 124.0,
        "sender_id": "tool",
        "sender_name": "tool_result",
        "tool_call_id": "call_1",
        "is_structured_toolcall": True,
    }


def test_message_processor_preserves_assistant_structured_content():
    processor = MessageProcessor("fairy")
    msg = {
        "role": "assistant",
        "content": [
            DummyPart({"type": "think", "think": "这一步先分析"}),
            DummyPart({"type": "text", "text": "最后结论"}),
        ],
    }

    processed = processor.process_message(msg)

    assert processed == {
        "role": "assistant",
        "content": [
            {"type": "think", "think": "这一步先分析"},
            {"type": "text", "text": "最后结论"},
        ],
    }


def test_filter_images_for_provider_keeps_assistant_think_parts():
    config = MagicMock()
    config.alias = "fairy"
    config.image_caption_provider_id = ""
    angel = MagicMock()
    angel.astr_context = MagicMock()
    front_desk = FrontDesk(config, angel)

    provider = SimpleNamespace(provider_config={"id": "text-only", "modalities": ["text"]})
    front_desk.context.astr_context.get_using_provider.return_value = provider

    contexts = [
        {
            "role": "assistant",
            "content": [
                {"type": "think", "think": "这是思维链"},
                {"type": "text", "text": "这是正文"},
                {"type": "image_url", "image_url": {"url": "file:///tmp/a.png"}},
            ],
        }
    ]

    filtered = front_desk.filter_images_for_provider("chat-1", contexts)

    assert filtered == [
        {
            "role": "assistant",
            "content": [
                {"type": "think", "think": "这是思维链"},
                {"type": "text", "text": "这是正文"},
            ],
        }
    ]


def test_estimate_provider_request_baseline_count_includes_context_user_and_system():
    provider_request = SimpleNamespace(
        contexts=[{"role": "assistant", "content": "历史1"}, {"role": "user", "content": "历史2"}],
        prompt="当前用户问题",
        image_urls=[],
        audio_urls=[],
        extra_user_content_parts=[],
        system_prompt="system prompt",
    )

    assert estimate_provider_request_baseline_count(provider_request) == 4


def test_estimate_provider_request_baseline_count_skips_checkpoint_items():
    provider_request = SimpleNamespace(
        contexts=[
            {"role": "assistant", "content": "历史assistant"},
            {"role": "_checkpoint", "content": {"id": "cp-1"}},
            {"role": "user", "content": "历史user"},
        ],
        prompt="当前用户问题",
        image_urls=[],
        audio_urls=[],
        extra_user_content_parts=[],
        system_prompt="",
    )

    # checkpoint 不会进入 run_context.messages，因此基线只计 2 条历史 + 1 条当前 user。
    assert estimate_provider_request_baseline_count(provider_request) == 3


def test_extract_completed_agent_messages_only_keeps_new_assistant_tool_chain():
    provider_request = SimpleNamespace(
        contexts=[
            {"role": "assistant", "content": "历史assistant"},
            {"role": "_checkpoint", "content": {"id": "cp-1"}},
        ],
        prompt="当前用户问题",
        image_urls=[],
        audio_urls=[],
        extra_user_content_parts=[],
        system_prompt="",
    )
    messages = [
        DummyMessage(role="assistant", content="历史assistant"),
        DummyMessage(role="user", content="当前用户问题"),
        DummyMessage(role="assistant", tool_calls=[DummyPart({"id": "call_1"})], content=[]),
        DummyMessage(role="tool", content="工具结果", tool_call_id="call_1"),
        DummyMessage(role="assistant", content=[DummyPart({"type": "text", "text": "最终回答"})]),
    ]

    extracted = extract_completed_agent_messages(messages, provider_request)

    assert [message.role for message in extracted] == ["assistant", "tool", "assistant"]
    assert extracted[0].tool_calls[0].model_dump() == {"id": "call_1"}
    assert extracted[1].tool_call_id == "call_1"
    assert extracted[2].content[0].model_dump() == {"type": "text", "text": "最终回答"}


def test_extract_completed_agent_messages_drops_leading_orphan_tool_result():
    """切片起点落在工具链中间时，无主回执不能进账本。"""
    provider_request = SimpleNamespace(
        contexts=[{"role": "user", "content": "更早的问题"}],
        prompt="当前用户问题",
        image_urls=[],
        audio_urls=[],
        extra_user_content_parts=[],
        system_prompt="",
    )
    messages = [
        DummyMessage(role="assistant", content="更早的回答"),
        DummyMessage(role="assistant", tool_calls=[DummyPart({"id": "call_orphan"})], content=[]),
        DummyMessage(role="tool", content="无主结果", tool_call_id="call_orphan"),
        DummyMessage(role="assistant", content=[DummyPart({"type": "text", "text": "后一条回答"})]),
    ]

    extracted = extract_completed_agent_messages(messages, provider_request)

    # 基线只覆盖 1 条历史 + 1 条 prompt，切片从 call_orphan 的回执开始，
    # 这条回执的调用不在切片内，必须丢弃，否则账本里就是一条无主回执。
    assert [message.role for message in extracted] == ["assistant"]
    assert extracted[0].content[0].model_dump() == {"type": "text", "text": "后一条回答"}
