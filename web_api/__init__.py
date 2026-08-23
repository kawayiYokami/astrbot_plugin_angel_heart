"""WebUI 后端 API：群聊配置模板与绑定管理。

通过 AstrBot 的 register_web_api 机制注册（/api/plug/...）。
优先使用新版 astrbot.api.web（4.27+）；旧版回退 quart request/jsonify。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from astrbot.api.web import error_response, json_response, request
    HAS_WEB_API = True
except ImportError:  # 兼容旧版 AstrBot（无 astrbot.api.web）
    from quart import jsonify, request  # type: ignore

    HAS_WEB_API = False


def _ok(data: Any = None, message: str = "") -> Any:
    if HAS_WEB_API:
        return json_response({"status": "ok", "message": message, "data": data})
    return jsonify({"status": "ok", "message": message, "data": data})


def _err(message: str, status_code: int = 400) -> Any:
    if HAS_WEB_API:
        return error_response(message, status_code=status_code)
    resp = jsonify({"status": "error", "message": message, "data": {}})
    resp.status_code = status_code
    return resp


async def _get_json() -> Dict:
    """读取请求体 JSON，兼容新版 astrbot.api.web 与 quart 两种形态。"""
    if HAS_WEB_API:
        data = await request.json(default=None)
    else:
        # quart: request.json 是 async property，非 JSON 时抛异常
        try:
            data = await request.json
        except Exception:
            data = None
    return data if isinstance(data, dict) else {}


class ProfileAPI:
    """模板 CRUD 与群聊绑定 API。"""

    def __init__(
        self,
        profile_store,
        config_manager,
        conversation_ledger,
        chat_sources=None,
        status_transition_manager=None,
        debounce_manager=None,
        last_decisions=None,
    ):
        self.profile_store = profile_store
        self.config_manager = config_manager
        self.conversation_ledger = conversation_ledger
        self.chat_sources = chat_sources
        self.status_transition_manager = status_transition_manager
        self.debounce_manager = debounce_manager
        self.last_decisions = last_decisions

    # ---------- 模板 ----------

    async def list_profiles(self):
        """模板列表 + 绑定总览（模板含完整 config）。

        bindings 以 {chat_id: template_id} 字典返回（前端契约）；
        需要列表形态请用 /bindings 接口。
        """
        templates = [
            t
            for t in (
                self.profile_store.get_template(item["id"])
                for item in self.profile_store.list_templates()
            )
            if t is not None
        ]
        bindings = {
            item["chat_id"]: item["template_id"]
            for item in self.profile_store.list_bindings()
        }
        return _ok(
            {
                "templates": templates,
                "bindings": bindings,
                "global_config": self.profile_store.template_from_global(
                    self.config_manager
                ),
            }
        )

    async def create_profile(self):
        """新建模板；config 缺省时按 from_global 决定是否预填全局六类配置。"""
        payload = await _get_json()
        if not isinstance(payload, dict):
            return _err("请求体必须是 JSON 对象")
        name = str(payload.get("name") or "").strip()
        if not name:
            return _err("模板名称不能为空")
        description = str(payload.get("description") or "").strip()
        config = payload.get("config")
        if not isinstance(config, dict) or not config:
            if payload.get("from_global", False):
                config = self.profile_store.template_from_global(self.config_manager)
            else:
                config = {}
        template = self.profile_store.create_template(name, description, config)
        return _ok(template, "模板已创建")

    async def update_profile(self):
        """更新模板（名称/描述/配置）。"""
        payload = await _get_json()
        if not isinstance(payload, dict):
            return _err("请求体必须是 JSON 对象")
        template_id = str(payload.get("id") or "").strip()
        if not template_id:
            return _err("缺少模板 id")
        patch: Dict[str, Any] = {}
        if "name" in payload:
            patch["name"] = str(payload.get("name") or "").strip()
        if "description" in payload:
            patch["description"] = str(payload.get("description") or "").strip()
        if "config" in payload:
            cfg = payload.get("config")
            if not isinstance(cfg, dict):
                return _err("config 必须是 JSON 对象")
            patch["config"] = cfg
        if not patch:
            return _err("没有可更新的字段")
        template = self.profile_store.update_template(template_id, patch)
        if template is None:
            return _err("模板不存在", 404)
        return _ok(template, "模板已更新")

    async def delete_profile(self):
        """删除模板；引用它的群聊自动解绑回通用配置。"""
        payload = await _get_json()
        if not isinstance(payload, dict):
            return _err("请求体必须是 JSON 对象")
        template_id = str(payload.get("id") or "").strip()
        if not template_id:
            return _err("缺少模板 id")
        if not self.profile_store.delete_template(template_id):
            return _err("模板不存在", 404)
        return _ok(None, "模板已删除")

    # ---------- 绑定 ----------

    async def list_bindings(self):
        """全部群聊绑定。"""
        bindings = self.profile_store.list_bindings()
        return _ok(bindings)

    async def set_binding(self):
        """设置或解除群聊绑定；template_id 为空字符串表示解绑。"""
        payload = await _get_json()
        if not isinstance(payload, dict):
            return _err("请求体必须是 JSON 对象")
        chat_id = str(payload.get("chat_id") or "").strip()
        if not chat_id:
            return _err("缺少 chat_id")
        template_id = str(payload.get("template_id") or "").strip()
        if not self.profile_store.set_binding(chat_id, template_id):
            return _err("模板不存在，绑定失败", 404)
        return _ok(None, "已解除绑定" if not template_id else "绑定已生效")

    # ---------- 群聊 ----------

    def _known_chats(self) -> List[Dict]:
        """合并来源/ledger/白名单并去重，返回 chat 条目列表（无 Response 包装）。"""
        known: List[str] = []
        try:
            if self.chat_sources is not None:
                known.extend(
                    item["chat_id"] for item in self.chat_sources.list_sources()
                )
        except Exception:
            logger.debug("AngelHeart: 读取来源登记失败", exc_info=True)
        try:
            known.extend(self.conversation_ledger.get_all_chat_ids() or [])
        except Exception:
            logger.debug("AngelHeart: 读取 ledger 会话列表失败", exc_info=True)
        try:
            known.extend(self.config_manager.chat_ids or [])
        except Exception:
            logger.debug("AngelHeart: 读取白名单群聊失败", exc_info=True)
        seen = set()
        chats = []
        for raw in known:
            chat_id = str(raw or "").strip()
            if not chat_id:
                continue
            suffix = chat_id.split(":")[-1] if ":" in chat_id else chat_id
            if suffix in seen:
                continue
            seen.add(suffix)
            display_name = ""
            kind = ""
            if self.chat_sources is not None:
                try:
                    entry = self.chat_sources.get_source(chat_id)
                    if entry:
                        display_name = entry.get("display_name", "")
                        kind = entry.get("kind", "")
                except Exception:
                    logger.debug("AngelHeart: 读取来源详情失败", exc_info=True)
            chats.append(
                {
                    "chat_id": chat_id,
                    "display_name": display_name,
                    "kind": kind,
                    "template_id": self.profile_store.get_binding(chat_id),
                }
            )
        chats.sort(key=lambda item: item["chat_id"])
        return chats

    async def list_chats(self):
        """已知群聊列表：来源登记 + 活跃会话 + 白名单 ID，去重，带显示名。

        同一会话可能出现两种 key 形态：完整 unified_msg_origin（来源登记/ledger）
        与纯群号（白名单）。用后缀（纯群号）做去重键，来源登记/ledger 先出现，
        优先保留带显示名的完整 origin 条目。
        """
        return _ok(self._known_chats())

    async def list_chat_sources(self):
        """来源登记表：所有见过的群聊/私聊及其显示名与时间。"""
        if self.chat_sources is None:
            return _ok([])
        try:
            return _ok(self.chat_sources.list_sources())
        except Exception as e:
            return _err(f"读取来源登记失败: {e}")

    # ---------- 群聊状态仪表盘 ----------

    async def chat_status(self):
        """群聊状态仪表盘：状态 + 能量 + 巡检 + 最近决策 + 绑定，按来源合并。"""
        items = []
        for chat in self._known_chats():
            chat_id = chat["chat_id"]
            # 降级时给完整默认形状，与前端 ChatStatusItem.status 必填契约一致
            status = {
                "current_status": "Unknown",
                "duration_seconds": 0,
                "duration_minutes": 0,
                "has_assistant_debounce": False,
                "has_secretary_debounce": False,
            }
            if self.status_transition_manager is not None:
                try:
                    status = self.status_transition_manager.get_status_summary(chat_id)
                    # 展示层在场超时推断：真实状态只在收到新消息时才做超时检查，
                    # 群聊安静后内存状态会停在「在场」，UI 轮询读到过期状态。
                    # 这里按 observation_timeout 推断为离场展示，不改真实状态机。
                    if status.get("current_status") == "OBSERVATION":
                        get_start = getattr(
                            self.status_transition_manager,
                            "get_status_start_time",
                            None,
                        )
                        if get_start is not None:
                            start = get_start(chat_id)
                            if start:
                                timeout = self.config_manager.for_chat(
                                    chat_id
                                ).observation_timeout
                                if time.time() - start >= timeout:
                                    status = dict(status)
                                    status["current_status"] = "NOT_PRESENT"
                except Exception:
                    pass
            energy = None
            if self.debounce_manager is not None:
                try:
                    energy = self.debounce_manager.get_chat_energy(chat_id)
                except Exception:
                    pass
            patrol = {"waiting": "", "remaining": 0.0, "total": 0.0}
            if self.debounce_manager is not None:
                try:
                    patrol = await self.debounce_manager.patrol_snapshot(chat_id)
                except Exception:
                    pass
            decision = None
            if self.last_decisions is not None:
                try:
                    decision = self.last_decisions.get(chat_id)
                except Exception:
                    pass
            items.append(
                {
                    "chat_id": chat_id,
                    "display_name": chat.get("display_name", ""),
                    "kind": chat.get("kind", ""),
                    "template_id": chat.get("template_id", ""),
                    "status": status,
                    "energy": energy,
                    "patrol": patrol,
                    "last_decision": decision,
                }
            )
        return _ok(items)


def register_all_routes(
    context,
    profile_store,
    config_manager,
    conversation_ledger,
    chat_sources=None,
    status_transition_manager=None,
    debounce_manager=None,
    last_decisions=None,
    config=None,
    plugin=None,
) -> None:
    """注册全部 WebUI API 路由。"""
    api = ProfileAPI(
        profile_store,
        config_manager,
        conversation_ledger,
        chat_sources,
        status_transition_manager,
        debounce_manager,
        last_decisions,
    )

    routes = [
        ("/astrbot_plugin_angel_heart/profiles", api.list_profiles, ["GET"], "模板列表与绑定总览"),
        ("/astrbot_plugin_angel_heart/profiles/create", api.create_profile, ["POST"], "新建配置模板"),
        ("/astrbot_plugin_angel_heart/profiles/update", api.update_profile, ["POST"], "更新配置模板"),
        ("/astrbot_plugin_angel_heart/profiles/delete", api.delete_profile, ["POST"], "删除配置模板"),
        ("/astrbot_plugin_angel_heart/bindings", api.list_bindings, ["GET"], "群聊绑定列表"),
        ("/astrbot_plugin_angel_heart/bindings/set", api.set_binding, ["POST"], "设置/解除群聊绑定"),
        ("/astrbot_plugin_angel_heart/chats", api.list_chats, ["GET"], "已知群聊列表"),
        ("/astrbot_plugin_angel_heart/chat_sources", api.list_chat_sources, ["GET"], "来源登记列表"),
        ("/astrbot_plugin_angel_heart/chat_status", api.chat_status, ["GET"], "群聊状态仪表盘"),
    ]

    if config is not None and plugin is not None:
        plugin_config_api = PluginConfigAPI(config, plugin)
        routes.append(
            (
                "/astrbot_plugin_angel_heart/plugin_config",
                plugin_config_api.get_config,
                ["GET"],
                "插件全局配置（schema + 当前值 + provider 列表）",
            )
        )
        routes.append(
            (
                "/astrbot_plugin_angel_heart/plugin_config/save",
                plugin_config_api.save_config,
                ["POST"],
                "保存插件全局配置并即时生效",
            )
        )

    for path, handler, methods, description in routes:
        context.register_web_api(path, handler, methods, description)


# ========== 插件全局配置（_conf_schema.json）==========

# schema type -> 校验/转换规则；bool 必须先于 int 判断（bool 是 int 子类）
_SCHEMA_TYPES = {"string", "text", "int", "float", "bool", "list", "object"}


def _convert_value(value, meta, path: str) -> tuple[list[str], Any]:
    """按 schema 元数据校验并转换单个值，返回 (errors, converted)。"""
    t = meta.get("type")
    if t not in _SCHEMA_TYPES:
        return [], value
    if t == "object":
        items = meta.get("items", {})
        if not isinstance(value, dict):
            return [f"{path} 应为对象"], value
        errors: list[str] = []
        converted: dict = {}
        for key, sub_meta in items.items():
            if key not in value:
                continue
            sub_errors, sub_value = _convert_value(value[key], sub_meta, f"{path}.{key}")
            errors.extend(sub_errors)
            converted[key] = sub_value
        return errors, converted
    if t == "bool":
        if not isinstance(value, bool):
            return [f"{path} 应为布尔值"], value
        return [], value
    if t == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, float) and value.is_integer():
                return [], int(value)
            return [f"{path} 应为整数"], value
        return [], value
    if t == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return [f"{path} 应为数字"], value
        return [], float(value)
    if t == "list":
        if not isinstance(value, list):
            return [f"{path} 应为列表"], value
        return [], value
    # string / text
    if not isinstance(value, str):
        return [f"{path} 应为字符串"], value
    return [], value


class PluginConfigAPI:
    """插件全局配置（_conf_schema.json）读取与保存。

    数据源是宿主的 AstrBotConfig 对象（dict 子类）。保存时原地更新键值，
    ConfigManager 等运行时组件持有同一引用，动态读取即时生效，无需重载插件；
    与原生 dashboard 配置页共存，互为同一份数据的两个编辑入口。
    """

    def __init__(self, config, plugin):
        self._config = config
        self._plugin = plugin
        self._schema = getattr(config, "schema", None) or {}

    def get_config(self):
        providers: Dict[str, List[str]] = {"chat": [], "embedding": [], "rerank": []}
        try:
            for p in self._plugin.context.get_all_providers():
                providers["chat"].append(p.meta().id)
            for p in self._plugin.context.get_all_embedding_providers():
                providers["embedding"].append(p.meta().id)
            rerank_getter = getattr(self._plugin.context, "get_all_rerank_providers", None)
            if callable(rerank_getter):
                for p in rerank_getter():
                    providers["rerank"].append(p.meta().id)
        except Exception as e:
            logger.warning(f"AngelHeart: 获取 provider 列表失败: {e}")

        return _ok(
            {
                "schema": self._schema,
                "values": dict(self._config),
                "providers": providers,
            }
        )

    async def save_config(self):
        payload = await _get_json()
        values = payload.get("values")
        if not isinstance(values, dict):
            return _err("values 必须是 JSON 对象")

        errors: list[str] = []
        converted: Dict[str, Any] = {}
        for key, value in values.items():
            meta = self._schema.get(key)
            if not isinstance(meta, dict):
                errors.append(f"未知配置项: {key}")
                continue
            key_errors, key_value = _convert_value(value, meta, key)
            errors.extend(key_errors)
            converted[key] = key_value
        if errors:
            return _err("；".join(errors))

        # 原地更新顶层键：保持 dict 引用，运行时组件动态读取即时生效
        for key, value in converted.items():
            self._config[key] = value
        try:
            self._config.save_config()
        except Exception as e:
            logger.error(f"AngelHeart: 配置落盘失败: {e}", exc_info=True)
            return _err(f"配置落盘失败: {e}")

        hook = getattr(self._plugin, "on_config_saved", None)
        if callable(hook):
            try:
                hook()
            except Exception as e:
                logger.error(f"AngelHeart: 配置热生效钩子失败: {e}", exc_info=True)
                return _err(f"配置已保存，但热生效刷新失败: {e}")

        return _ok(message="已保存并即时生效")
