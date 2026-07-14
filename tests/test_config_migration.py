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
