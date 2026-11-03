"""最近若干次请求的摘要快照测试。

排查请求体问题（call_id 重复、回执缺失、角色序列错位）靠的是摘要，
不是原文；这里盯四件事：摘要化不泄露正文、环形上限、会话隔离、配对检查。
"""

from __future__ import annotations

from astrbot_plugin_angel_heart.core.request_ring import (
    PREVIEW_CHARS,
    RequestRing,
    inspect_tool_pairing,
    summarize_message,
)


def _call(call_id: str, name: str = "weather") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name}}
        ],
    }


def _result(call_id: str, content: str = "晴") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


class _Clock:
    """可控时钟：每次取值前进 1 秒，便于断言倒序。"""

    def __init__(self, start: float = 1_700_000_000.0):
        self._now = start

    def __call__(self) -> float:
        self._now += 1.0
        return self._now


# ---------------------------------------------------------------------------
# 摘要化
# ---------------------------------------------------------------------------


def test_summarize_keeps_only_digest():
    message = {"role": "user", "content": "红豆" * 100}
    digest = summarize_message(message)
    assert digest["role"] == "user"
    assert digest["chars"] == 200
    assert digest["preview"].startswith("红豆红豆")
    assert digest["preview"].endswith("…")
    assert len(digest["preview"]) == PREVIEW_CHARS + 1
    # 预览之外不留原文
    assert "content" not in digest


def test_summarize_short_text_has_no_ellipsis():
    digest = summarize_message({"role": "user", "content": "在吗"})
    assert digest["preview"] == "在吗"
    assert digest["chars"] == 2


def test_summarize_multimodal_marks_image_parts():
    digest = summarize_message(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "http://x/y.png"}},
            ],
        }
    )
    assert digest["preview"] == "看图[图片]"
    assert digest["chars"] == len("看图[图片]")


def test_summarize_collects_tool_call_ids():
    digest = summarize_message(_call("call_a", name="send_message_to_user"))
    assert digest["tool_calls"] == [{"id": "call_a", "name": "send_message_to_user"}]
    assert digest["chars"] == 0


def test_summarize_tool_result_keeps_call_id():
    digest = summarize_message(_result("call_b"))
    assert digest["tool_call_id"] == "call_b"
    assert digest["tool_calls"] == []


# ---------------------------------------------------------------------------
# 配对检查
# ---------------------------------------------------------------------------


def test_pairing_ok_for_matched_chain():
    report = inspect_tool_pairing([_call("c1"), _result("c1")])
    assert report["ok"] is True
    assert report["missing"] == []


def test_pairing_reports_missing_result():
    report = inspect_tool_pairing([_call("c1"), _call("c2"), _result("c1")])
    assert report["ok"] is False
    assert report["missing"] == ["c2"]


def test_pairing_reports_duplicate_result():
    report = inspect_tool_pairing([_call("c1"), _result("c1"), _result("c1")])
    assert report["ok"] is False
    assert report["duplicate_result"] == ["c1"]


def test_pairing_reports_orphan_result():
    report = inspect_tool_pairing([_result("c9")])
    assert report["ok"] is False
    assert report["orphan"] == ["c9"]


# ---------------------------------------------------------------------------
# 环形队列
# ---------------------------------------------------------------------------


def test_record_builds_snapshot_shape():
    ring = RequestRing(clock=_Clock())
    snapshot = ring.record(
        "chat",
        source="assistant",
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "人设"},
            {"role": "user", "content": "你好"},
        ],
    )
    assert snapshot["chat_id"] == "chat"
    assert snapshot["source"] == "assistant"
    assert snapshot["model"] == "gpt-4o"
    assert snapshot["message_count"] == 2
    assert snapshot["total_chars"] == len("人设") + len("你好")
    assert snapshot["role_sequence"] == ["system", "user"]
    assert snapshot["pairing"]["ok"] is True
    assert snapshot["time"]


def test_ring_keeps_latest_per_chat():
    ring = RequestRing(per_chat_limit=3, clock=_Clock())
    for index in range(5):
        ring.record("chat", source="assistant", messages=[{"role": "user", "content": str(index)}])
    items = ring.recent("chat")
    assert len(items) == 3
    # 时间倒序：最后一条在最前
    assert [item["messages"][0]["preview"] for item in items] == ["4", "3", "2"]


def test_recent_respects_limit():
    ring = RequestRing(clock=_Clock())
    for index in range(4):
        ring.record("chat", source="secretary", messages=[{"role": "user", "content": str(index)}])
    assert len(ring.recent("chat", 2)) == 2


def test_chats_are_isolated():
    ring = RequestRing(clock=_Clock())
    ring.record("a", source="secretary", messages=[{"role": "user", "content": "a"}])
    ring.record("b", source="assistant", messages=[{"role": "user", "content": "b"}])
    assert len(ring.recent("a")) == 1
    assert ring.recent("a")[0]["messages"][0]["preview"] == "a"
    assert ring.recent("b")[0]["messages"][0]["preview"] == "b"
    assert {item["chat_id"] for item in ring.chats()} == {"a", "b"}


def test_evicts_least_recent_chat():
    ring = RequestRing(max_chats=2, clock=_Clock())
    ring.record("a", source="assistant", messages=[])
    ring.record("b", source="assistant", messages=[])
    ring.record("c", source="assistant", messages=[])
    assert [item["chat_id"] for item in ring.chats()] == ["c", "b"]
    assert ring.recent("a") == []


def test_seq_increases_across_chats():
    ring = RequestRing(clock=_Clock())
    first = ring.record("a", source="secretary", messages=[])
    second = ring.record("b", source="assistant", messages=[])
    assert second["seq"] == first["seq"] + 1


def test_recent_returns_copy():
    ring = RequestRing(clock=_Clock())
    ring.record("chat", source="assistant", messages=[{"role": "user", "content": "x"}])
    items = ring.recent("chat")
    items[0]["messages"][0]["preview"] = "改坏了"
    assert ring.recent("chat")[0]["messages"][0]["preview"] == "x"


def test_clear_single_and_all():
    ring = RequestRing(clock=_Clock())
    ring.record("a", source="assistant", messages=[])
    ring.record("b", source="assistant", messages=[])
    ring.clear("a")
    assert ring.recent("a") == []
    assert len(ring.recent("b")) == 1
    ring.clear()
    assert ring.chats() == []


def test_ignores_non_dict_messages():
    ring = RequestRing(clock=_Clock())
    snapshot = ring.record("chat", source="assistant", messages=[{"role": "user", "content": "x"}, "bad"])
    assert snapshot["message_count"] == 1
