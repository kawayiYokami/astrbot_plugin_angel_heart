"""工作账本与系统提醒注入测试。"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

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
):
    sys.modules.setdefault(_mod_path, types.ModuleType(_mod_path))

sys.modules["astrbot.api"].logger = MagicMock()

from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger
from astrbot_plugin_angel_heart.core.llm_analyzer import LLMAnalyzer


class TestWorkLedgerFormat:
    def test_secretary_third_person_other_running(self):
        wl = WorkLedger()
        wl.start_work(
            chat_id="g1",
            work_id="w1",
            trigger_message_id="m1",
            trigger_summary="解释双防抖",
            kind="assistant",
        )
        # 另一事件看账本：应提示避让
        text = wl.format_for_secretary("g1", current_work_id="w2")
        assert "助理工作账本" in text
        assert "助理正在处理" in text
        assert "不要让助理处理重复的问题" in text
        assert "解释双防抖" in text

    def test_secretary_current_work_not_blocked(self):
        wl = WorkLedger()
        wl.start_work(
            chat_id="g1",
            work_id="w1",
            trigger_message_id="m1",
            trigger_summary="解释双防抖",
            kind="assistant",
        )
        text = wl.format_for_secretary("g1", current_work_id="w1")
        assert "本轮待处理" in text
        assert "可以继续决策" in text
        assert "不要让助理处理重复的问题" not in text

    def test_assistant_current_work_is_excluded_from_temporary_reminder(self):
        wl = WorkLedger()
        wl.start_work(
            chat_id="g1",
            work_id="w1",
            trigger_message_id="m1",
            trigger_summary="解释双防抖",
            kind="assistant",
        )
        text = wl.format_for_assistant("g1", current_work_id="w1")
        assert text == "工作账本：当前没有其他已登记工作。"

    def test_assistant_other_running_avoids_duplicate(self):
        wl = WorkLedger()
        wl.start_work(
            chat_id="g1",
            work_id="w1",
            trigger_message_id="m1",
            trigger_summary="解释双防抖",
            kind="assistant",
        )
        text = wl.format_for_assistant("g1", current_work_id="w2")
        assert "工作账本：" in text
        assert "解释双防抖" in text
        assert "请勿重复处理其他运行中的工作。" in text

    def test_complete_work_status(self):
        wl = WorkLedger()
        wl.start_work(
            chat_id="g1",
            work_id="w1",
            trigger_message_id="m1",
            trigger_summary="任务A",
        )
        wl.complete_work("g1", "w1", status="done", result_summary="已回复")
        active = wl.get_active_works("g1")
        assert active == []
        recent = wl.get_recent_works("g1")
        assert recent[0].status == "done"
        assert recent[0].result_summary == "已回复"


class TestAnalyzerPromptInjection:
    def test_build_prompt_appends_work_ledger(self):
        config = MagicMock()
        config.alias = "草王"
        config.ai_self_identity = "测试身份"
        analyzer = LLMAnalyzer("mock", MagicMock(), "策略", config)
        analyzer.base_prompt_template = (
            "BASE\n{historical_context}\n{recent_dialogue}\n"
            "{reply_strategy_guide}\n{alias}\n{ai_self_identity}"
        )
        analyzer.strategy_guide = "策略"
        prompt = analyzer._build_prompt(
            historical_context=[],
            recent_dialogue=[{"role": "user", "content": "hi", "sender_name": "u"}],
            work_ledger_text="助理正在处理：解释双防抖。不要让助理处理重复的问题。",
        )
        assert "<助理工作账本>" in prompt
        assert "不要让助理处理重复的问题" in prompt
        assert "解释双防抖" in prompt


class TestTemporaryWorkContext:
    def test_front_desk_builds_no_save_work_context_without_repeating_current_prompt(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        config = MagicMock()
        config.alias = "草王"
        angel = MagicMock()
        angel.work_ledger = WorkLedger()
        angel.work_ledger.start_work(
            chat_id="GroupMessage:1",
            work_id="e1",
            trigger_message_id="m1",
            trigger_summary="正在答群友问题",
        )
        angel.work_ledger.start_work(
            chat_id="GroupMessage:1",
            work_id="e2",
            trigger_message_id="m2",
            trigger_summary="另一个工作",
        )
        angel.astr_context = MagicMock()
        fd = FrontDesk(config, angel)

        class E:
            def get_extra(self, k, d=None):
                if k == "angelheart_work_id":
                    return "e1"
                return d

        ctx = fd._build_temporary_work_ledger_context("GroupMessage:1", E())
        assert ctx is not None
        assert ctx["_no_save"] is True
        assert ctx["is_temporary_context"] is True
        text = ctx["content"][0]["text"]
        assert "<system_reminder>" in text
        assert "正在答群友问题" not in text
        assert "另一个工作" in text
        assert "请正常回答" not in text
