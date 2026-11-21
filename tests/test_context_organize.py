"""消息管理与上下文压缩边界测试。"""

from __future__ import annotations

import re
import time
import types
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

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
                    "content_retain_tokens": 80,
                    "tool_retain_tokens": 40,
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
    }
    if tool:
        m["role"] = "tool"
        m["tool_call_id"] = f"t{i}"
    return m


class TestGroupRuleOrganize:
    def test_group_drops_tools_and_builds_summary(self, ledger):
        """未超时：群聊整理应保留尾部工具（对齐“超过一定时间才丢”）"""
        chat_id = "GroupMessage:1"
        for i in range(1, 12):
            ledger.add_message(chat_id, _msg(i, f"正文{i}" * 20, chat_id=chat_id))
            ledger.add_message(
                chat_id, _msg(100 + i, f"tool{i}", tool=True, chat_id=chat_id)
            )

        # 未超时（forgetting_timeout=0 永不超时）→ 保留尾部工具
        ok = ledger.organize_context(chat_id, mode="group_rule")
        assert ok is True
        summary = ledger.get_current_summary(chat_id)
        assert summary
        formal = ledger.get_formal_context(chat_id)
        assert formal
        assert formal[0].get("kind") == "context_summary"
        # 未超时：应保留至少一个 tool（按 tool_retain_tokens 预算）
        assert any(m.get("role") == "tool" for m in formal[1:])

    def test_group_drops_tools_when_timeout(self, tmp_path):
        """超时：群聊整理才全清工具"""
        from pathlib import Path
        import time

        cfg = DummyConfig()
        # 开启超时判定
        cfg._config["context_compression"]["forgetting_timeout"] = 86400
        # 提高 token 上限避免加消息时因旧时间戳自动触发整理
        cfg._config["context_compression"]["max_conversation_tokens"] = 100000
        lg = ConversationLedger(cfg, Path(tmp_path), astr_context=None)
        chat_id = "GroupMessage:timeout1"
        now = time.time()
        for i in range(1, 6):
            lg.add_message(
                chat_id, _msg(i, f"old{i}", chat_id=chat_id, tool=False)
            )
            lg._ledgers[chat_id]["messages"][-1]["timestamp"] = now + i
        for i in range(100, 103):
            lg.add_message(chat_id, _msg(i, f"tool{i}", tool=True, chat_id=chat_id))
            lg._ledgers[chat_id]["messages"][-1]["timestamp"] = now + 100 + i
        # 人为把上次整理时间设为很久以前，触发超时
        lg._last_compression_time[chat_id] = now - 90000
        assert lg._is_forgetting_timeout(chat_id) is True
        ok = lg.organize_context(chat_id, mode="group_rule")
        assert ok is True
        msgs = [m for m in lg.get_all_messages(chat_id) if m.get("kind") != "context_summary"]
        assert all(m.get("role") != "tool" for m in msgs)
        lg.close()

    def test_group_enter_keeps_from_timestamp(self, ledger):
        chat_id = "GroupMessage:2"
        for i in range(1, 8):
            ledger.add_message(chat_id, _msg(i, f"old{i}", chat_id=chat_id))
        for i in range(8, 12):
            ledger.add_message(chat_id, _msg(i, f"new{i}", chat_id=chat_id))

        ok = ledger.organize_on_group_enter(chat_id, keep_from_timestamp=8.0)
        assert ok is True
        msgs = ledger.get_all_messages(chat_id)
        # 保留触发后消息 + 摘要
        body = [m for m in msgs if m.get("kind") != "context_summary"]
        assert all(m.get("timestamp", 0) >= 8.0 for m in body)

    def test_group_min_retain_fallback_retains_tools_when_not_timeout(self, ledger):
        """正文不足 MIN_RETAIN 时，未超时 fallback 应按预算保留工具"""
        chat_id = "GroupMessage:toolfb"
        # 只有 3 条正文 + 若干 tool，触发 fallback
        for i in range(1, 4):
            ledger.add_message(chat_id, _msg(i, f"u{i}", chat_id=chat_id))
            ledger.add_message(
                chat_id, _msg(100 + i, f"tool{i}", tool=True, chat_id=chat_id)
            )
        ok = ledger.organize_context(chat_id, mode="group_rule")
        msgs = [
            m
            for m in ledger.get_all_messages(chat_id)
            if m.get("kind") != "context_summary"
        ]
        # 未超时：fallback 按 keep_tools=True 保留尾部工具
        assert any(m.get("role") == "tool" for m in msgs)


class TestPrivateLlmCompress:
    @pytest.mark.asyncio
    async def test_private_llm_summary_success(self, ledger):
        chat_id = "FriendMessage:9"
        for i in range(1, 20):
            ledger.add_message(
                chat_id, _msg(i, f"私聊内容{i}" * 30, chat_id=chat_id)
            )
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
    async def test_private_llm_failure_fallback(self, ledger):
        chat_id = "FriendMessage:10"
        for i in range(1, 20):
            ledger.add_message(
                chat_id, _msg(i, f"私聊内容{i}" * 30, chat_id=chat_id)
            )

        async def boom(prompt: str) -> str:
            raise RuntimeError("llm down")

        ok = await ledger.maybe_llm_compress_private(chat_id, boom)
        assert ok is True
        # 失败后仍有规则摘要，不留半成品空提交
        assert ledger.get_current_summary(chat_id)


class TestPartitionIncludesSummary:
    def test_partition_dialogue_prefixes_summary(self, ledger):
        from astrbot_plugin_angel_heart.core.utils.context_utils import (
            partition_dialogue,
            partition_dialogue_raw,
        )

        chat_id = "GroupMessage:3"
        for i in range(1, 6):
            m = _msg(i, f"m{i}", chat_id=chat_id)
            ledger.add_message(chat_id, m)
        ledger.set_current_summary(chat_id, "历史已收口")

        hist, recent, ts = partition_dialogue(ledger, chat_id)
        assert hist
        assert hist[0]["kind"] == "context_summary"
        assert "历史已收口" in hist[0]["content"]
        assert recent
        assert ts > 0

        hist2, recent2, _ = partition_dialogue_raw(ledger, chat_id)
        assert hist2[0]["kind"] == "context_summary"


class TestGroupSummaryNotDuplicated:
    """issue #88：群聊离场→入场整理不能让摘要自身重复计入。"""

    def test_repeated_group_enter_does_not_double_summary(self, ledger):
        """连续 5 次入场整理，摘要恒等于「旧摘要 + 本轮丢弃的普通消息」。"""
        chat_id = "GroupMessage:stable"
        # 避免入库路径自动触发整理，只让本测试显式整理
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        base = time.time()
        expected: list[str] = []
        pending: list[str] = []
        lengths: list[int] = []

        for round_no in range(5):
            round_start = base + round_no * 10
            for k in range(3):
                text = f"m{round_no}{k}" + "y" * 15
                msg = _msg(round_no * 10 + k, text, chat_id=chat_id)
                msg["sender_name"] = "u"
                msg["timestamp"] = round_start + k
                ledger.add_message(chat_id, msg)
                pending.append(text)

            # 本轮只保留最后一条，其余全部收进摘要
            expected.extend(pending[:-1])
            pending = pending[-1:]
            assert ledger.organize_on_group_enter(
                chat_id, keep_from_timestamp=round_start + 2
            )

            messages = ledger.get_all_messages(chat_id)
            summaries = [m for m in messages if m.get("kind") == "context_summary"]
            assert len(summaries) == 1
            assert messages[0].get("kind") == "context_summary"

            summary = ledger.get_current_summary(chat_id)
            # 摘要 = 旧摘要 + 本轮丢弃的普通消息，逐字比对，多算一次就会失败
            sep = ConversationLedger.SUMMARY_BLOCK_SEPARATOR
            assert summary == sep.join(f"u: {text}" for text in expected)
            # 展示前缀只属于 content 外壳，不能叠进摘要正文
            assert ConversationLedger.SUMMARY_CONTENT_PREFIX not in summary
            lengths.append(len(summary))

        # 每轮只累加本轮丢弃的消息，长度线性增长而不是翻倍
        assert lengths == sorted(lengths)
        assert lengths[-1] < 2 * lengths[-2]


class TestRuleSummaryTrimming:
    """超长摘要按消息条目裁剪，不把某条消息切成半句。"""

    def test_long_summary_drops_oldest_entries_whole(self, ledger):
        chat_id = "GroupMessage:trim"
        ledger.config_manager._config["context_compression"][
            "max_conversation_tokens"
        ] = 100000
        base = time.time()
        for i in range(1, 41):
            msg = _msg(i, f"m{i:02d}" + "x" * 100, chat_id=chat_id)
            msg["sender_name"] = "u"
            msg["timestamp"] = base + i
            ledger.add_message(chat_id, msg)

        assert ledger.organize_on_group_enter(chat_id, keep_from_timestamp=base + 100)

        summary = ledger.get_current_summary(chat_id)
        assert len(summary) <= ConversationLedger.RULE_SUMMARY_MAX_CHARS
        assert summary.startswith(ConversationLedger.SUMMARY_OMIT_MARKER)
        # 最旧的条目被整条丢弃，最新的条目完整保留
        assert "m01" not in summary
        assert f"m40{'x' * 100}" in summary
        # 保留下来的每个条目都是完整消息，没有从中间切断
        sep = ConversationLedger.SUMMARY_BLOCK_SEPARATOR
        body = summary.split("\n", 1)[1]
        entries = [e for e in body.split(sep) if e.strip()]
        assert entries
        assert all(re.fullmatch(r"u: m\d{2}x{100}", e) for e in entries)
        assert entries[-1] == f"u: m40{'x' * 100}"

    def test_oversized_single_entry_keeps_newest_tail(self, ledger):
        """单条自身就超预算时只能截断，但保留的是最新的尾部。"""
        block = "u: " + "旧" * 5000 + "最新尾巴"

        summary = ledger._trim_rule_summary([block])

        assert len(summary) <= ConversationLedger.RULE_SUMMARY_MAX_CHARS
        assert summary.startswith(ConversationLedger.SUMMARY_OMIT_MARKER)
        assert summary.endswith("最新尾巴")


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
