"""配置热更新测试：改配置后组件下一轮就吃到新值，不留构造时快照。

对应现场问题：WebUI 改 analyzer_model 并保存后，秘书仍用旧模型，
而同一份配置下的图片转述已切新模型——差别只在读取方式。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.llm_analyzer import LLMAnalyzer


def _config(**overrides) -> dict:
    data = {
        "analyzer_model": "model-a",
        "is_reasoning_model": False,
        "access_control": {"chat_ids": ["1001"], "whitelist_enabled": True},
        "timing": {},
    }
    data.update(overrides)
    return data


def _analyzer(config: dict | None = None) -> tuple[LLMAnalyzer, ConfigManager]:
    manager = ConfigManager(config or _config())
    return LLMAnalyzer(context=None, config_manager=manager), manager


class TestAnalyzerModelHotReload:
    def test_model_name_follows_config_without_reload(self):
        analyzer, manager = _analyzer()
        assert analyzer.analyzer_model_name == "model-a"

        # 模拟 WebUI 原地改键值：不换 ConfigManager，也不调 reload_config
        manager._config["analyzer_model"] = "model-b"

        assert analyzer.analyzer_model_name == "model-b", "分析器不应保留模型名快照"

    def test_model_name_follows_replaced_manager(self):
        analyzer, _ = _analyzer()
        new_manager = ConfigManager(_config(analyzer_model="model-c"))

        analyzer.reload_config(new_manager)

        assert analyzer.analyzer_model_name == "model-c"


class TestRevision:
    def test_revision_increments_on_mark_changed(self):
        _, manager = _analyzer()
        before = manager.revision

        manager.mark_changed()

        assert manager.revision == before + 1

    def test_for_chat_view_reads_latest_config(self):
        _, manager = _analyzer()
        view = manager.for_chat("GroupMessage:1001")
        assert view.analyzer_model == "model-a"

        manager._config["analyzer_model"] = "model-b"

        assert view.analyzer_model == "model-b", "群视图应与全局共享同一份配置"


class TestOutputContractHotReload:
    def test_contract_rebuilds_when_reasoning_flag_flips(self):
        analyzer, manager = _analyzer()
        plain_contract = analyzer.output_contract
        assert "请先输出完整的分析和推理报告" in plain_contract

        manager._config["is_reasoning_model"] = True
        manager.mark_changed()
        analyzer._build_prompts([], [])

        assert (
            "直接输出下面的 JSON 对象" in analyzer.output_contract
        ), "切换推理模型后输出契约应随之重建"

    def test_contract_kept_when_nothing_changed(self):
        analyzer, _ = _analyzer()
        before = analyzer.output_contract

        analyzer._build_prompts([], [])

        assert analyzer.output_contract == before


class TestWhitelistHotReload:
    def _plugin(self, manager: ConfigManager):
        from astrbot_plugin_angel_heart.main import AngelHeartPlugin

        plugin = object.__new__(AngelHeartPlugin)
        plugin.config_manager = manager
        plugin._whitelist_cache = set()
        plugin._whitelist_token = None
        return plugin

    def test_whitelist_rebuilt_after_config_change(self):
        manager = ConfigManager(_config())
        plugin = self._plugin(manager)
        assert plugin._ensure_whitelist() == {"1001"}

        manager._config["access_control"]["chat_ids"] = ["1001", "1002"]
        manager.mark_changed()

        assert plugin._ensure_whitelist() == {"1001", "1002"}, "白名单应随配置版本重建"

    def test_whitelist_not_rebuilt_without_change(self):
        manager = ConfigManager(_config())
        plugin = self._plugin(manager)
        first = plugin._ensure_whitelist()

        assert plugin._ensure_whitelist() is first, "版本未变时不应重复构建集合"


class TestReloadConfigKeepsSingleManager:
    """reload_config 只换配置内容，不换管理器实例。

    换实例会让没被逐一改引用的组件（媒体缓存、钓鱼回复等）继续读旧配置，
    这正是「改了一项配置，另一个功能不跟随」的来源。
    """

    def _plugin(self, config: dict):
        from astrbot_plugin_angel_heart.main import AngelHeartPlugin

        plugin = object.__new__(AngelHeartPlugin)
        plugin.config = config
        plugin.config_manager = ConfigManager(config)
        plugin._whitelist_cache = set()
        plugin._whitelist_token = None
        plugin.secretary = MagicMock()
        return plugin

    def test_manager_identity_kept_and_values_updated(self):
        plugin = self._plugin(_config())
        manager_before = plugin.config_manager

        plugin.reload_config(_config(analyzer_model="model-z"))

        assert plugin.config_manager is manager_before, "不应新建配置管理器实例"
        assert plugin.config_manager.analyzer_model == "model-z"

    def test_config_object_reference_kept_for_webui(self):
        config = _config()
        plugin = self._plugin(config)

        plugin.reload_config(_config(analyzer_model="model-y", is_reasoning_model=True))

        assert plugin.config is config, "共享配置对象必须保持同一引用"
        assert config["analyzer_model"] == "model-y"
        assert plugin.config_manager.revision >= 1

