import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parent))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astrbot_plugin_angel_heart.core.message_processor import MessageProcessor


def test_user_message_does_not_append_duplicate_time_text_block():
    processor = MessageProcessor("fairy")
    msg = {
        "role": "user",
        "content": "fairy？有没有觉得提示词哪里不对？我继续修",
        "sender_name": "红豆泥",
        "sender_id": "289104862",
        "timestamp": 1784008800,
        "chat_id": "aiocqhttp:GroupMessage:10000",
    }

    processed = processor.process_message(msg)

    assert processed["role"] == "user"
    assert isinstance(processed["content"], list)
    assert len(processed["content"]) == 1
    assert processed["content"][0]["type"] == "text"
    text = processed["content"][0]["text"]
    assert "[群友: 红豆泥 (ID: 289104862)]" in text
    assert "fairy？有没有觉得提示词哪里不对？我继续修" in text
    assert text.count("2026-07-14 14:00") == 1
