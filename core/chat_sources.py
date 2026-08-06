"""群聊/私聊来源登记存储。

记录所有见过的来源（群聊按群号、私聊按 QQ），保存显示名与首次/最近见到时间，
供 WebUI 绑定页认群与展示。显示名来自上游同步字段：
- 群聊：event.message_obj.group.group_name（aiocqhttp 已从 OneBot 事件填入）
- 私聊：event.message_obj.sender.nickname
"""

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

STORE_FILE_NAME = "chat_sources.json"


class ChatSourcesStore:
    """来源登记的 JSON 持久化存储。"""

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._file_path = os.path.join(data_dir, STORE_FILE_NAME)
        self._lock = threading.Lock()
        self._sources: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

    # ---------- 持久化 ----------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        data: Dict = {}
        if os.path.isfile(self._file_path):
            try:
                with open(self._file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError, TypeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        sources = data.get("sources", {})
        if isinstance(sources, dict):
            self._sources = {
                str(k): v for k, v in sources.items() if isinstance(v, dict)
            }
        self._loaded = True

    def save(self) -> None:
        data = {"version": 1, "sources": self._sources}
        tmp_path = self._file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._file_path)

    @staticmethod
    def _now() -> float:
        return time.time()

    # ---------- 登记与查询 ----------

    def record(self, chat_id: str, display_name: str, kind: str) -> None:
        """登记一次来源；已存在时只更新显示名与最近见到时间。"""
        if not chat_id:
            return
        chat_id = str(chat_id)
        now = self._now()
        with self._lock:
            self._ensure_loaded()
            entry = self._sources.get(chat_id)
            if entry is None:
                self._sources[chat_id] = {
                    "chat_id": chat_id,
                    "display_name": display_name or "",
                    "kind": kind,
                    "first_seen": now,
                    "last_seen": now,
                }
            else:
                if display_name:
                    entry["display_name"] = display_name
                entry["last_seen"] = now
            self.save()

    def list_sources(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return [
                {
                    "chat_id": entry.get("chat_id") or chat_id,
                    "display_name": entry.get("display_name", ""),
                    "kind": entry.get("kind", ""),
                    "first_seen": entry.get("first_seen", 0),
                    "last_seen": entry.get("last_seen", 0),
                }
                for chat_id, entry in sorted(
                    self._sources.items(),
                    key=lambda item: item[1].get("last_seen", 0),
                    reverse=True,
                )
            ]

    def get_display_name(self, chat_id: str) -> Optional[str]:
        with self._lock:
            self._ensure_loaded()
            entry = self._sources.get(str(chat_id))
            if entry:
                return entry.get("display_name") or None
            return None

    def get_source(self, chat_id: str) -> Optional[Dict[str, Any]]:
        """返回单个来源完整登记；不存在时返回 None。"""
        with self._lock:
            self._ensure_loaded()
            entry = self._sources.get(str(chat_id))
            if not entry:
                return None
            return {
                "chat_id": entry.get("chat_id") or str(chat_id),
                "display_name": entry.get("display_name", ""),
                "kind": entry.get("kind", ""),
                "first_seen": entry.get("first_seen", 0),
                "last_seen": entry.get("last_seen", 0),
            }
