"""消息管理与上下文压缩边界测试。"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
_PARENT = str(PLUGIN_ROOT.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

for _mod_path in (
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.core",
    "astrbot.core.agent",
    "astrbot.core.agent.message",
    "astrbot.core.message",
    "astrbot.core.message.components",
    "astrbot.core.star",
    "astrbot.core.star.context",
):
    sys.modules.setdefault(_mod_path, types.ModuleType(_mod_path))

sys.modules["astrbot.api"].logger = MagicMock()
sys.modules["astrbot.api.event"].MessageChain = MagicMock
sys.modules["astrbot.core.message.components"].Plain = type("Plain", (), {})
sys.modules["astrbot.core.message.components"].At = type("At", (), {})
sys.modules["astrbot.core.star.context"].Context = type("Context", (), {})

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
        chat_id = "GroupMessage:1"
        for i in range(1, 12):
            ledger.add_message(chat_id, _msg(i, f"正文{i}" * 20, chat_id=chat_id))
            ledger.add_message(
                chat_id, _msg(100 + i, f"tool{i}", tool=True, chat_id=chat_id)
            )

        # 强制整理
        ok = ledger.organize_context(chat_id, mode="group_rule")
        assert ok is True
        summary = ledger.get_current_summary(chat_id)
        assert summary
        formal = ledger.get_formal_context(chat_id)
        assert formal
        assert formal[0].get("kind") == "context_summary"
        # 群聊工具应被丢掉
        assert all(m.get("role") != "tool" for m in formal[1:])

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

    def test_group_min_retain_fallback_drops_tools(self, ledger):
        """正文不足 MIN_RETAIN 时 fallback 也不得把 tool 塞回连续块。"""
        chat_id = "GroupMessage:toolfb"
        # 只有 3 条正文 + 若干 tool，触发 fallback
        for i in range(1, 4):
            ledger.add_message(chat_id, _msg(i, f"u{i}", chat_id=chat_id))
            ledger.add_message(
                chat_id, _msg(100 + i, f"tool{i}", tool=True, chat_id=chat_id)
            )
        ok = ledger.organize_context(chat_id, mode="group_rule")
        # 可能因预算/摘要条件返回 True/False，但 messages 不得含 tool
        msgs = [
            m
            for m in ledger.get_all_messages(chat_id)
            if m.get("kind") != "context_summary"
        ]
        assert all(m.get("role") != "tool" for m in msgs)


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
