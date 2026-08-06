"""LastDecisionStore 最近决策存储测试。"""

import json
import os

import pytest

from core.last_decisions import STORE_FILE_NAME, LastDecisionStore


@pytest.fixture
def store(tmp_path):
    return LastDecisionStore(str(tmp_path))


def test_record_and_get(store):
    store.record("default:GroupMessage:10001", True, "话题相关，值得回复")
    entry = store.get("default:GroupMessage:10001")
    assert entry is not None
    assert entry["should_reply"] is True
    assert entry["summary"] == "话题相关，值得回复"
    assert entry["decided_at"] > 0


def test_record_overwrites_last(store):
    store.record("default:GroupMessage:10001", True, "第一次")
    store.record("default:GroupMessage:10001", False, "第二次")
    entry = store.get("default:GroupMessage:10001")
    assert entry["should_reply"] is False
    assert entry["summary"] == "第二次"
    assert len(store.list()) == 1


def test_get_missing_returns_none(store):
    assert store.get("default:GroupMessage:99999") is None


def test_empty_chat_id_ignored(store):
    store.record("", True, "x")
    store.record(None, True, "x")
    assert store.list() == []


def test_persist_reload(tmp_path):
    store = LastDecisionStore(str(tmp_path))
    store.record("default:GroupMessage:10001", False, "继续观察")
    store2 = LastDecisionStore(str(tmp_path))
    entry = store2.get("default:GroupMessage:10001")
    assert entry is not None
    assert entry["should_reply"] is False
    assert entry["summary"] == "继续观察"


def test_corrupted_file_falls_back_empty(tmp_path):
    os.makedirs(str(tmp_path), exist_ok=True)
    with open(os.path.join(str(tmp_path), STORE_FILE_NAME), "w", encoding="utf-8") as f:
        f.write("{not json")
    store = LastDecisionStore(str(tmp_path))
    assert store.list() == []
    store.record("default:GroupMessage:10001", True, "ok")
    assert store.get("default:GroupMessage:10001")["should_reply"] is True
    with open(os.path.join(str(tmp_path), STORE_FILE_NAME), "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["decisions"]["default:GroupMessage:10001"]["should_reply"] is True


def test_array_root_file_falls_back_empty(tmp_path):
    """JSON 内容为合法数组（非 dict 根节点）时不能崩溃，按空存储恢复。"""
    with open(os.path.join(str(tmp_path), STORE_FILE_NAME), "w", encoding="utf-8") as f:
        f.write("[]")
    store = LastDecisionStore(str(tmp_path))
    assert store.list() == []
    store.record("default:GroupMessage:10001", False, "继续观察")
    assert store.get("default:GroupMessage:10001")["should_reply"] is False
