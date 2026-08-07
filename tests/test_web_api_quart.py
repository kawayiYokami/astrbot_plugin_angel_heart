"""web_api 冒烟测试：quart 回退路径下路由注册与 CRUD/绑定全流程。

conftest.py 已提供 astrbot 桩且不含 astrbot.api.web 子模块，
因此 web_api 导入时自动走 quart 回退分支（HAS_WEB_API=False）。
"""

import pytest

from core.chat_profile import ChatProfileStore
from core.config_manager import ConfigManager


class FakeContext:
    """模拟 Star.context：只收集 register_web_api 注册。"""

    def __init__(self):
        self.routes = []

    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, handler, methods, desc))


@pytest.fixture
def api_app(tmp_path):
    """构建一个挂载了 web_api 路由的 quart app。"""
    from quart import Quart

    store = ChatProfileStore(str(tmp_path))
    manager = ConfigManager({})
    ledger = type("Ledger", (), {"get_all_chat_ids": staticmethod(lambda: ["chat:g:1"])})()
    fake = FakeContext()

    import web_api as web_api_module
    web_api_module.register_all_routes(fake, store, manager, ledger)

    app = Quart(__name__)
    for route, handler, methods, _ in fake.routes:
        # 路由形如 /astrbot_plugin_angel_heart/profiles -> 挂到 /api/plug 下
        path = "/api/plug" + route
        app.add_url_rule(path, endpoint=path, view_func=handler, methods=methods)
    return app


def make_app_with_chat_sources(tmp_path):
    """构造挂载了 chat_sources 的 quart app；白名单含纯群号、来源登记含完整 origin。"""
    from quart import Quart

    from core.chat_sources import ChatSourcesStore

    store = ChatProfileStore(str(tmp_path))
    manager = ConfigManager(
        {"access_control": {"chat_ids": ["1051472372", "830624502", "999000111"]}}
    )
    ledger = type("Ledger", (), {"get_all_chat_ids": staticmethod(lambda: [])})()
    sources = ChatSourcesStore(str(tmp_path))
    sources.record(
        "default:GroupMessage:1051472372", "zlb米游剧情AI工具测试群", "group"
    )
    sources.record("default:GroupMessage:830624502", "绝区零&一条龙开发社群", "group")
    fake = FakeContext()

    import web_api as web_api_module
    web_api_module.register_all_routes(fake, store, manager, ledger, chat_sources=sources)

    app = Quart(__name__)
    for route, handler, methods, _ in fake.routes:
        path = "/api/plug" + route
        app.add_url_rule(path, endpoint=path, view_func=handler, methods=methods)
    return app


@pytest.mark.asyncio
async def test_list_chats_dedup_plain_id_vs_unified_origin(tmp_path):
    """同一群同时以纯群号（白名单）与完整 origin（来源登记）出现时只保留一条。"""
    from quart import Quart

    app = make_app_with_chat_sources(tmp_path)
    client = app.test_client()

    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/chats")
    chats = (await resp.get_json())["data"]

    # 1051472372 与 830624502 各只出现一次，且保留带显示名的完整 origin
    ids = [c["chat_id"] for c in chats]
    assert ids.count("1051472372") == 0
    assert ids.count("default:GroupMessage:1051472372") == 1
    assert ids.count("830624502") == 0
    assert ids.count("default:GroupMessage:830624502") == 1
    by_id = {c["chat_id"]: c for c in chats}
    assert by_id["default:GroupMessage:1051472372"]["display_name"] == "zlb米游剧情AI工具测试群"
    assert by_id["default:GroupMessage:830624502"]["display_name"] == "绝区零&一条龙开发社群"
    # 只在白名单、无来源登记的群保留纯群号条目
    assert by_id["999000111"]["display_name"] == ""


@pytest.mark.asyncio
async def test_list_chats_filters_outside_whitelist_when_enabled(tmp_path):
    """白名单启用后，白名单外的来源/ledger 会话不再出现在 chat-config。"""
    from quart import Quart

    from core.chat_sources import ChatSourcesStore

    store = ChatProfileStore(str(tmp_path))
    manager = ConfigManager(
        {
            "access_control": {
                "whitelist_enabled": True,
                "chat_ids": ["1051472372", "999000111"],
            }
        }
    )
    ledger = type(
        "Ledger",
        (),
        {"get_all_chat_ids": staticmethod(lambda: ["default:GroupMessage:830624502"])},
    )()
    sources = ChatSourcesStore(str(tmp_path))
    sources.record("default:GroupMessage:1051472372", "白名单群", "group")
    fake = FakeContext()

    import web_api as web_api_module

    web_api_module.register_all_routes(fake, store, manager, ledger, chat_sources=sources)

    app = Quart(__name__)
    for route, handler, methods, _ in fake.routes:
        path = "/api/plug" + route
        app.add_url_rule(path, endpoint=path, view_func=handler, methods=methods)

    client = app.test_client()
    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/chats")
    chats = (await resp.get_json())["data"]
    ids = [c["chat_id"] for c in chats]

    assert "default:GroupMessage:830624502" not in ids
    assert "default:GroupMessage:1051472372" in ids
    assert "999000111" in ids


@pytest.mark.asyncio
async def test_full_crud_and_binding_flow(api_app):
    client = api_app.test_client()

    # 初始为空
    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/profiles")
    data = await resp.get_json()
    assert data["status"] == "ok"
    assert data["data"]["templates"] == []
    assert data["data"]["bindings"] == {}
    assert "global_config" in data["data"]

    # 创建模板（从全局复制）
    resp = await client.post(
        "/api/plug/astrbot_plugin_angel_heart/profiles/create",
        json={"name": "游戏群", "from_global": True},
    )
    data = await resp.get_json()
    assert data["status"] == "ok"
    tpl_id = data["data"]["id"]

    # 更新模板配置
    resp = await client.post(
        "/api/plug/astrbot_plugin_angel_heart/profiles/update",
        json={"id": tpl_id, "config": {"timing": {"waiting_time": 5.0}}},
    )
    assert (await resp.get_json())["status"] == "ok"

    # 绑定群聊
    resp = await client.post(
        "/api/plug/astrbot_plugin_angel_heart/bindings/set",
        json={"chat_id": "chat:g:1", "template_id": tpl_id},
    )
    assert (await resp.get_json())["status"] == "ok"

    # 群聊列表带绑定
    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/chats")
    chats = (await resp.get_json())["data"]
    assert any(c["chat_id"] == "chat:g:1" and c["template_id"] == tpl_id for c in chats)

    # profiles 的 bindings 是 {chat_id: template_id} 字典（前端契约）
    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/profiles")
    bindings = (await resp.get_json())["data"]["bindings"]
    assert bindings == {"chat:g:1": tpl_id}
    assert isinstance(bindings, dict)

    # 模板列表含 config
    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/profiles")
    templates = (await resp.get_json())["data"]["templates"]
    assert templates[0]["config"]["timing"]["waiting_time"] == 5.0

    # 删除模板 -> 级联解绑
    resp = await client.post(
        "/api/plug/astrbot_plugin_angel_heart/profiles/delete",
        json={"id": tpl_id},
    )
    assert (await resp.get_json())["status"] == "ok"
    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/bindings")
    assert (await resp.get_json())["data"] == []


@pytest.mark.asyncio
async def test_validation_errors(api_app):
    client = api_app.test_client()

    resp = await client.post(
        "/api/plug/astrbot_plugin_angel_heart/profiles/create",
        json={"name": "  "},
    )
    data = await resp.get_json()
    assert data["status"] == "error"

    resp = await client.post(
        "/api/plug/astrbot_plugin_angel_heart/profiles/update",
        json={"id": "tpl_nope", "name": "x"},
    )
    data = await resp.get_json()
    assert data["status"] == "error"

    resp = await client.post(
        "/api/plug/astrbot_plugin_angel_heart/bindings/set",
        json={"chat_id": "chat:g:9", "template_id": "tpl_nope"},
    )
    data = await resp.get_json()
    assert data["status"] == "error"


@pytest.mark.asyncio
async def test_update_profile_rejects_non_dict_config(api_app):
    """update_profile 传非 dict 的 config 应拒绝，避免存坏数据。"""
    client = api_app.test_client()
    resp = await client.post(
        "/api/plug/astrbot_plugin_angel_heart/profiles/create",
        json={"name": "tpl", "from_global": True},
    )
    tpl_id = (await resp.get_json())["data"]["id"]

    resp = await client.post(
        "/api/plug/astrbot_plugin_angel_heart/profiles/update",
        json={"id": tpl_id, "config": "not-a-dict"},
    )
    data = await resp.get_json()
    assert data["status"] == "error"

    # 模板配置未被污染
    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/profiles")
    templates = (await resp.get_json())["data"]["templates"]
    assert isinstance(templates[0]["config"], dict)


@pytest.mark.asyncio
async def test_chat_status_merges_runtime_state(tmp_path):
    """chat_status 合并状态/能量/巡检/最近决策/绑定；依赖缺失时优雅降级。"""
    from quart import Quart

    from core.chat_sources import ChatSourcesStore
    from core.last_decisions import LastDecisionStore

    store = ChatProfileStore(str(tmp_path))
    manager = ConfigManager({})
    ledger = type("Ledger", (), {"get_all_chat_ids": staticmethod(lambda: ["default:GroupMessage:10001"])})()
    sources = ChatSourcesStore(str(tmp_path))
    sources.record("default:GroupMessage:10001", "测试群", "group")
    decisions = LastDecisionStore(str(tmp_path))
    decisions.record("default:GroupMessage:10001", True, "话题相关")

    class FakeStatusManager:
        def get_status_summary(self, chat_id):
            return {"current_status": "OBSERVATION", "duration_seconds": 12.0}

    class FakeDebounce:
        async def patrol_snapshot(self, chat_id):
            return {"waiting": "secretary", "remaining": 5.0, "total": 30.0}

        def get_chat_energy(self, chat_id):
            return 62.0

    fake = FakeContext()
    import web_api as web_api_module
    web_api_module.register_all_routes(
        fake, store, manager, ledger,
        chat_sources=sources,
        status_transition_manager=FakeStatusManager(),
        debounce_manager=FakeDebounce(),
        last_decisions=decisions,
    )

    app = Quart(__name__)
    for route, handler, methods, _ in fake.routes:
        path = "/api/plug" + route
        app.add_url_rule(path, endpoint=path, view_func=handler, methods=methods)

    client = app.test_client()
    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/chat_status")
    items = (await resp.get_json())["data"]
    assert len(items) == 1
    item = items[0]
    assert item["chat_id"] == "default:GroupMessage:10001"
    assert item["display_name"] == "测试群"
    assert item["status"]["current_status"] == "OBSERVATION"
    assert item["energy"] == 62.0
    assert item["patrol"] == {"waiting": "secretary", "remaining": 5.0, "total": 30.0}
    assert item["last_decision"]["should_reply"] is True
    assert item["last_decision"]["summary"] == "话题相关"


@pytest.mark.asyncio
async def test_chat_status_without_runtime_refs_falls_back(api_app):
    """未注入状态/防抖/决策引用时，字段降级为默认形状，不报错。"""
    client = api_app.test_client()
    resp = await client.get("/api/plug/astrbot_plugin_angel_heart/chat_status")
    items = (await resp.get_json())["data"]
    assert len(items) == 1  # ledger 提供 chat:g:1
    item = items[0]
    assert item["chat_id"] == "chat:g:1"
    assert item["status"] == {
        "current_status": "Unknown",
        "duration_seconds": 0,
        "duration_minutes": 0,
        "has_assistant_debounce": False,
        "has_secretary_debounce": False,
    }
    assert item["energy"] is None
    assert item["patrol"] == {"waiting": "", "remaining": 0.0, "total": 0.0}
    assert item["last_decision"] is None
