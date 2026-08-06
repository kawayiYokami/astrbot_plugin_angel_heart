"""ConfigManager.for_chat 覆盖视图测试：模板字段优先、未绑定回退全局、非模板字段不受影响。"""

import pytest

from core.chat_profile import ChatProfileStore
from core.config_manager import ConfigManager


@pytest.fixture
def global_config():
    return {
        "timing": {"waiting_time": 30.0},
        "energy": {"max_energy": 100.0},
        "wake_interaction": {"alias": "AngelHeart"},
        "personality": {"ai_self_identity": "全局画像"},
    }


@pytest.fixture
def manager(global_config):
    return ConfigManager(dict(global_config))


@pytest.fixture
def store(tmp_path):
    return ChatProfileStore(str(tmp_path))


def test_unbound_chat_uses_global(manager):
    view = manager.for_chat("chat:g:999")
    assert view.waiting_time == 30.0
    assert view.max_energy == 100.0
    assert view.alias == "AngelHeart"


def test_bound_chat_override_fields(manager, store):
    manager.attach_profile_store(store)
    store.create_template(
        "t",
        config={"timing": {"waiting_time": 5.0}},
    )
    tpl = store.list_templates()[0]
    store.set_binding("chat:g:1", tpl["id"])

    view = manager.for_chat("chat:g:1")
    # 模板字段覆盖
    assert view.waiting_time == 5.0
    # 模板未覆盖的字段回退全局
    assert view.max_energy == 100.0
    assert view.alias == "AngelHeart"
    assert view.ai_self_identity == "全局画像"


def test_partial_override_within_group(manager, store):
    """同一分组内只覆盖部分字段时，其余字段仍用全局值。"""
    manager.attach_profile_store(store)
    store.create_template(
        "t",
        config={"timing": {"waiting_time": 5.0}},
    )
    tpl = store.list_templates()[0]
    store.set_binding("chat:g:1", tpl["id"])

    view = manager.for_chat("chat:g:1")
    assert view.waiting_time == 5.0
    assert view.observation_timeout == 60  # 全局默认值


def test_deleted_template_falls_back_to_global(manager, store):
    manager.attach_profile_store(store)
    tpl = store.create_template("t", config={"timing": {"waiting_time": 5.0}})
    store.set_binding("chat:g:1", tpl["id"])
    store.delete_template(tpl["id"])

    view = manager.for_chat("chat:g:1")
    assert view.waiting_time == 30.0


def test_non_template_fields_unaffected(manager, store):
    """access_control / debug 等非模板字段永远读全局。"""
    manager.attach_profile_store(store)
    store.create_template("t", config={"timing": {"waiting_time": 5.0}})
    tpl = store.list_templates()[0]
    store.set_binding("chat:g:1", tpl["id"])

    view = manager.for_chat("chat:g:1")
    assert view.debug_mode is False
    assert view.whitelist_enabled is False


def test_for_chat_does_not_mutate_global(manager, store):
    manager.attach_profile_store(store)
    store.create_template("t", config={"timing": {"waiting_time": 5.0}})
    tpl = store.list_templates()[0]
    store.set_binding("chat:g:1", tpl["id"])

    view = manager.for_chat("chat:g:1")
    assert view.waiting_time == 5.0
    # 全局实例不受影响
    assert manager.waiting_time == 30.0


def test_template_from_global(manager, store):
    """新建模板可复制全局六类字段。"""
    result = store.template_from_global(manager)
    assert result["timing"]["waiting_time"] == 30.0
    assert result["energy"]["max_energy"] == 100.0
    assert result["personality"]["ai_self_identity"] == "全局画像"


def test_binding_with_plain_group_id_matches_unified_origin(manager, store):
    """白名单群用纯群号绑定，运行时用完整 unified_msg_origin 解析也应命中。"""
    manager.attach_profile_store(store)
    store.create_template("t", config={"timing": {"waiting_time": 5.0}})
    tpl = store.list_templates()[0]
    # 绑定纯群号（白名单来源）
    store.set_binding("830624502", tpl["id"])

    view = manager.for_chat("aiocqhttp:GroupMessage:830624502")
    assert view.waiting_time == 5.0


def test_binding_with_unified_origin_matches_plain_group_id(manager, store):
    """反向：完整 origin 绑定后，用纯群号查询也应命中。"""
    manager.attach_profile_store(store)
    store.create_template("t", config={"timing": {"waiting_time": 5.0}})
    tpl = store.list_templates()[0]
    store.set_binding("aiocqhttp:GroupMessage:830624502", tpl["id"])

    assert store.get_binding("830624502") == tpl["id"]
    view = manager.for_chat("830624502")
    assert view.waiting_time == 5.0


def test_suffix_match_does_not_cross_match_other_group(manager, store):
    """后缀匹配不能误伤：不同完整前缀的群不应命中。"""
    manager.attach_profile_store(store)
    store.create_template("t", config={"timing": {"waiting_time": 5.0}})
    tpl = store.list_templates()[0]
    store.set_binding("aiocqhttp:GroupMessage:10001", tpl["id"])

    view = manager.for_chat("aiocqhttp:GroupMessage:10002")
    assert view.waiting_time == 30.0  # 未绑定，回退全局


def test_for_chat_empty_id_logs_warning_and_falls_back_to_global(manager, caplog, monkeypatch):
    """空 chat_id 属于无 ID 读取异常路径：记 warning 暴露漏传，但只回退全局不抛异常。"""
    import logging

    from core import config_manager as cm

    # 测试桩环境里 astrbot.api.logger 可能是 MagicMock（其他测试注入），
    # 统一替换为标准 logger 才能被 caplog 捕获，钉死告警行为。
    monkeypatch.setattr(cm, "logger", logging.getLogger("test.for_chat_empty_id"))

    with caplog.at_level(logging.WARNING):
        view = manager.for_chat("")
        view2 = manager.for_chat(None)

    # 不抛异常，读取仍可用（回退全局）
    assert view.waiting_time == 30.0
    assert view2.waiting_time == 30.0
    # 两次调用都留下 warning
    assert len(caplog.records) >= 2
    assert all(r.levelno == logging.WARNING for r in caplog.records)
    assert "for_chat 收到空 chat_id" in caplog.text
