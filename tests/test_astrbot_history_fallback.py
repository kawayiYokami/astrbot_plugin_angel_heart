from __future__ import annotations

import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parent))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_api_event_module = types.ModuleType("astrbot.api.event")
astrbot_core_module = types.ModuleType("astrbot.core")
astrbot_core_message_module = types.ModuleType("astrbot.core.message")
astrbot_components_module = types.ModuleType("astrbot.core.message.components")


class AstrMessageEvent:
    pass


class Image:
    pass


class Plain:
    pass


astrbot_api_event_module.AstrMessageEvent = AstrMessageEvent
astrbot_components_module.Image = Image
astrbot_components_module.Plain = Plain

sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)
sys.modules.setdefault("astrbot.api.event", astrbot_api_event_module)
sys.modules.setdefault("astrbot.core", astrbot_core_module)
sys.modules.setdefault("astrbot.core.message", astrbot_core_message_module)
sys.modules.setdefault("astrbot.core.message.components", astrbot_components_module)

from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


def _front_desk() -> FrontDesk:
    return object.__new__(FrontDesk)


def test_astrbot_history_fallback_limits_to_latest_seven_text_messages():
    front_desk = _front_desk()
    history = [{"role": "user", "content": f"消息{i}"} for i in range(10)]

    messages = front_desk._convert_astrbot_history_to_angelheart_format(history, 19)

    assert len(messages) == 7
    assert [msg["content"] for msg in messages] == [f"消息{i}" for i in range(3, 10)]


def test_astrbot_history_fallback_skips_tools_and_tool_calls():
    front_desk = _front_desk()
    history = [
        {"role": "user", "content": "保留1"},
        {"role": "assistant", "content": "跳过工具调用", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "跳过工具结果"},
        {"role": "assistant", "content": "保留2"},
    ]

    messages = front_desk._convert_astrbot_history_to_angelheart_format(history, 19)

    assert [msg["content"] for msg in messages] == ["保留1", "保留2"]


def test_astrbot_history_fallback_extracts_openai_content_parts():
    front_desk = _front_desk()
    history = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "正文"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                {"type": "text", "text": "补充"},
            ],
        },
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "工具结果", "tool_call_id": "1"},
    ]

    messages = front_desk._convert_astrbot_history_to_angelheart_format(history, 19)

    assert [msg["content"] for msg in messages] == ["正文 补充"]


def test_astrbot_history_fallback_stops_at_10k_text_tokens():
    front_desk = _front_desk()
    huge_text = "a" * 40000
    history = [
        {"role": "user", "content": "短消息"},
        {"role": "assistant", "content": huge_text},
        {"role": "user", "content": "最新消息"},
    ]

    messages = front_desk._convert_astrbot_history_to_angelheart_format(history, 19)

    assert [msg["content"] for msg in messages] == ["最新消息"]
