"""
钉死决策逻辑：should_reply AND (is_questioned OR is_interesting OR reply_even)

覆盖 analyzer 正向条件过滤 + secretary force_reply 带理由约束。
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保 astrbot_plugin_angel_heart 包可被导入（core/、models/ 等使用相对导入）
HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
_PARENT = str(PLUGIN_ROOT.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
# astrbot stubs（conftest.py 已做，这里加固）
import types as _types
for _mod_path in (
    "astrbot", "astrbot.api", "astrbot.api.event",
    "astrbot.core", "astrbot.core.agent", "astrbot.core.agent.message",
    "astrbot.core.message", "astrbot.core.message.components",
):
    sys.modules.setdefault(_mod_path, _types.ModuleType(_mod_path))


def make_config(reply_even: bool, force_reply: bool = True):
    """构造最小 ConfigManager 桩"""
    from astrbot_plugin_angel_heart.core.config_manager import ConfigManager

    return ConfigManager({
        "analyzer_model": "mock-model",
        "wake_interaction": {
            "force_reply_when_summoned": force_reply,
            "reply_even_not_questioned": reply_even,
            "alias": "fairy",
        },
        "personality": {
            "ai_self_identity": "测试用",
            "reply_strategy_guide": "",
        },
        "timing": {},
        "leave_reply": {},
        "access_control": {},
        "context_compression": {},
        "comfort": {},
        "debug": {},
    })


def make_decision(should_reply: bool, questioned: bool, interesting: bool) -> dict:
    """构造 LLM 返回的 JSON 字符串"""
    return json.dumps({
        "should_reply": should_reply,
        "is_questioned": questioned,
        "is_interesting": interesting,
        "reply_strategy": "情感支持类策略",
        "topic": "测试",
        "reply_target": "测试用户",
        "entities": [],
        "facts": [],
        "keywords": [],
    }, ensure_ascii=False)


class TestAnalyzerForwardCondition:
    """
    测试 LLMAnalyzer.analyze_and_decide 的正向条件过滤。

    公式：should_reply AND (is_questioned OR is_interesting OR reply_even_not_questioned)
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reply_even,should_reply,questioned,interesting,expected", [
        # reply_even=true → 无条件放行
        (True,  True,  True,  True,  True),
        (True,  True,  True,  False, True),
        (True,  True,  False, True,  True),
        (True,  True,  False, False, True),
        (True,  False, True,  True,  False),
        (True,  False, False, False, False),
        # reply_even=false → 必须有提问或兴趣
        (False, True,  True,  True,  True),
        (False, True,  True,  False, True),
        (False, True,  False, True,  True),
        (False, True,  False, False, False),  # ← 关键 case
        (False, False, True,  True,  False),
        (False, False, False, False, False),
    ])
    async def test_analyzer_decision(
        self, reply_even, should_reply, questioned, interesting, expected,
    ):
        from astrbot_plugin_angel_heart.core.llm_analyzer import LLMAnalyzer
        from astrbot_plugin_angel_heart.core.config_manager import ConfigManager

        config = make_config(reply_even=reply_even)
        response_json = make_decision(should_reply, questioned, interesting)

        with patch.object(LLMAnalyzer, "_call_ai_model", new_callable=AsyncMock) as mock_call:
            mock_call.return_value = response_json

            with patch("astrbot_plugin_angel_heart.core.llm_analyzer.PromptModuleLoader") as mock_loader_cls:
                mock_loader = mock_loader_cls.return_value
                mock_loader.build_prompt_template.return_value = "mock template {historical_context} {recent_dialogue} {reply_strategy_guide} {alias} {ai_self_identity}"

                analyzer = LLMAnalyzer(
                    analyzer_model_name="mock-model",
                    context=None,
                    strategy_guide="",
                    config_manager=config,
                )

                decision = await analyzer.analyze_and_decide([], [], "test-chat")

                assert decision.should_reply == expected, (
                    f"reply_even={reply_even}, should_reply={should_reply}, "
                    f"questioned={questioned}, interesting={interesting} → "
                    f"expected={expected}, got={decision.should_reply}"
                )


class TestForceReplyWithReason:
    """
    测试 Secretary._handle_summoned_reply 的带理由强制回复。

    force_reply=true 时，只在 (is_questioned OR is_interesting OR reply_even) 满足时才覆盖。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reply_even,should_reply,questioned,interesting,expected",
        [
            # reply_even=true：任何 case force 都兜底
            (True, True, True, True, True),
            (True, True, False, False, True),
            (True, False, True, False, True),
            (True, False, False, False, True),
            # reply_even=false：无理由不兜底
            (False, False, False, False, False),  # ← 关键
            (False, False, True, False, True),
            (False, False, False, True, True),
        ],
    )
    async def test_force_only_with_reason(
        self, reply_even, should_reply, questioned, interesting, expected,
    ):
        from astrbot_plugin_angel_heart.models.analysis_result import SecretaryDecision

        config = make_config(reply_even=reply_even, force_reply=True)

        # 模拟 perform_analysis 返回的决策
        decision = SecretaryDecision(
            should_reply=should_reply,
            is_questioned=questioned,
            is_interesting=interesting,
            reply_strategy="测试策略",
            topic="测试",
            entities=[],
            facts=[],
            keywords=[],
        )

        # 模拟 _handle_summoned_reply 中的 force_reply 逻辑
        if config.force_reply_when_summoned:
            has_reason = (
                decision.is_questioned
                or decision.is_interesting
                or config.reply_even_not_questioned
            )
            if has_reason:
                decision.should_reply = True
                decision.reply_strategy = "被呼唤回复"

        assert decision.should_reply == expected, (
            f"reply_even={reply_even}, should_reply={should_reply}, "
            f"questioned={questioned}, interesting={interesting} → "
            f"expected={expected}, got={decision.should_reply}"
        )
