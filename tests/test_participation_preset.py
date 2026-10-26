"""提示词文档结构测试。

规则文档 prompts/system_prompt.md 作为第一条用户提示词；会话人格由 AstrBot 提供。
输出契约与对话模板固定不变，始终随规则一起发给分析模型。
"""

from __future__ import annotations

import json
from pathlib import Path

from astrbot_plugin_angel_heart.core.prompt_module_loader import PromptModuleLoader

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _loader() -> PromptModuleLoader:
    return PromptModuleLoader()


def test_system_prompt_has_no_strategy_placeholder():
    """接话策略占位符已下线，策略清单直接写在提示词里。"""
    text = _loader().system_prompt

    assert "{reply_strategy_guide}" not in text
    assert "可选策略类型" in text


def test_system_prompt_does_not_rely_on_plugin_identity_config():
    """身份改由会话人格提供，提示词不再引用插件侧身份字段。"""
    text = _loader().system_prompt

    assert "{ai_self_identity}" not in text
    assert "{alias}" in text


def test_output_contract_and_template_hold_their_placeholders():
    loader = _loader()

    assert "{output_report_rule}" in loader.output_contract
    assert "{historical_context}" in loader.conversation_template
    assert "{recent_dialogue}" in loader.conversation_template


def test_loader_builds_output_contract_for_both_model_kinds():
    loader = _loader()

    reasoning_contract = loader.build_output_contract(is_reasoning_model=True)
    plain_contract = loader.build_output_contract(is_reasoning_model=False)

    assert "{output_report_rule}" not in reasoning_contract
    assert "{output_report_rule}" not in plain_contract
    assert "不需要任何分析报告" in reasoning_contract
    assert "请先输出完整的分析和推理报告" in plain_contract


def test_schema_no_longer_contains_personality_group():
    """助理画像配置页整体删除，身份与提示词都不再由插件配置。"""
    schema_path = PLUGIN_ROOT / "_conf_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert "personality" not in schema


def test_prompt_settings_store_preset_custom_roundtrip(tmp_path):
    from astrbot_plugin_angel_heart.core.prompt_settings import PromptSettingsStore

    store = PromptSettingsStore(str(tmp_path))
    assert store.get_mode() == "preset"
    assert store.get_custom_prompt() == ""

    snapshot = store.update(mode="custom", custom_prompt="只聊足球")
    assert snapshot == {"mode": "custom", "custom_prompt": "只聊足球"}
    assert store.snapshot() == snapshot

    # 非法值回落预设
    assert store.update(mode="whatever")["mode"] == "preset"


def test_config_manager_reads_prompt_settings(tmp_path):
    from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
    from astrbot_plugin_angel_heart.core.prompt_presets import load_preset_system_prompt
    from astrbot_plugin_angel_heart.core.prompt_settings import PromptSettingsStore

    store = PromptSettingsStore(str(tmp_path))
    config = ConfigManager({})
    config.attach_prompt_settings(store)

    # 预设：用户乱填也不影响，返回内置全文
    assert config.reply_strategy_mode == "preset"
    assert config.reply_strategy_guide == load_preset_system_prompt()

    # 自定义：返回用户文本；空白回落预设
    store.update(mode="custom", custom_prompt="只聊足球")
    assert config.for_chat("chat:g:1").reply_strategy_guide == "只聊足球"
    store.update(mode="custom", custom_prompt="   ")
    assert config.for_chat("chat:g:1").reply_strategy_guide == load_preset_system_prompt()


def test_initial_custom_prompt_is_persona_based_starter():
    from astrbot_plugin_angel_heart.core.prompt_presets import INITIAL_CUSTOM_PROMPT

    assert INITIAL_CUSTOM_PROMPT.startswith("请根据你的人设，选择一个适合的参与决策。")
    assert "优先参与" in INITIAL_CUSTOM_PROMPT
    assert "保持沉默" in INITIAL_CUSTOM_PROMPT
