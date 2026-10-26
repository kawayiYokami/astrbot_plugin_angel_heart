"""pytest 配置文件：统一测试桩源 + 桩身份守护。

所有 astrbot 框架桩在此一次性建齐，测试文件禁止再写 sys.modules 桩模块属性
（见 AGENTS.md「测试规范」）。桩只增不改：session 结束时按快照校验桩身份，
任何文件在收集期或执行期替换桩类都会在此报错。
"""

import gc
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# 路径接管（此前散落在各测试文件的 sys.path 段统一收口）：
# - 插件根父目录：支持 from astrbot_plugin_angel_heart.core... 包导入
# - 插件根本身：支持 from core.chat_sources... 顶层导入
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(PLUGIN_ROOT.parent), str(PLUGIN_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# 模块桩：覆盖插件源码实际 import 的全部 astrbot 路径
# ---------------------------------------------------------------------------

astrbot_module = types.ModuleType("astrbot")
astrbot_api_module = types.ModuleType("astrbot.api")
astrbot_api_event_module = types.ModuleType("astrbot.api.event")
astrbot_api_star_module = types.ModuleType("astrbot.api.star")
astrbot_api_provider_module = types.ModuleType("astrbot.api.provider")
astrbot_core_module = types.ModuleType("astrbot.core")
astrbot_core_agent_module = types.ModuleType("astrbot.core.agent")
astrbot_core_agent_message_module = types.ModuleType("astrbot.core.agent.message")
astrbot_core_message_module = types.ModuleType("astrbot.core.message")
astrbot_components_module = types.ModuleType("astrbot.core.message.components")
astrbot_core_star_module = types.ModuleType("astrbot.core.star")
astrbot_core_star_context_module = types.ModuleType("astrbot.core.star.context")
astrbot_core_star_register_module = types.ModuleType("astrbot.core.star.register")
astrbot_core_star_star_tools_module = types.ModuleType("astrbot.core.star.star_tools")
astrbot_core_star_filter_module = types.ModuleType("astrbot.core.star.filter")
astrbot_core_star_filter_command_module = types.ModuleType("astrbot.core.star.filter.command")
astrbot_core_star_filter_command_group_module = types.ModuleType(
    "astrbot.core.star.filter.command_group"
)


class AstrMessageEvent:
    pass


class FunctionTool:
    pass


class Plain:
    """带 text 属性，供消息链构造与输出改写钩子就地修改。"""

    def __init__(self, text=""):
        self.text = text


class At:
    """带 qq/name，支持位置与关键字两种构造。"""

    def __init__(self, qq="", name=""):
        self.qq = qq
        self.name = name


class AtAll:
    pass


class Reply:
    """带 id 属性，支持 Reply() 与 Reply(id=...) 两种构造。"""

    def __init__(self, id=""):
        self.id = id


class Poke:
    pass


class Image:
    pass


class File:
    def __init__(self, name="", file="", url=""):
        self.name = name
        self.file_ = file
        self.url = url

    async def get_file(self):
        return self.file_


class ImageURLPart:
    """兼容 dict 与 SimpleNamespace 两种 image_url 输入。"""

    def __init__(self, image_url):
        if isinstance(image_url, dict):
            image_url = types.SimpleNamespace(**image_url)
        self.image_url = image_url

    def model_dump_for_context(self):
        return {
            "type": "image_url",
            "image_url": {"url": self.image_url.url},
        }


class Context:
    """astrbot.api.star 与 astrbot.core.star.context 共用同一身份。"""


class Star:
    def __init__(self, context):
        self.context = context


class ProviderRequest:
    pass


class LLMResponse:
    pass


class CommandFilter:
    pass


class CommandGroupFilter:
    pass


class StarTools:
    @staticmethod
    def get_data_dir(name):
        return PLUGIN_ROOT


def _noop_decorator(*args, **kwargs):
    def decorator(func):
        return func

    return decorator


class _Filter:
    EventMessageType = type(
        "EventMessageType",
        (),
        {"GROUP_MESSAGE": 1, "PRIVATE_MESSAGE": 2},
    )
    event_message_type = staticmethod(_noop_decorator)
    on_llm_request = staticmethod(_noop_decorator)
    on_decorating_result = staticmethod(_noop_decorator)
    after_message_sent = staticmethod(_noop_decorator)


# ---- 符号挂载 ----

astrbot_api_module.FunctionTool = FunctionTool
astrbot_api_module.logger = MagicMock()

astrbot_api_event_module.AstrMessageEvent = AstrMessageEvent
astrbot_api_event_module.MessageChain = MagicMock
astrbot_api_event_module.filter = _Filter()

astrbot_api_star_module.Star = Star
astrbot_api_star_module.Context = Context
astrbot_api_star_module.register = _noop_decorator

astrbot_api_provider_module.ProviderRequest = ProviderRequest
astrbot_api_provider_module.LLMResponse = LLMResponse

astrbot_components_module.At = At
astrbot_components_module.AtAll = AtAll
astrbot_components_module.File = File
astrbot_components_module.Image = Image
astrbot_components_module.Plain = Plain
astrbot_components_module.Poke = Poke
astrbot_components_module.Reply = Reply

astrbot_core_agent_message_module.ImageURLPart = ImageURLPart

astrbot_core_star_context_module.Context = Context

astrbot_core_star_register_module.register_on_agent_done = _noop_decorator

astrbot_core_star_star_tools_module.StarTools = StarTools

astrbot_core_star_filter_command_module.CommandFilter = CommandFilter
astrbot_core_star_filter_command_group_module.CommandGroupFilter = CommandGroupFilter

# ---- sys.modules 注入（setdefault：容器内已有真实包时以既有者为准）----

for _module in (
    astrbot_module,
    astrbot_api_module,
    astrbot_api_event_module,
    astrbot_api_star_module,
    astrbot_api_provider_module,
    astrbot_core_module,
    astrbot_core_agent_module,
    astrbot_core_agent_message_module,
    astrbot_core_message_module,
    astrbot_components_module,
    astrbot_core_star_module,
    astrbot_core_star_context_module,
    astrbot_core_star_register_module,
    astrbot_core_star_star_tools_module,
    astrbot_core_star_filter_module,
    astrbot_core_star_filter_command_module,
    astrbot_core_star_filter_command_group_module,
):
    sys.modules.setdefault(_module.__name__, _module)


# ---------------------------------------------------------------------------
# 桩身份守护：桩只增不改
# ---------------------------------------------------------------------------

_PROTECTED_MODULES = (
    astrbot_api_module,
    astrbot_api_event_module,
    astrbot_api_star_module,
    astrbot_api_provider_module,
    astrbot_core_agent_message_module,
    astrbot_components_module,
    astrbot_core_star_context_module,
    astrbot_core_star_register_module,
    astrbot_core_star_star_tools_module,
    astrbot_core_star_filter_command_module,
    astrbot_core_star_filter_command_group_module,
)

# 建桩完成后拍快照；session 结束逐一比对 `is`
_STUB_SNAPSHOT = {module.__name__: dict(vars(module)) for module in _PROTECTED_MODULES}


@pytest.fixture(scope="session", autouse=True)
def guard_stub_identity():
    """全套件（或单跑任一文件）结束时校验桩类身份未被替换。"""
    yield
    drifts = []
    for module in _PROTECTED_MODULES:
        assert sys.modules.get(module.__name__) is module, (
            f"桩模块 {module.__name__} 已不在 sys.modules，测试环境与桩语义不符"
        )
        current = vars(module)
        expected = _STUB_SNAPSHOT[module.__name__]
        for name in set(expected) | set(current):
            if current.get(name) is not expected.get(name):
                drifts.append(f"{module.__name__}.{name}")
    assert not drifts, (
        "测试桩被中途替换（桩只增不改，见 AGENTS.md 测试规范），违规符号：\n  "
        + "\n  ".join(drifts)
    )


@pytest.fixture(autouse=True)
def cleanup_sqlite_connections():
    """每个测试结束后强制垃圾回收，释放 SQLite 连接"""
    yield
    gc.collect()
