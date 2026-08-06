"""每群最近一次秘书决策存储。

记录每个会话最近一次秘书决策（是否回复 + 策略摘要 + 决策时刻），
供 WebUI 右侧群聊状态栏展示。只保留每群最近一条，不存历史。
"""

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

STORE_FILE_NAME = "last_decisions.json"


class LastDecisionStore:
    """最近决策的 JSON 持久化存储（每群 1 条）。"""

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._file_path = os.path.join(data_dir, STORE_FILE_NAME)
        self._lock = threading.Lock()
        self._decisions: Dict[str, Dict[str, Any]] = {}
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
        decisions = data.get("decisions", {})
        if isinstance(decisions, dict):
            self._decisions = {
                str(k): v for k, v in decisions.items() if isinstance(v, dict)
            }
        self._loaded = True

    def save(self) -> None:
        data = {"version": 1, "decisions": self._decisions}
        tmp_path = self._file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._file_path)

    # ---------- 读写 ----------

    def record(self, chat_id: str, should_reply: bool, summary: str) -> None:
        """记录一次决策，覆盖该群最近一条。"""
        chat_id = str(chat_id or "")
        if not chat_id:
            return
        with self._lock:
            self._ensure_loaded()
            self._decisions[chat_id] = {
                "chat_id": chat_id,
                "decided_at": time.time(),
                "should_reply": bool(should_reply),
                "summary": str(summary or ""),
            }
            self.save()

    def get(self, chat_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            entry = self._decisions.get(str(chat_id))
            if not entry:
                return None
            return {
                "chat_id": entry.get("chat_id") or str(chat_id),
                "decided_at": entry.get("decided_at", 0),
                "should_reply": bool(entry.get("should_reply", False)),
                "summary": entry.get("summary", ""),
            }

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return [
                {
                    "chat_id": entry.get("chat_id") or chat_id,
                    "decided_at": entry.get("decided_at", 0),
                    "should_reply": bool(entry.get("should_reply", False)),
                    "summary": entry.get("summary", ""),
                }
                for chat_id, entry in sorted(
                    self._decisions.items(),
                    key=lambda item: item[1].get("decided_at", 0),
                    reverse=True,
                )
            ]
