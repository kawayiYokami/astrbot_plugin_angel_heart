"""On-demand image understanding tool."""

from dataclasses import dataclass, field
from typing import Any

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent


@dataclass
class AngelDescribeImageTool(FunctionTool):
    """Describe a ledger image with the configured vision provider."""

    conversation_ledger: Any = field(repr=False, default=None)
    config_manager: Any = field(repr=False, default=None)
    astr_context: Any = field(repr=False, default=None)
    name: str = "angel_describe_image"
    description: str = "当你当前看不到图片、但需要某张历史图片的细节时才调用。"
    parameters: dict = field(
        default_factory=lambda: {
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
            },
        }
    )

    async def run(
        self,
        event: AstrMessageEvent,
        focus: str,
        path: str,
    ) -> str:
        return await self.conversation_ledger.describe_image(
            chat_id=event.unified_msg_origin,
            path=path,
            focus=focus,
            caption_provider_id=self.config_manager.image_caption_provider_id,
            astr_context=self.astr_context,
        )
