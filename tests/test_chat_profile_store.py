"""ChatProfileStore 单元测试：模板 CRUD、绑定、持久化、字段白名单。"""

import os

import pytest

from core.chat_profile import ChatProfileStore, filter_template_config


@pytest.fixture
def store(tmp_path):
    return ChatProfileStore(str(tmp_path))


def test_create_and_get_template(store):
    tpl = store.create_template("游戏群", "打游戏专用", {"timing": {"waiting_time": 5.0}})
    assert tpl["id"].startswith("tpl_")
    assert tpl["name"] == "游戏群"
    got = store.get_template(tpl["id"])
    assert got["config"]["timing"]["waiting_time"] == 5.0
    # 未设置字段不应出现在 config 中
    assert "energy" not in got["config"]


def test_filter_unknown_keys(store):
    tpl = store.create_template(
        "t",
        config={
            "timing": {"waiting_time": 1.0, "hack_key": 999},
            "unknown_group": {"x": 1},
            "energy": {"max_energy": 50.0},
        },
    )
    config = tpl["config"]
    assert "timing" in config
    assert "hack_key" not in config["timing"]
    assert "unknown_group" not in config
    assert config["energy"]["max_energy"] == 50.0


def test_update_template(store):
    tpl = store.create_template("t")
    updated = store.update_template(tpl["id"], {"name": "改名", "config": {"timing": {"waiting_time": 9.0}}})
    assert updated["name"] == "改名"
    assert store.get_template(tpl["id"])["config"]["timing"]["waiting_time"] == 9.0
    # 更新不存在模板
    assert store.update_template("tpl_nope", {"name": "x"}) is None


def test_delete_template_cascades_binding(store):
    tpl = store.create_template("t")
    assert store.set_binding("chat:g:123", tpl["id"]) is True
    assert store.get_binding("chat:g:123") == tpl["id"]
    assert store.delete_template(tpl["id"]) is True
    # 绑定级联解除
    assert store.get_binding("chat:g:123") == ""
    # 重复删除失败
    assert store.delete_template(tpl["id"]) is False


def test_binding_validation(store):
    # 绑定不存在的模板失败
    assert store.set_binding("chat:g:1", "tpl_nope") is False
    # 空 chat_id 失败
    assert store.set_binding("", "tpl_nope") is False
    # 解绑
    tpl = store.create_template("t")
    store.set_binding("chat:g:1", tpl["id"])
    assert store.set_binding("chat:g:1", "") is True
    assert store.get_binding("chat:g:1") == ""


def test_persistence_reload(tmp_path):
    store = ChatProfileStore(str(tmp_path))
    tpl = store.create_template("持久化", "测试", {"wake_interaction": {"alias": "小天使"}})
    store.set_binding("chat:g:42", tpl["id"])

    # 重新加载
    store2 = ChatProfileStore(str(tmp_path))
    assert store2.get_template(tpl["id"])["name"] == "持久化"
    assert store2.get_template(tpl["id"])["config"]["wake_interaction"]["alias"] == "小天使"
    assert store2.get_binding("chat:g:42") == tpl["id"]


def test_resolve_override(store):
    tpl = store.create_template("t", config={"timing": {"waiting_time": 3.0}})
    # 未绑定 -> None
    assert store.resolve_override("chat:g:1") is None
    store.set_binding("chat:g:1", tpl["id"])
    override = store.resolve_override("chat:g:1")
    assert override["timing"]["waiting_time"] == 3.0


def test_list_templates_sorted(store):
    store.create_template("b", description="")
    store.create_template("a", description="")
    names = [t["name"] for t in store.list_templates()]
    assert names == ["b", "a"]  # 按创建时间排序


def test_filter_template_config_non_dict():
    assert filter_template_config(None) == {}
    assert filter_template_config("oops") == {}
    assert filter_template_config({"timing": "not-a-dict"}) == {}


def test_corrupt_file_recovers(tmp_path):
    target = tmp_path / "chat_profiles.json"
    target.write_text("{not valid json", encoding="utf-8")
    store = ChatProfileStore(str(tmp_path))
    assert store.list_templates() == []
    # 保存后文件可正常写入
    store.create_template("恢复")
    assert os.path.isfile(tmp_path / "chat_profiles.json")
