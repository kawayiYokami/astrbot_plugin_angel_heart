"""ChatSourcesStore 来源登记存储测试。"""

import json
import os

import pytest

from core.chat_sources import ChatSourcesStore, STORE_FILE_NAME


@pytest.fixture
def store(tmp_path):
    return ChatSourcesStore(str(tmp_path))


def test_record_new_source(store):
    store.record("aiocqhttp:GroupMessage:10001", "绝区零&一条龙开发社群", "group")
    sources = store.list_sources()
    assert len(sources) == 1
    assert sources[0]["chat_id"] == "aiocqhttp:GroupMessage:10001"
    assert sources[0]["display_name"] == "绝区零&一条龙开发社群"
    assert sources[0]["kind"] == "group"
    assert sources[0]["first_seen"] > 0
    assert sources[0]["last_seen"] == sources[0]["first_seen"]


def test_record_private_source(store):
    store.record("aiocqhttp:FriendMessage:289104862", "红豆泥", "private")
    sources = store.list_sources()
    assert len(sources) == 1
    assert sources[0]["kind"] == "private"
    assert sources[0]["display_name"] == "红豆泥"


def test_record_update_keeps_first_seen(store):
    store.record("aiocqhttp:GroupMessage:10001", "旧群名", "group")
    first = store.list_sources()[0]["first_seen"]
    store.record("aiocqhttp:GroupMessage:10001", "新群名", "group")
    sources = store.list_sources()
    assert len(sources) == 1
    assert sources[0]["display_name"] == "新群名"
    assert sources[0]["first_seen"] == first
    assert sources[0]["last_seen"] >= first


def test_record_ignores_empty_chat_id(store):
    store.record("", "无名", "group")
    assert store.list_sources() == []


def test_list_sources_sorted_by_last_seen(store):
    store.record("c", "群C", "group")
    store.record("a", "群A", "group")
    store.record("b", "群B", "group")
    # 再更新 c，使其 last_seen 最新
    store.record("c", "群C", "group")
    ids = [s["chat_id"] for s in store.list_sources()]
    assert ids[0] == "c"


def test_get_source_and_display_name(store):
    store.record("g1", "某群", "group")
    assert store.get_source("g1")["kind"] == "group"
    assert store.get_display_name("g1") == "某群"
    assert store.get_source("missing") is None
    assert store.get_display_name("missing") is None


def test_persistence_reload(tmp_path):
    path = str(tmp_path)
    s1 = ChatSourcesStore(path)
    s1.record("g1", "群1", "group")
    s1.record("p1", "某人", "private")

    s2 = ChatSourcesStore(path)
    assert len(s2.list_sources()) == 2
    assert s2.get_display_name("g1") == "群1"
    assert s2.get_display_name("p1") == "某人"


def test_corrupted_file_falls_back_empty(tmp_path):
    file_path = os.path.join(str(tmp_path), STORE_FILE_NAME)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("{ not valid json")
    store = ChatSourcesStore(str(tmp_path))
    assert store.list_sources() == []


def test_file_format(tmp_path):
    store = ChatSourcesStore(str(tmp_path))
    store.record("g1", "群1", "group")
    data = json.loads(
        open(os.path.join(str(tmp_path), STORE_FILE_NAME), encoding="utf-8").read()
    )
    assert data["version"] == 1
    assert "g1" in data["sources"]
