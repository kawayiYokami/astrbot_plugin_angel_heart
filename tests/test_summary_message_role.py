"""摘要消息角色约束测试。

上下文整理产出的摘要消息永远是用户消息：账本内、下游请求体、状态扫描口径一致，
不允许出现 system 角色。
"""

from astrbot_plugin_angel_heart.core.angel_heart_status import _is_summary_message
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger
from astrbot_plugin_angel_heart.core.message_processor import MessageProcessor


def test_ledger_summary_message_is_user_role():
    ledger = object.__new__(ConversationLedger)
    msg = ledger._make_summary_message("红豆: 讲了个笑话", 1784008800.0)

    assert msg["role"] == "user"
    assert msg["kind"] == "context_summary"
    assert msg["content"].startswith("[当前摘要]")


def test_summary_message_processed_as_user_role():
    processor = MessageProcessor("fairy")
    msg = {
        "role": "user",
        "content": "[当前摘要]\n红豆: 讲了个笑话",
        "sender_id": "system",
        "sender_name": "context_summary",
        "kind": "context_summary",
        "timestamp": 1784008800,
    }
    processed = processor.process_message(msg)

    assert processed["role"] == "user"
    text = processed["content"][0]["text"]
    assert "红豆: 讲了个笑话" in text


def test_all_summary_kinds_recognized():
    for kind in ("context_summary", "summary_context", "context_compaction"):
        assert _is_summary_message({"role": "user", "kind": kind}) is True
    assert _is_summary_message({"role": "user", "sender_name": "红豆"}) is False
