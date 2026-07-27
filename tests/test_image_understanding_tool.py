from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parent))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astrbot_plugin_angel_heart.tools.image_understanding import AngelDescribeImageTool


def test_image_tool_keeps_registered_schema():
    tool = AngelDescribeImageTool()

    assert tool.name == "angel_describe_image"
    assert tool.parameters == {
        "type": "object",
        "properties": {
            "focus": {
                "type": "string",
                "description": "希望从图片中确认的具体内容，例如“读取右下角的报错文字”或“比较这张图中的两个数值”。",
            },
            "path": {
                "type": "string",
                "description": "当前会话 AngelHeart 上下文中显示的图片路径；只能使用其中已有的单张图片路径。",
            },
        }
    }


def test_image_tool_passes_current_event_dependencies_to_ledger():
    calls = []

    class Ledger:
        async def describe_image(self, **kwargs):
            calls.append(kwargs)
            return "图片描述"

    astr_context = object()
    tool = AngelDescribeImageTool(
        conversation_ledger=Ledger(),
        config_manager=SimpleNamespace(image_caption_provider_id="vision"),
        astr_context=astr_context,
    )

    result = asyncio.run(
        tool.run(
            SimpleNamespace(unified_msg_origin="aiocqhttp:GroupMessage:10000"),
            focus="读取右下角文字",
            path="file:///tmp/ledger-image.png",
        )
    )

    assert result == "图片描述"
    assert calls == [
        {
            "chat_id": "aiocqhttp:GroupMessage:10000",
            "focus": "读取右下角文字",
            "path": "file:///tmp/ledger-image.png",
            "caption_provider_id": "vision",
            "astr_context": astr_context,
        }
    ]
