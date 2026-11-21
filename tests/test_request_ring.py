"""最近十次请求的快照测试。

队列是全局的：只留最近 10 次，秘书与助理各占一条。消息存原文，
唯一被替换的是图片块里的内联二进制——那张图可能有几兆。
"""

from __future__ import annotations

from astrbot_plugin_angel_heart.core.request_ring import (
    RECENT_LIMIT,
    RequestRing,
    inspect_tool_pairing,
)


def _call(call_id: str, name: str = "weather", args: str = "{}") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}
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
# 存原文
# ---------------------------------------------------------------------------


def test_record_keeps_full_text():
    long_text = "红豆" * 500
    ring = RequestRing(clock=_Clock())
    snapshot = ring.record(
        "chat", source="assistant", messages=[{"role": "user", "content": long_text}]
    )
    assert snapshot["messages"][0]["content"] == long_text
    assert snapshot["total_chars"] == 1000


def test_record_does_not_touch_source_messages():
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": "看图"}],
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
    }
    ring = RequestRing(clock=_Clock())
    ring.record("chat", source="assistant", messages=[message])
    # 快照里的副本被改动，原对象必须保持干净
    snapshot = ring.recent()[0]
    snapshot["messages"][0]["content"].append({"type": "text", "text": "污染"})
    assert message["content"] == [{"type": "text", "text": "看图"}]


def test_inline_image_binary_replaced():
    url = "data:image/jpeg;base64," + "A" * 20000
    ring = RequestRing(clock=_Clock())
    snapshot = ring.record(
        "chat",
        source="assistant",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ],
    )
    image_part = snapshot["messages"][0]["content"][1]
    assert image_part["image_url"]["url"].startswith("[内联图片 ")
    assert image_part["image_url"]["url"].endswith("KB]")
    # 文本块原样保留
    assert snapshot["messages"][0]["content"][0] == {"type": "text", "text": "看图"}


def test_http_image_url_kept():
    ring = RequestRing(clock=_Clock())
    snapshot = ring.record(
        "chat",
        source="assistant",
        messages=[
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "https://x/y.png"}}],
            }
        ],
    )
    assert snapshot["messages"][0]["content"][0]["image_url"]["url"] == "https://x/y.png"


# ---------------------------------------------------------------------------
# 全局环形队列
# ---------------------------------------------------------------------------


def test_global_limit_across_chats():
    ring = RequestRing(clock=_Clock())
    for index in range(RECENT_LIMIT + 2):
        ring.record(
            f"chat{index}", source="assistant", messages=[{"role": "user", "content": str(index)}]
        )
    items = ring.recent()
    assert len(items) == RECENT_LIMIT
    # 最早的两条被挤出队列
    assert items[-1]["messages"][0]["content"] == "2"
    assert items[0]["messages"][0]["content"] == str(RECENT_LIMIT + 1)


def test_recent_filters_by_chat():
    ring = RequestRing(clock=_Clock())
    ring.record("a", source="secretary", messages=[{"role": "user", "content": "a1"}])
    ring.record("b", source="assistant", messages=[{"role": "user", "content": "b1"}])
    ring.record("a", source="assistant", messages=[{"role": "user", "content": "a2"}])
    assert [item["messages"][0]["content"] for item in ring.recent("a")] == ["a2", "a1"]
    assert len(ring.recent("b")) == 1
    assert len(ring.recent()) == 3


def test_recent_respects_limit():
    ring = RequestRing(clock=_Clock())
    for index in range(4):
        ring.record("chat", source="secretary", messages=[{"role": "user", "content": str(index)}])
    assert len(ring.recent(None, 2)) == 2


def test_recent_is_time_desc():
    ring = RequestRing(clock=_Clock())
    for index in range(3):
        ring.record("chat", source="assistant", messages=[{"role": "user", "content": str(index)}])
    assert [item["messages"][0]["content"] for item in ring.recent()] == ["2", "1", "0"]


def test_chats_counts_per_session():
    ring = RequestRing(clock=_Clock())
    ring.record("a", source="secretary", messages=[])
    ring.record("a", source="assistant", messages=[])
    ring.record("b", source="assistant", messages=[])
    assert {item["chat_id"]: item["count"] for item in ring.chats()} == {"a": 2, "b": 1}


def test_recent_returns_copy():
    ring = RequestRing(clock=_Clock())
    ring.record("chat", source="assistant", messages=[{"role": "user", "content": "x"}])
    items = ring.recent()
    items[0]["messages"][0]["content"] = "改坏了"
    assert ring.recent()[0]["messages"][0]["content"] == "x"


def test_clear():
    ring = RequestRing(clock=_Clock())
    ring.record("a", source="assistant", messages=[])
    ring.clear()
    assert ring.recent() == []
    assert ring.chats() == []


def test_ignores_non_dict_messages():
    ring = RequestRing(clock=_Clock())
    snapshot = ring.record(
        "chat", source="assistant", messages=[{"role": "user", "content": "x"}, "bad"]
    )
    assert snapshot["message_count"] == 1


# ---------------------------------------------------------------------------
# 快照形状与配对检查
# ---------------------------------------------------------------------------


def test_snapshot_shape():
    ring = RequestRing(clock=_Clock())
    snapshot = ring.record(
        "chat",
        source="assistant",
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "人设"},
            _call("c1"),
            _result("c1"),
        ],
    )
    assert snapshot["chat_id"] == "chat"
    assert snapshot["source"] == "assistant"
    assert snapshot["model"] == "gpt-4o"
    assert snapshot["message_count"] == 3
    assert snapshot["role_sequence"] == ["system", "assistant", "tool"]
    assert snapshot["tool_calls"] == 1
    assert snapshot["tool_results"] == 1
    assert snapshot["pairing"]["ok"] is True
    assert snapshot["time"]


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


def test_seq_increases_across_chats():
    ring = RequestRing(clock=_Clock())
    first = ring.record("a", source="secretary", messages=[])
    second = ring.record("b", source="assistant", messages=[])
    assert second["seq"] == first["seq"] + 1
