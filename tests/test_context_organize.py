"""消息管理与上下文压缩边界测试。

账本是滑动窗口：只装最近一段正文，工具一律不进窗口，滑出的直接丢弃。
摘要 = 去掉工具之后的正文节选，不是模型压出来的文本，也不生成摘要消息。
"""

from __future__ import annotations

import time

import pytest

from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger


class DummyConfig(ConfigManager):
    def __init__(self):
        super().__init__(
            {
                "context_compression": {
                    "max_conversation_tokens": 200,
                    "context_compression_threshold": 0.5,
                    "content_retain_tokens": 4000,
                    "forgetting_timeout": 0,
                },
                "timing": {},
                "wake_interaction": {},
                "leave_reply": {},
                "access_control": {},
                "output_rewrite": {},
                "personality": {},
            }
        )


@pytest.fixture
def ledger(tmp_path):
    return ConversationLedger(DummyConfig(), tmp_path, astr_context=None)


def _msg(i, text, *, role="user", tool=False, chat_id="GroupMessage:1"):
    m = {
        "role": "tool" if tool else role,
        "content": text,
        "sender_id": f"u{i}",
        "sender_name": f"user{i}",
        "timestamp": float(i),
        "chat_id": chat_id,
        "source_message_id": f"src-{i}",
    }
    if tool:
        m["role"] = "tool"
        m["tool_call_id"] = f"t{i}"
    return m


def _no_tools(messages):
    for m in messages:
        assert m.get("role") != "tool"
        assert not m.get("tool_calls")
        assert m.get("sender_name") != "tool_result"


class TestSlidingWindow:
    """滑动窗口：保留最近正文，工具不进，滑出即丢。"""

    def test_keeps_recent_messages_with_ids(self, ledger):
        """窗口内是原消息，ID、发送者、时间都还在。"""
        chat_id = "GroupMessage:ids"
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        for i in range(1, 8):
            ledger.add_message(chat_id, _msg(i, f"old{i}", chat_id=chat_id))

        assert ledger.organize_on_group_enter(chat_id, keep_from_timestamp=6.0)

        msgs = ledger.get_all_messages(chat_id)
        assert msgs
        for m in msgs:
            assert m["source_message_id"].startswith("src-")
            assert m["sender_id"].startswith("u")
            assert m["sender_name"].startswith("user")
            assert m["timestamp"] > 0
        # 只剩触发点之后的
        assert [m["source_message_id"] for m in msgs] == ["src-6", "src-7"]

    def test_tools_never_enter_window(self, ledger):
        """三种工具消息都不进窗口：回执、带 tool_calls 的 assistant、tool_result。"""
        chat_id = "GroupMessage:notools"
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        base = time.time()
        ledger.add_message(chat_id, _msg(1, "人说的话", chat_id=chat_id))
        ledger.add_message(chat_id, _msg(2, "tool out", tool=True, chat_id=chat_id))
        ledger.add_message(
            chat_id,
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "name": "x"}],
                "sender_id": "bot",
                "sender_name": "bot",
                "timestamp": 3.0,
                "chat_id": chat_id,
                "source_message_id": "src-3",
            },
        )
        ledger.add_message(
            chat_id,
            {
                "role": "user",
                "content": "工具直发的结果",
                "sender_id": "tool",
                "sender_name": "tool_result",
                "timestamp": 4.0,
                "chat_id": chat_id,
                "source_message_id": "src-4",
            },
        )
        ledger.add_message(chat_id, _msg(5, "后面的人话", chat_id=chat_id))
        ledger._ledgers[chat_id]["messages"][-1]["timestamp"] = base + 100

        assert ledger.organize_on_group_enter(chat_id, keep_from_timestamp=base + 50)

        msgs = ledger.get_all_messages(chat_id)
        assert msgs
        _no_tools(msgs)
        assert [m["source_message_id"] for m in msgs] == ["src-5"]

    def test_drops_oldest_when_over_budget(self, ledger):
        """超出预算时从最旧整条丢，留下的每条都是完整消息。"""
        chat_id = "GroupMessage:trim"
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        base = time.time()
        for i in range(1, 41):
            msg = _msg(i, f"m{i:02d}" + "x" * 300, chat_id=chat_id)
            msg["timestamp"] = base + i
            ledger.add_message(chat_id, msg)

        # 触发点在最早一条之前，所以裁剪只由预算决定
        assert ledger.organize_on_group_enter(chat_id, keep_from_timestamp=base)

        msgs = ledger.get_all_messages(chat_id)
        assert msgs
        budget = ledger.config_manager.context_content_retain_tokens
        assert sum(ledger._count_message_tokens(m) for m in msgs) <= budget
        # 最旧的整条被丢，最新的完整保留
        assert msgs[0]["source_message_id"] != "src-1"
        assert msgs[-1]["source_message_id"] == "src-40"
        # 每条都是完整消息，没有从中间切断
        assert all(len(m["content"]) == 303 for m in msgs)

    def test_repeated_organize_does_not_grow(self, ledger):
        """连续整理不会把上轮结果再算一遍，窗口只由最近消息决定。"""
        chat_id = "GroupMessage:stable"
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        base = time.time()
        counts = []

        for round_no in range(5):
            round_start = base + round_no * 10
            for k in range(3):
                msg = _msg(round_no * 10 + k, f"m{round_no}{k}", chat_id=chat_id)
                msg["timestamp"] = round_start + k
                ledger.add_message(chat_id, msg)

            assert ledger.organize_on_group_enter(
                chat_id, keep_from_timestamp=round_start + 2
            )

            msgs = ledger.get_all_messages(chat_id)
            ids = [m["source_message_id"] for m in msgs]
            assert len(ids) == len(set(ids))
            counts.append(len(msgs))

        # 每轮窗口只装触发点之后的消息，不会累加
        assert counts == [1, 1, 1, 1, 1]


class TestCompressSnapshot:
    """群聊压缩器：整批进，去掉工具，按预算留尾部一组消息。"""

    def test_keeps_everything_when_within_budget(self, ledger):
        """预算装得下全部：一条不少。"""
        msgs = [_msg(i, f"m{i}", chat_id="c") for i in range(1, 4)]
        retained = ledger._compress_snapshot(msgs, 100000)

        assert len(retained) == 3

    def test_drops_oldest_and_keeps_messages_whole(self, ledger):
        """超预算从最旧整条丢，留下的每条都是完整消息。"""
        msgs = [_msg(i, f"m{i:02d}" + "x" * 200, chat_id="c") for i in range(1, 11)]
        retained = ledger._compress_snapshot(msgs, 200)

        assert retained
        ids = [m["source_message_id"] for m in retained]
        # 保留的是尾部，最旧的那条被丢
        assert ids[-1] == "src-10"
        assert "src-1" not in ids
        # 每条都是完整消息，没有从中间切断
        assert all(len(m["content"]) == 203 for m in retained)

    def test_tool_blocks_never_retained(self, ledger):
        """assistant 调用 + 工具回执整块跳过，预算再大也不进保留区。"""
        msgs = [
            _msg(1, "m1", chat_id="c"),
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "name": "x"}],
                "sender_id": "bot",
                "sender_name": "bot",
                "timestamp": 2.0,
                "chat_id": "c",
                "source_message_id": "src-2",
            },
            _msg(3, "tool out", tool=True, chat_id="c"),
            _msg(4, "m4", chat_id="c"),
            _msg(5, "m5", chat_id="c"),
        ]
        retained = ledger._compress_snapshot(msgs, 100000)

        ids = {m["source_message_id"] for m in retained}
        assert "src-2" not in ids
        assert "src-3" not in ids
        assert {"src-1", "src-4", "src-5"} <= ids

    def test_keep_from_timestamp_filters_blocks(self, ledger):
        """触发点之前的不进结果。"""
        msgs = [_msg(i, f"m{i}", chat_id="c") for i in range(1, 8)]
        retained = ledger._compress_snapshot(
            msgs, 100000, keep_from_timestamp=5.0
        )

        assert [m["source_message_id"] for m in retained] == [
            "src-5",
            "src-6",
            "src-7",
        ]

    def test_minimum_retain_count_fallback(self, ledger):
        """预算极小时保底留下最近 MIN_RETAIN_COUNT 条。"""
        msgs = [_msg(i, f"m{i}" + "x" * 100, chat_id="c") for i in range(1, 15)]
        retained = ledger._compress_snapshot(msgs, 0)

        assert len(retained) == ledger.MIN_RETAIN_COUNT
        assert retained[-1]["source_message_id"] == "src-14"


class TestSplitByBudget:
    """判断哪些消息拿去压缩：左侧待压缩，右侧留在账本。"""

    def test_splits_left_and_right(self, ledger):
        msgs = [_msg(i, f"m{i:02d}" + "x" * 200, chat_id="c") for i in range(1, 11)]
        left, right = ledger._split_by_budget(msgs, 200)

        assert left
        assert right
        # 右侧是尾部，左侧是更早那批，两边不重叠且合起来就是原集合
        assert right[-1]["source_message_id"] == "src-10"
        left_ids = {m["source_message_id"] for m in left}
        right_ids = {m["source_message_id"] for m in right}
        assert not (left_ids & right_ids)
        assert left_ids | right_ids == {f"src-{i}" for i in range(1, 11)}

    def test_budget_holds_everything_gives_empty_left(self, ledger):
        msgs = [_msg(i, f"m{i}", chat_id="c") for i in range(1, 4)]
        left, right = ledger._split_by_budget(msgs, 100000)

        assert left == []
        assert len(right) == 3

    def test_does_not_touch_ledger(self, ledger):
        """纯函数：调用前后账本原样不动。"""
        chat = "GroupMessage:pure"
        ledger.config_manager._config["context_compression"][
            "content_retain_tokens"
        ] = 100000
        for i in range(1, 6):
            ledger.add_message(chat, _msg(i, f"m{i}", chat_id=chat))

        before = [m["source_message_id"] for m in ledger.get_all_messages(chat)]
        ledger._split_by_budget(list(ledger.get_all_messages(chat)), 100000)
        after = [m["source_message_id"] for m in ledger.get_all_messages(chat)]

        assert after == before


class TestFormalContext:
    """注入侧：账本是什么就返回什么，不再补摘要前缀。"""

    def test_formal_context_is_ledger_messages(self, ledger):
        chat_id = "GroupMessage:ctx"
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        for i in range(1, 8):
            ledger.add_message(chat_id, _msg(i, f"old{i}", chat_id=chat_id))

        assert ledger.organize_on_group_enter(chat_id, keep_from_timestamp=6.0)

        formal = ledger.get_formal_context(chat_id)
        raw = ledger.get_all_messages(chat_id)
        assert formal == raw
        assert all(m.get("kind") != "context_summary" for m in formal)

    def test_no_summary_is_generated(self, ledger):
        """压缩不再产出摘要文本，current_summary 保持为空。"""
        chat_id = "GroupMessage:nosum"
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        for i in range(1, 8):
            ledger.add_message(chat_id, _msg(i, f"old{i}", chat_id=chat_id))

        assert ledger.organize_on_group_enter(chat_id, keep_from_timestamp=6.0)

        assert ledger.get_current_summary(chat_id) == ""

    def test_partition_has_no_history_prefix(self, ledger):
        """秘书路径：新账本没有摘要，整份都是最近对话。"""
        from astrbot_plugin_angel_heart.core.utils.context_utils import (
            partition_dialogue,
            partition_dialogue_raw,
        )

        chat_id = "GroupMessage:part"
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        for i in range(1, 8):
            ledger.add_message(chat_id, _msg(i, f"old{i}", chat_id=chat_id))

        assert ledger.organize_on_group_enter(chat_id, keep_from_timestamp=6.0)

        hist, recent, ts = partition_dialogue(ledger, chat_id)
        assert hist == []
        assert recent
        assert ts > 0

        hist2, recent2, _ = partition_dialogue_raw(ledger, chat_id)
        assert hist2 == []
        assert recent2


class TestGroupToolRetention:
    """工具不进窗口，超不超时都一样。"""

    def test_tools_dropped_even_when_not_timeout(self, ledger):
        chat_id = "GroupMessage:1"
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        for i in range(1, 60):
            ledger.add_message(chat_id, _msg(i, f"正文{i}" * 20, chat_id=chat_id))
            ledger.add_message(
                chat_id, _msg(1000 + i, f"tool{i}", tool=True, chat_id=chat_id)
            )

        ok = ledger.organize_context(chat_id, mode="group_rule")
        assert ok is True
        _no_tools(ledger.get_formal_context(chat_id))

    def test_group_drops_tools_when_timeout(self, tmp_path):
        """超时同样全清工具。"""
        from pathlib import Path

        cfg = DummyConfig()
        cfg._config["context_compression"]["forgetting_timeout"] = 86400
        cfg._config["context_compression"]["max_conversation_tokens"] = 100000
        lg = ConversationLedger(cfg, Path(tmp_path), astr_context=None)
        chat_id = "GroupMessage:timeout1"
        now = time.time()
        for i in range(1, 6):
            lg.add_message(chat_id, _msg(i, f"old{i}", chat_id=chat_id))
            lg._ledgers[chat_id]["messages"][-1]["timestamp"] = now + i
        for i in range(100, 103):
            lg.add_message(chat_id, _msg(i, f"tool{i}", tool=True, chat_id=chat_id))
            lg._ledgers[chat_id]["messages"][-1]["timestamp"] = now + 100 + i
        lg._last_compression_time[chat_id] = now - 90000
        assert lg._is_forgetting_timeout(chat_id) is True
        ok = lg.organize_context(chat_id, mode="group_rule")
        assert ok is True
        _no_tools(lg.get_all_messages(chat_id))
        lg.close()

    def test_group_enter_keeps_from_timestamp(self, ledger):
        """入场整理：触发点之前的丢掉，之后的原样留在窗口。"""
        chat_id = "GroupMessage:2"
        for i in range(1, 8):
            ledger.add_message(chat_id, _msg(i, f"old{i}", chat_id=chat_id))
        for i in range(8, 12):
            ledger.add_message(chat_id, _msg(i, f"new{i}", chat_id=chat_id))

        ok = ledger.organize_on_group_enter(chat_id, keep_from_timestamp=8.0)
        assert ok is True
        msgs = ledger.get_all_messages(chat_id)
        assert all(m.get("timestamp", 0) >= 8.0 for m in msgs)


class TestPrivateLlmCompress:
    """私聊仍走 LLM 摘要，行为不变。"""

    @pytest.mark.asyncio
    async def test_private_llm_summary_success(self, ledger):
        chat_id = "FriendMessage:9"
        for i in range(1, 20):
            ledger.add_message(chat_id, _msg(i, f"私聊内容{i}" * 500, chat_id=chat_id))
            if i % 3 == 0:
                ledger.add_message(
                    chat_id, _msg(100 + i, f"tool{i}", tool=True, chat_id=chat_id)
                )

        async def fake_llm(prompt: str) -> str:
            assert "私聊" in prompt or "摘要" in prompt or "待收口" in prompt
            return "## Goal\n- 继续私聊任务"

        ok = await ledger.maybe_llm_compress_private(chat_id, fake_llm)
        assert ok is True
        assert "继续私聊任务" in ledger.get_current_summary(chat_id)
        formal = ledger.get_formal_context(chat_id)
        assert formal[0]["kind"] == "context_summary"

    @pytest.mark.asyncio
    async def test_scheduled_messages_survive_await(self, ledger):
        """调度期间入账的消息原样留在最后，不挤掉保留区，也不丢失。"""
        chat_id = "FriendMessage:sched"
        ledger.config_manager._config["context_compression"][
            "content_retain_tokens"
        ] = 100
        ledger._should_compress = lambda _c: True

        for i in range(1, 11):
            ledger.add_message(chat_id, _msg(i, f"m{i}" * 50, chat_id=chat_id))

        prompts = []

        async def summarize(prompt: str) -> str:
            prompts.append(prompt)
            # 模拟 await 期间用户又发了消息
            ledger.add_message(
                chat_id, _msg(11, "期间新消息", chat_id=chat_id)
            )
            return "摘要正文"

        ok = await ledger.maybe_llm_compress_private(chat_id, summarize)
        assert ok is True

        messages = ledger.get_all_messages(chat_id)
        ids = [m.get("source_message_id") for m in messages]
        # 摘要在最前
        assert messages[0].get("kind") == "context_summary"
        # 期间新入账的消息还在最后
        assert ids[-1] == "src-11"
        # 被压成摘要的那批进了提示词
        assert "m1m1" in prompts[0]
        # 保留区和期间新入账的都在账本里
        assert {f"src-{i}" for i in range(4, 12)} <= set(ids)
        # 被压成摘要的那批不再单独留在账本里——两边都不落才是 bug
        assert not ({f"src-{i}" for i in range(1, 4)} & set(ids))

    @pytest.mark.asyncio
    async def test_private_llm_failure_fallback(self, ledger):
        chat_id = "FriendMessage:10"
        for i in range(1, 20):
            ledger.add_message(chat_id, _msg(i, f"私聊内容{i}" * 500, chat_id=chat_id))

        async def boom(prompt: str) -> str:
            raise RuntimeError("llm down")

        ok = await ledger.maybe_llm_compress_private(chat_id, boom)
        # LLM 失败降级到群聊压缩方法，压动了就返回 True
        assert ok is True
        messages = ledger.get_all_messages(chat_id)
        # 规则压缩后条数下降，工具全部不在
        assert len(messages) < 19
        assert all(m.get("role") != "tool" for m in messages)
        # 降级不产摘要
        assert ledger.get_current_summary(chat_id) == ""


class TestCompressionLock:
    def test_concurrent_organize_skips_second(self, ledger):
        chat_id = "GroupMessage:4"
        for i in range(1, 15):
            ledger.add_message(chat_id, _msg(i, f"x{i}" * 40, chat_id=chat_id))

        lock = ledger._get_compression_lock(chat_id)
        assert lock.acquire(blocking=False)
        try:
            ok = ledger.organize_context(chat_id, mode="group_rule")
            assert ok is False
        finally:
            lock.release()


class TestChatLockIsolation:
    """账本锁按会话独立：一个会话被占，别的会话照常读写。"""

    def test_each_chat_has_its_own_lock(self, ledger):
        a = ledger._get_chat_lock("GroupMessage:a")
        b = ledger._get_chat_lock("GroupMessage:b")

        assert a is not b
        assert ledger._get_chat_lock("GroupMessage:a") is a

    def test_locked_chat_does_not_block_another(self, ledger):
        locked = ledger._get_chat_lock("GroupMessage:locked")
        assert locked.acquire(blocking=False)
        try:
            other = "GroupMessage:other"
            ledger.add_message(other, _msg(1, "hi", chat_id=other))
            assert ledger.get_all_messages(other)
        finally:
            locked.release()
