"""DebounceManager.patrol_snapshot 巡检快照测试。"""

import asyncio
import time

import pytest

from core.config_manager import ConfigManager
from core.debounce_manager import DebounceManager


def make_manager():
    return DebounceManager(ConfigManager({}))


def make_record(kind, chat_id, delay, sender_id="u1"):
    """构造一个带 future 的防抖记录并塞入账本。"""
    from core.debounce_manager import DebounceRecord

    record = DebounceRecord(
        kind=kind,
        chat_id=chat_id,
        sender_id=sender_id,
        event=object(),
        future=asyncio.get_event_loop().create_future(),
        version=1,
        must_reply=False,
        start_message_id="s",
        end_message_id="e",
        delay=delay,
    )
    return record


@pytest.mark.asyncio
async def test_snapshot_idle():
    dm = make_manager()
    snap = await dm.patrol_snapshot("chat:g:1")
    assert snap == {"waiting": "", "remaining": 0.0, "total": 0.0}


@pytest.mark.asyncio
async def test_snapshot_secretary_priority():
    dm = make_manager()
    dm._secretary["chat:g:1"] = make_record("secretary", "chat:g:1", 30.0)
    # 同时有点名防抖，秘书应优先
    dm._assistant[("chat:g:1", "u1")] = make_record("assistant", "chat:g:1", 1.0)
    snap = await dm.patrol_snapshot("chat:g:1")
    assert snap["waiting"] == "secretary"
    assert snap["total"] == 30.0
    assert 0 < snap["remaining"] <= 30.0


@pytest.mark.asyncio
async def test_snapshot_assistant():
    dm = make_manager()
    dm._assistant[("chat:g:1", "u1")] = make_record("assistant", "chat:g:1", 1.0)
    snap = await dm.patrol_snapshot("chat:g:1")
    assert snap["waiting"] == "assistant"
    assert snap["total"] == 1.0


@pytest.mark.asyncio
async def test_snapshot_assistant_picks_earliest_expiry():
    """多条助理防抖并存时，应选 created_at + delay 最早到期的那条，
    而不是最后插入的。"""
    dm = make_manager()
    # 先插一条长等待（created_at 更早，但到期晚）
    dm._assistant[("chat:g:1", "u1")] = make_record(
        "assistant", "chat:g:1", 100.0, sender_id="u1"
    )
    # 后插一条短等待（created_at 更晚，但到期更早）
    short = make_record("assistant", "chat:g:1", 0.5, sender_id="u2")
    dm._assistant[("chat:g:1", "u2")] = short
    snap = await dm.patrol_snapshot("chat:g:1")
    assert snap["waiting"] == "assistant"
    assert snap["total"] == 0.5
    assert 0 < snap["remaining"] <= 0.5


@pytest.mark.asyncio
async def test_snapshot_rest():
    dm = make_manager()
    dm._assistant_rest_until["chat:g:1"] = time.time() + 20.0
    snap = await dm.patrol_snapshot("chat:g:1")
    assert snap["waiting"] == "rest"
    assert snap["total"] == snap["remaining"]
    assert 0 < snap["remaining"] <= 20.0


@pytest.mark.asyncio
async def test_snapshot_rest_expired_returns_idle():
    dm = make_manager()
    dm._assistant_rest_until["chat:g:1"] = time.time() - 5.0
    snap = await dm.patrol_snapshot("chat:g:1")
    assert snap["waiting"] == ""
