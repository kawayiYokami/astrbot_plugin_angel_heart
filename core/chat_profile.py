"""群聊配置模板存储。

模板 = 六类字段的配置集合（助理画像 / 点名与交互 / 离场应答 / 回复长度 / 能量设置 / 回复节奏）。
绑定 = chat_id(unified_msg_origin) -> template_id 映射；未绑定的群聊使用全局配置。

数据持久化到插件数据目录下 chat_profiles.json。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

# 模板字段白名单：只允许六类分组内的键写入，拒绝未知键防脏数据。
TEMPLATE_GROUPS: Dict[str, Dict[str, Any]] = {
    "personality": {
        "ai_self_identity": None,
        "reply_strategy_guide": None,
    },
    "wake_interaction": {
        "enter_on_mention_only": None,
        "force_reply_when_summoned": None,
        "reply_even_not_questioned": None,
        "block_unapproved_wake_non_command": None,
        "alias": None,
        "slap_words": None,
        "speak_words": None,
        "silence_duration": None,
    },
    "leave_reply": {
        "leave_echo_reply": None,
        "leave_dense_reply": None,
        "echo_detection_threshold": None,
        "echo_detection_window": None,
        "dense_conversation_threshold": None,
        "dense_conversation_window": None,
        "min_participant_count": None,
        "familiarity_cooldown_duration": None,
    },
    "reply_length": {
        "focus_instructions": None,
        "normal_reply_max_chars": None,
        "focus_reply_max_chars": None,
    },
    "energy": {
        "initial_energy": None,
        "max_energy": None,
        "min_energy": None,
        "recovery_per_second": None,
        "base_reply_cost": None,
        "reply_cost_per_character": None,
    },
    "timing": {
        "waiting_time": None,
        "assistant_debounce_time": None,
        "secretary_debounce_time": None,
        "accelerate_debounce_time": None,
        "observation_timeout": None,
    },
}

STORE_FILE_NAME = "chat_profiles.json"


def filter_template_config(config: Optional[Dict]) -> Dict:
    """只保留六类分组内的字段，忽略未知键。"""
    result: Dict = {}
    if not isinstance(config, dict):
        return result
    for group, keys in TEMPLATE_GROUPS.items():
        raw_group = config.get(group)
        if not isinstance(raw_group, dict):
            continue
        cleaned = {key: raw_group[key] for key in keys if key in raw_group}
        if cleaned:
            result[group] = cleaned
    return result


class ChatProfileStore:
    """模板与绑定的 JSON 持久化存储。"""

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._file_path = os.path.join(data_dir, STORE_FILE_NAME)
        self._lock = threading.Lock()
        self._templates: Dict[str, Dict] = {}
        self._bindings: Dict[str, str] = {}
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
        templates = data.get("templates", {})
        self._templates = (
            {t["id"]: t for t in templates if isinstance(t, dict) and t.get("id")}
            if isinstance(templates, list)
            else {}
        )
        bindings = data.get("bindings", {})
        self._bindings = (
            {str(k): str(v) for k, v in bindings.items()} if isinstance(bindings, dict) else {}
        )
        self._loaded = True

    def save(self) -> None:
        data = {
            "version": 1,
            "templates": list(self._templates.values()),
            "bindings": self._bindings,
        }
        tmp_path = self._file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self._file_path)

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _new_id() -> str:
        return "tpl_" + uuid.uuid4().hex[:12]

    # ---------- 模板 CRUD ----------

    def list_templates(self) -> List[Dict]:
        with self._lock:
            self._ensure_loaded()
            return [
                {
                    "id": t["id"],
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "created_at": t.get("created_at", 0),
                    "updated_at": t.get("updated_at", 0),
                }
                for t in sorted(
                    self._templates.values(), key=lambda x: x.get("created_at", 0)
                )
            ]

    def get_template(self, template_id: str) -> Optional[Dict]:
        with self._lock:
            self._ensure_loaded()
            template = self._templates.get(template_id)
            if not template:
                return None
            return {
                "id": template["id"],
                "name": template.get("name", ""),
                "description": template.get("description", ""),
                "created_at": template.get("created_at", 0),
                "updated_at": template.get("updated_at", 0),
                "config": template.get("config", {}),
            }

    def get_template_config(self, template_id: str) -> Optional[Dict]:
        template = self.get_template(template_id)
        return template["config"] if template else None

    def create_template(
        self,
        name: str,
        description: str = "",
        config: Optional[Dict] = None,
    ) -> Dict:
        now = self._now()
        template = {
            "id": self._new_id(),
            "name": str(name or "").strip() or "未命名模板",
            "description": str(description or "").strip(),
            "created_at": now,
            "updated_at": now,
            "config": filter_template_config(config),
        }
        with self._lock:
            self._ensure_loaded()
            self._templates[template["id"]] = template
            self.save()
        return template

    def update_template(self, template_id: str, patch: Dict) -> Optional[Dict]:
        with self._lock:
            self._ensure_loaded()
            template = self._templates.get(template_id)
            if not template:
                return None
            if "name" in patch:
                template["name"] = str(patch["name"] or "").strip() or "未命名模板"
            if "description" in patch:
                template["description"] = str(patch["description"] or "").strip()
            if "config" in patch:
                template["config"] = filter_template_config(patch["config"])
            template["updated_at"] = self._now()
            self.save()
            return dict(template)

    def delete_template(self, template_id: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            if template_id not in self._templates:
                return False
            del self._templates[template_id]
            # 级联解绑引用该模板的群聊
            self._bindings = {
                chat_id: tid
                for chat_id, tid in self._bindings.items()
                if tid != template_id
            }
            self.save()
            return True

    # ---------- 绑定 ----------

    def set_binding(self, chat_id: str, template_id: str) -> bool:
        """设置或解除绑定。chat_id 统一存完整 unified_msg_origin；
        若传入纯群号，自动补成可匹配形式（前缀缺省，由 resolve 时后缀匹配兜底）。
        """
        chat_id = str(chat_id or "").strip()
        template_id = str(template_id or "").strip()
        if not chat_id:
            return False
        with self._lock:
            self._ensure_loaded()
            if template_id:
                if template_id not in self._templates:
                    return False
                self._bindings[chat_id] = template_id
            else:
                self._bindings.pop(chat_id, None)
            self.save()
            return True

    def get_binding(self, chat_id: str) -> str:
        with self._lock:
            self._ensure_loaded()
            return self._bindings.get(self._resolve_key(chat_id), "")

    def list_bindings(self) -> List[Dict]:
        with self._lock:
            self._ensure_loaded()
            return [
                {"chat_id": chat_id, "template_id": template_id}
                for chat_id, template_id in sorted(self._bindings.items())
            ]

    # ---------- 覆盖解析 ----------

    @staticmethod
    def _suffix_key(chat_id: str) -> str:
        """取 chat_id 最后一段作为纯群号/QQ号兜底 key。"""
        parts = str(chat_id or "").split(":")
        return parts[-1] if parts else ""

    def _resolve_key(self, chat_id: str) -> str:
        """按绑定 key 匹配，支持双向：完整 origin 与纯群号互查。

        依次尝试：
        1. 查询 key 本身就是绑定 key
        2. 查询 key 的后缀（纯群号）是绑定 key
        3. 遍历绑定 key，其后缀等于查询 key 或查询 key 的后缀
        """
        raw = str(chat_id or "")
        if raw in self._bindings:
            return raw
        suffix = self._suffix_key(raw)
        if suffix and suffix in self._bindings:
            return suffix
        for bound in self._bindings:
            if self._suffix_key(bound) == raw or (
                suffix and self._suffix_key(bound) == suffix
            ):
                return bound
        return raw

    def resolve_override(self, chat_id: str) -> Optional[Dict]:
        """返回该群聊绑定模板的覆盖配置；未绑定或模板缺失时返回 None。

        兼容两种绑定 key：完整 unified_msg_origin（如 aiocqhttp:GroupMessage:1）
        或纯群号（如 1），白名单群通常以后者形式被绑定。
        """
        with self._lock:
            self._ensure_loaded()
            template_id = self._bindings.get(self._resolve_key(chat_id), "")
            if not template_id:
                return None
            template = self._templates.get(template_id)
            if not template:
                return None
            return filter_template_config(template.get("config"))

    # ---------- 工具 ----------

    @staticmethod
    def template_from_global(config_manager) -> Dict:
        """从全局配置提取六类字段，作为新建模板的初始值。"""
        result: Dict = {}
        for group, keys in TEMPLATE_GROUPS.items():
            group_data: Dict = {}
            for key in keys:
                try:
                    value = getattr(config_manager, key)
                except (AttributeError, TypeError):
                    continue
                if value is not None:
                    group_data[key] = value
            if group_data:
                result[group] = group_data
        return result
