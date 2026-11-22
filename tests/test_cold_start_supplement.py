"""冷启动补种的幂等：同一会话在进程生命周期内只补一次。

补种挂在每次防抖激活路径上，不是只在进程首次启动。靠 `_supplemented_chats`
标记挡住重复拉取；账本是内存态，进程重启后标记跟着清空，正好对应
「重启失忆 → 重新补一次」。
"""

from __future__ import annotations

import pytest

from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

CHAT = "aiocqhttp:GroupMessage:1"


class _Ledger:
    def __init__(self, messages=None):
        self._messages = list(messages or [])
        self.set_calls = 0

    def get_all_messages(self, chat_id):
        return list(self._messages)

    def get_current_summary(self, chat_id):
        return ""

    def set_messages(self, chat_id, messages):
        self._messages = list(messages)
        self.set_calls += 1


class _Context:
    def __init__(self, ledger):
        self.conversation_ledger = ledger


class _Event:
    def get_extra(self, key, default=None):
        return default


def _front(ledger):
    front = object.__new__(FrontDesk)
    front.context = _Context(ledger)
    front._supplemented_chats = set()
    front.fetch_count = 0

    async def fake_fetch(chat_id, needed, event):
        front.fetch_count += 1
        return [{"role": "user", "content": "补进来的历史", "timestamp": 1.0}]

    front._fetch_database_history = fake_fetch
    return front


@pytest.mark.asyncio
async def test_supplement_runs_only_once_per_chat():
    """同一会话连续激活三次，只真正拉一次历史。"""
    ledger = _Ledger([{"role": "user", "content": "当前这条", "timestamp": 2.0}])
    front = _front(ledger)

    await front._ensure_minimum_context(CHAT, _Event())
    await front._ensure_minimum_context(CHAT, _Event())
    await front._ensure_minimum_context(CHAT, _Event())

    assert front.fetch_count == 1
    assert CHAT in front._supplemented_chats
    assert ledger.set_calls == 1


@pytest.mark.asyncio
async def test_supplement_marker_is_per_chat():
    """标记按会话隔离，一个群补过不影响另一个群。"""
    ledger = _Ledger([{"role": "user", "content": "当前这条", "timestamp": 2.0}])
    front = _front(ledger)
    other = "aiocqhttp:GroupMessage:2"

    await front._ensure_minimum_context(CHAT, _Event())
    await front._ensure_minimum_context(other, _Event())

    assert front.fetch_count == 2
    assert {CHAT, other} <= front._supplemented_chats


@pytest.mark.asyncio
async def test_supplement_failure_does_not_mark_chat():
    """拉取失败返回 None 时不写标记，下次激活还能重试。"""
    ledger = _Ledger([{"role": "user", "content": "当前这条", "timestamp": 2.0}])
    front = _front(ledger)
    front.fetch_count = 0

    async def failing_fetch(chat_id, needed, event):
        front.fetch_count += 1
        return None

    front._fetch_database_history = failing_fetch

    await front._ensure_minimum_context(CHAT, _Event())
    assert CHAT not in front._supplemented_chats

    await front._ensure_minimum_context(CHAT, _Event())
    assert front.fetch_count == 2


@pytest.mark.asyncio
async def test_supplement_empty_result_still_marks_chat():
    """确实查过、结果为空，视为该群没有可补历史，写标记避免反复尝试。"""
    ledger = _Ledger([{"role": "user", "content": "当前这条", "timestamp": 2.0}])
    front = _front(ledger)
    front.fetch_count = 0

    async def empty_fetch(chat_id, needed, event):
        front.fetch_count += 1
        return []

    front._fetch_database_history = empty_fetch

    await front._ensure_minimum_context(CHAT, _Event())
    await front._ensure_minimum_context(CHAT, _Event())

    assert front.fetch_count == 1
    assert CHAT in front._supplemented_chats


@pytest.mark.asyncio
async def test_supplement_skips_when_current_summary_present():
    """已有摘要说明上下文已经建立过，不补种也不打标记。"""
    ledger = _Ledger([{"role": "user", "content": "当前这条", "timestamp": 2.0}])
    ledger.get_current_summary = lambda chat_id: "已有摘要正文"
    front = _front(ledger)

    await front._ensure_minimum_context(CHAT, _Event())

    assert front.fetch_count == 0
    assert CHAT not in front._supplemented_chats
