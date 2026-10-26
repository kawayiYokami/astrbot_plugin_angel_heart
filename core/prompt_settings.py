"""系统提示词设置存储。

助理画像这类设置不放进 AstrBot 插件配置：自带设置页会把 schema 里的字段
全部渲染出来。改为插件自己的 JSON 存储，只由本插件 WebUI 读写。
"""

import json
import os
import threading
from typing import Any, Dict

STORE_FILE_NAME = "prompt_settings.json"

MODE_PRESET = "preset"
MODE_CUSTOM = "custom"


class PromptSettingsStore:
    """系统提示词设置的 JSON 持久化存储（全局一份）。"""

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._file_path = os.path.join(data_dir, STORE_FILE_NAME)
        self._lock = threading.Lock()
        self._mode = MODE_PRESET
        self._custom_prompt = ""
        self._loaded = False

    # ---------- 持久化 ----------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        data: Dict[str, Any] = {}
        if os.path.isfile(self._file_path):
            try:
                with open(self._file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError, TypeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        self._mode = self._normalize_mode(data.get("mode"))
        self._custom_prompt = str(data.get("custom_prompt") or "")
        self._loaded = True

    def _save(self) -> None:
        payload = {
            "version": 1,
            "mode": self._mode,
            "custom_prompt": self._custom_prompt,
        }
        tmp_path = self._file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._file_path)

    @staticmethod
    def _normalize_mode(raw: Any) -> str:
        value = str(raw or "").strip().lower()
        return MODE_CUSTOM if value == MODE_CUSTOM else MODE_PRESET

    # ---------- 读写 ----------

    def get_mode(self) -> str:
        with self._lock:
            self._ensure_loaded()
            return self._mode

    def get_custom_prompt(self) -> str:
        with self._lock:
            self._ensure_loaded()
            return self._custom_prompt

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            return {"mode": self._mode, "custom_prompt": self._custom_prompt}

    def update(
        self, mode: Any = None, custom_prompt: Any = None
    ) -> Dict[str, Any]:
        """更新设置；未传入的字段保持不变。"""
        with self._lock:
            self._ensure_loaded()
            if mode is not None:
                self._mode = self._normalize_mode(mode)
            if custom_prompt is not None:
                self._custom_prompt = str(custom_prompt)
            self._save()
            return {"mode": self._mode, "custom_prompt": self._custom_prompt}
