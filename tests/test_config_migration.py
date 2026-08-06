import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_PARENT = str(PLUGIN_ROOT.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from astrbot_plugin_angel_heart.core import config_migration


def test_migration_removes_retired_comfort_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "patience_interval": 60,
                "comfort_words": "稍等",
                "comfort": {
                    "patience_interval": 120,
                    "comfort_words": "马上",
                },
                "debug": {"debug_mode": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_migration, "_find_config_path", lambda: str(config_path))

    config_migration.run_migration()

    migrated = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert "patience_interval" not in migrated
    assert "comfort_words" not in migrated
    assert "comfort" not in migrated
    assert migrated["debug"] == {"debug_mode": True}


def test_migration_removes_llm_timeout_but_preserves_active_cooldowns(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "timing": {
                    "llm_timeout": 180,
                    "waiting_time": 14,
                    "no_reply_cooldown": 7,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_migration, "_find_config_path", lambda: str(config_path))

    config_migration.run_migration()

    migrated = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert "llm_timeout" not in migrated["timing"]
    assert migrated["timing"]["waiting_time"] == 14
    assert "no_reply_cooldown" not in migrated["timing"]


def test_migration_discards_grouped_analysis_on_mention_only(tmp_path, monkeypatch):
    """旧分组键 wake_interaction.analysis_on_mention_only 是旧分析机制字段，
    与新入场机制 enter_on_mention_only 完全无关，直接废弃删除，不迁移旧值。"""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "wake_interaction": {
                    "analysis_on_mention_only": False,
                    "alias": "小天使",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_migration, "_find_config_path", lambda: str(config_path))

    config_migration.run_migration()

    migrated = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert "analysis_on_mention_only" not in migrated["wake_interaction"]
    assert "enter_on_mention_only" not in migrated["wake_interaction"]
    assert migrated["wake_interaction"]["alias"] == "小天使"


def test_migration_discards_flat_analysis_on_mention_only(tmp_path, monkeypatch):
    """旧扁平键 analysis_on_mention_only 同样废弃删除，不迁移到新字段。"""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "analysis_on_mention_only": False,
                "alias": "小天使",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_migration, "_find_config_path", lambda: str(config_path))

    config_migration.run_migration()

    migrated = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert "analysis_on_mention_only" not in migrated
    assert "enter_on_mention_only" not in migrated
    assert migrated["wake_interaction"]["alias"] == "小天使"


def test_migration_preserves_existing_enter_on_mention_only(tmp_path, monkeypatch):
    """新入场字段 enter_on_mention_only 已存在时原样保留，不受旧字段影响。"""
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "wake_interaction": {
                    "analysis_on_mention_only": False,
                    "enter_on_mention_only": True,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_migration, "_find_config_path", lambda: str(config_path))

    config_migration.run_migration()

    migrated = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert "analysis_on_mention_only" not in migrated["wake_interaction"]
    assert migrated["wake_interaction"]["enter_on_mention_only"] is True
