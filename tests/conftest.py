"""pytest 配置文件：补齐 AstrBot 最小测试桩并清理 SQLite 连接。"""

import gc
import sys
import types

import pytest


astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_api_event_module = types.ModuleType("astrbot.api.event")
astrbot_core_module = types.ModuleType("astrbot.core")
astrbot_core_agent_module = types.ModuleType("astrbot.core.agent")
astrbot_core_agent_message_module = types.ModuleType("astrbot.core.agent.message")
astrbot_core_message_module = types.ModuleType("astrbot.core.message")
astrbot_components_module = types.ModuleType("astrbot.core.message.components")


class AstrMessageEvent:
    pass


class At:
    pass


class Image:
    pass


class Plain:
    pass


class Reply:
    pass


class ImageURLPart:
    def __init__(self, image_url):
        if isinstance(image_url, dict):
            image_url = types.SimpleNamespace(**image_url)
        self.image_url = image_url

    def model_dump_for_context(self):
        return {
            "type": "image_url",
            "image_url": {"url": self.image_url.url},
        }


astrbot_api_event_module.AstrMessageEvent = AstrMessageEvent
astrbot_components_module.At = At
astrbot_components_module.Image = Image
astrbot_components_module.Plain = Plain
astrbot_components_module.Reply = Reply
astrbot_core_agent_message_module.ImageURLPart = ImageURLPart

sys.modules.setdefault("astrbot", astrbot_module)
sys.modules.setdefault("astrbot.api", astrbot_api_module)
sys.modules.setdefault("astrbot.api.event", astrbot_api_event_module)
sys.modules.setdefault("astrbot.core", astrbot_core_module)
sys.modules.setdefault("astrbot.core.agent", astrbot_core_agent_module)
sys.modules.setdefault("astrbot.core.agent.message", astrbot_core_agent_message_module)
sys.modules.setdefault("astrbot.core.message", astrbot_core_message_module)
sys.modules.setdefault("astrbot.core.message.components", astrbot_components_module)


@pytest.fixture(autouse=True)
def cleanup_sqlite_connections():
    """每个测试结束后强制垃圾回收，释放 SQLite 连接"""
    yield
    gc.collect()
