"""出请求前的工具链收口测试。

上游硬约束：每个 function_call 恰好一条 function_call_output。
这条约束一旦破，整次请求直接 400，不是少一段历史那么简单。
"""

from __future__ import annotations

from astrbot_plugin_angel_heart.core.message_processor import (
    TOOL_RESULT_PLACEHOLDER,
    ensure_tool_call_pairing,
)


def _assistant_call(call_id: str, text: str | None = None) -> dict:
    message = {
        "role": "assistant",
        "content": [{"type": "text", "text": text}] if text else [],
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "x"}}],
    }
    return message


def _tool_result(call_id: str, content: str = "结果") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _pairing_by_call(contexts: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in contexts:
        if item.get("role") != "tool":
            continue
        call_id = item.get("tool_call_id")
        counts[call_id] = counts.get(call_id, 0) + 1
    return counts


def test_clean_chain_passes_through_unchanged():
    contexts = [
        {"role": "user", "content": "问个问题"},
        _assistant_call("call_1"),
        _tool_result("call_1", "工具结果"),
        {"role": "assistant", "content": [{"type": "text", "text": "最终回答"}]},
    ]

    result = ensure_tool_call_pairing(contexts)

    assert result == contexts


def test_duplicate_tool_output_is_dropped():
    """同一次调用出现两条回执，就是上游报 Duplicate 的那一版。"""
    contexts = [
        {"role": "user", "content": "问个问题"},
        _assistant_call("call_1"),
        _tool_result("call_1", "第一次结果"),
        _tool_result("call_1", "重复结果"),
        {"role": "assistant", "content": [{"type": "text", "text": "最终回答"}]},
    ]

    result = ensure_tool_call_pairing(contexts)

    assert _pairing_by_call(result) == {"call_1": 1}
    assert [item for item in result if item.get("role") == "tool"][0]["content"] == "第一次结果"


def test_missing_tool_output_is_filled_with_placeholder():
    """只有调用没有回执时补 no reply，保证闭合链完整。"""
    contexts = [
        {"role": "user", "content": "问个问题"},
        _assistant_call("call_1"),
        {"role": "assistant", "content": [{"type": "text", "text": "最终回答"}]},
    ]

    result = ensure_tool_call_pairing(contexts)

    assert _pairing_by_call(result) == {"call_1": 1}
    filled = [item for item in result if item.get("role") == "tool"]
    assert filled[0]["content"] == TOOL_RESULT_PLACEHOLDER
    # 补出来的回执紧跟在调用之后，不打乱原有次序
    assert [item["role"] for item in result] == ["user", "assistant", "tool", "assistant"]


def test_trailing_missing_tool_output_is_filled():
    """调用在末尾、回执还没来时同样要补。"""
    contexts = [_assistant_call("call_1")]

    result = ensure_tool_call_pairing(contexts)

    assert [item["role"] for item in result] == ["assistant", "tool"]
    assert result[1]["tool_call_id"] == "call_1"


def test_orphan_tool_output_is_dropped():
    """找不到对应调用的回执不能留在请求里。"""
    contexts = [
        {"role": "user", "content": "问个问题"},
        _tool_result("call_ghost"),
        {"role": "assistant", "content": [{"type": "text", "text": "回答"}]},
    ]

    result = ensure_tool_call_pairing(contexts)

    assert _pairing_by_call(result) == {}
    assert [item["role"] for item in result] == ["user", "assistant"]


def test_duplicate_call_id_in_two_assistant_messages_keeps_first_only():
    """同一次调用被记了两遍时只认第一条，否则回执数目必然对不上。"""
    contexts = [
        _assistant_call("call_1", "先说一句"),
        _assistant_call("call_1", "又说一遍"),
        _tool_result("call_1"),
    ]

    result = ensure_tool_call_pairing(contexts)

    assistant_calls = [item for item in result if item.get("tool_calls")]
    assert len(assistant_calls) == 1
    assert assistant_calls[0]["content"] == [{"type": "text", "text": "先说一句"}]
    assert _pairing_by_call(result) == {"call_1": 1}


def test_pure_duplicate_assistant_without_content_is_dropped():
    """重复调用且没有任何文本时整条丢弃，避免下发空 assistant 消息。"""
    contexts = [
        _assistant_call("call_1"),
        _assistant_call("call_1"),
        _tool_result("call_1"),
    ]

    result = ensure_tool_call_pairing(contexts)

    assert [item["role"] for item in result] == ["assistant", "tool"]


def test_multiple_calls_in_one_message_keep_order():
    """一条 assistant 里多个调用，回执顺序按原样保留。"""
    contexts = [
        {
            "role": "assistant",
            "content": [],
            "tool_calls": [{"id": "call_a"}, {"id": "call_b"}],
        },
        _tool_result("call_b", "b 的结果"),
        _tool_result("call_a", "a 的结果"),
    ]

    result = ensure_tool_call_pairing(contexts)

    assert _pairing_by_call(result) == {"call_a": 1, "call_b": 1}
    assert [item.get("tool_call_id") for item in result if item.get("role") == "tool"] == [
        "call_b",
        "call_a",
    ]
