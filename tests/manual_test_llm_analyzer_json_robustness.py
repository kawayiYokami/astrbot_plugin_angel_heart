"""手动测试脚本：验证 LLMAnalyzer 对异常 JSON 输出的鲁棒性。

用法：
    cd E:/github/ai-qq/astrbot/data/plugins/astrbot_plugin_angel_heart
    python tests/manual_test_llm_analyzer_json_robustness.py
"""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parent))

from astrbot_plugin_angel_heart.core.llm_analyzer import LLMAnalyzer


class DummyConfigManager:
    is_reasoning_model = False
    alias = "伊莉雅|AngelHeart"


class DummyContext:
    pass


def build_analyzer() -> LLMAnalyzer:
    return LLMAnalyzer(
        analyzer_model_name="dummy-analyzer",
        context=DummyContext(),
        strategy_guide="",
        config_manager=DummyConfigManager(),
    )


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}\n  expected={expected!r}\n  actual={actual!r}")


def run_case(analyzer: LLMAnalyzer, name: str, response_text: str, expected: dict) -> None:
    decision = analyzer._parse_and_validate_decision(response_text, alias="伊莉雅")

    print(f"[CASE] {name}")
    print(f"  should_reply   = {decision.should_reply!r}")
    print(f"  reply_strategy = {decision.reply_strategy!r}")
    print(f"  topic          = {decision.topic!r}")
    print(f"  reply_target   = {decision.reply_target!r}")
    print(f"  entities       = {decision.entities!r}")
    print(f"  facts          = {decision.facts!r}")
    print(f"  keywords       = {decision.keywords!r}")

    for field_name, expected_value in expected.items():
        actual_value = getattr(decision, field_name)
        assert_equal(actual_value, expected_value, f"案例 {name} 字段 {field_name} 不符合预期")

    print("  -> PASS\n")


def main() -> None:
    analyzer = build_analyzer()

    cases = [
        {
            "name": "facts 被模型弱智地输出成单个字符串",
            "response_text": """
            好的我认真想了一下，下面是结果：
            {"should_reply": false, "reply_strategy": "继续观察", "topic": "刷屏", "facts": "群友在刷666", "keywords": ["666"]}
            """,
            "expected": {
                "should_reply": False,
                "reply_strategy": "继续观察",
                "topic": "刷屏",
                "facts": ["群友在刷666"],
                "keywords": ["666"],
            },
        },
        {
            "name": "只有 should_reply，其他字段全丢",
            "response_text": '{"should_reply": true}',
            "expected": {
                "should_reply": False,
                "reply_strategy": "继续观察",
                "topic": "未知话题",
                "reply_target": "",
                "entities": [],
                "facts": [],
                "keywords": [],
            },
        },
        {
            "name": "列表字段掺杂 null、数字、对象、空串",
            "response_text": """
            ```json
            {
              "should_reply": "true",
              "is_interesting": true,
              "reply_strategy": 12345,
              "topic": null,
              "reply_target": {"name": "红豆"},
              "entities": [null, "夕月", 114514, {"a": 1}, "   "],
              "facts": ["群友提问插件", null, 233, "  ", [1, 2]],
              "keywords": "插件报错"
            }
            ```
            """,
            "expected": {
                "should_reply": True,
                "reply_strategy": "12345",
                "topic": "未知话题",
                "reply_target": "{'name': '红豆'}",
                "entities": ["夕月", "114514", "{'a': 1}"],
                "facts": ["群友提问插件", "233", "[1, 2]"],
                "keywords": ["插件报错"],
            },
        },
        {
            "name": "前面一坨胡言乱语，后面才给 JSON",
            "response_text": """
            我先胡说八道一会儿：facts 应该是世界的真理，猫会开飞机，电饭煲会写代码。
            然后我才想起你要 JSON。
            {"should_reply": 0, "reply_strategy": "继续观察", "topic": "无关闲聊", "entities": "群友", "facts": null, "keywords": ["闲聊"]}
            """,
            "expected": {
                "should_reply": False,
                "reply_strategy": "继续观察",
                "topic": "无关闲聊",
                "entities": ["群友"],
                "facts": [],
                "keywords": ["闲聊"],
            },
        },
        {
            "name": "存在多个 JSON，前面弱智，后面更完整",
            "response_text": """
            {"foo": "bar"}
            一段莫名其妙的解释。
            {"should_reply": "是", "is_questioned": true, "reply_strategy": "回答问题", "topic": "插件配置", "reply_target": "红豆", "entities": ["红豆", "配置项"], "facts": "红豆询问配置", "keywords": ["配置", "JSON"]}
            """,
            "expected": {
                "should_reply": True,
                "reply_strategy": "回答问题",
                "topic": "插件配置",
                "reply_target": "红豆",
                "entities": ["红豆", "配置项"],
                "facts": ["红豆询问配置"],
                "keywords": ["配置", "JSON"],
            },
        },
        {
            "name": "should_reply 缺失时必须保守回退为不回复",
            "response_text": """
            {"reply_strategy": "快去回答", "topic": "看起来很重要", "facts": "但我偏偏不写 should_reply"}
            """,
            "expected": {
                "should_reply": False,
                "reply_strategy": "分析内容无有效JSON",
                "topic": "未知",
                "entities": [],
                "facts": [],
                "keywords": [],
            },
        },
    ]

    print("开始执行 LLMAnalyzer JSON 鲁棒性手动测试。\n")
    for case in cases:
        run_case(
            analyzer=analyzer,
            name=case["name"],
            response_text=case["response_text"],
            expected=case["expected"],
        )

    print("全部案例通过。")


if __name__ == "__main__":
    main()