"""
AngelHeart 配置迁移模块

在框架加载 _conf_schema.json 之前执行，将旧的扁平配置迁移到新的嵌套 object 结构。
这样框架的 check_config_integrity 运行时，配置文件已经是新格式，旧值不会丢失。
"""

import json
import os
import logging

logger = logging.getLogger(__name__)

# 插件目录名（用于定位配置文件）
_PLUGIN_DIR_NAME = "astrbot_plugin_angel_heart"

# 迁移映射：旧的扁平 key -> (新的 object key, 子 key)
_MIGRATION_MAP = {
    # timing
    "waiting_time": ("timing", "waiting_time"),
    "llm_timeout": ("timing", "llm_timeout"),
    "no_reply_cooldown": ("timing", "no_reply_cooldown"),
    "observation_timeout": ("timing", "observation_timeout"),
    # leave_reply
    "leave_echo_reply": ("leave_reply", "leave_echo_reply"),
    "leave_dense_reply": ("leave_reply", "leave_dense_reply"),
    "echo_detection_threshold": ("leave_reply", "echo_detection_threshold"),
    "echo_detection_window": ("leave_reply", "echo_detection_window"),
    "dense_conversation_threshold": ("leave_reply", "dense_conversation_threshold"),
    "dense_conversation_window": ("leave_reply", "dense_conversation_window"),
    "min_participant_count": ("leave_reply", "min_participant_count"),
    "familiarity_cooldown_duration": ("leave_reply", "familiarity_cooldown_duration"),
    # wake_interaction
    "analysis_on_mention_only": ("wake_interaction", "analysis_on_mention_only"),
    "force_reply_when_summoned": ("wake_interaction", "force_reply_when_summoned"),
    "block_unapproved_wake_non_command": ("wake_interaction", "block_unapproved_wake_non_command"),
    "alias": ("wake_interaction", "alias"),
    "slap_words": ("wake_interaction", "slap_words"),
    "speak_words": ("wake_interaction", "speak_words"),
    "silence_duration": ("wake_interaction", "silence_duration"),
    # access_control
    "whitelist_enabled": ("access_control", "whitelist_enabled"),
    "chat_ids": ("access_control", "chat_ids"),
    "group_chat_enhancement": ("access_control", "group_chat_enhancement"),
    "takeover_private_chat_context": ("access_control", "takeover_private_chat_context"),
    # personality
    "ai_self_identity": ("personality", "ai_self_identity"),
    "reply_strategy_guide": ("personality", "reply_strategy_guide"),
    # context_compression
    "max_conversation_tokens": ("context_compression", "max_conversation_tokens"),
    "context_content_retain_tokens": ("context_compression", "content_retain_tokens"),
    "context_tool_retain_tokens": ("context_compression", "tool_retain_tokens"),
    "context_forgetting_timeout": ("context_compression", "forgetting_timeout"),
    # comfort
    "patience_interval": ("comfort", "patience_interval"),
    "comfort_words": ("comfort", "comfort_words"),
    # tool_decoration
    "tool_decoration_enabled": ("tool_decoration", "tool_decoration_enabled"),
    "tool_decoration_cooldown": ("tool_decoration", "tool_decoration_cooldown"),
    "tool_decorations": ("tool_decoration", "tool_decorations"),
    # debug
    "debug_mode": ("debug", "debug_mode"),
    "strip_markdown_enabled": ("debug", "strip_markdown_enabled"),
}

_DEPRECATED_FLAT_KEYS = {
    "familiarity_timeout",
}

_DEPRECATED_GROUPED_KEYS = {
    "leave_reply": {
        "familiarity_timeout",
    },
}


def _find_config_path() -> str | None:
    """定位插件配置文件路径"""
    # 方式1：通过 AstrBot API 获取
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_config_path
        config_dir = get_astrbot_config_path()
        config_file = os.path.join(config_dir, f"{_PLUGIN_DIR_NAME}_config.json")
        if os.path.exists(config_file):
            return config_file
    except ImportError:
        pass

    # 方式2：从插件目录往上推导 (data/plugins/plugin_name -> data/config/)
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plugins_dir = os.path.dirname(plugin_dir)  # data/plugins/
    data_dir = os.path.dirname(plugins_dir)    # data/
    config_file = os.path.join(data_dir, "config", f"{_PLUGIN_DIR_NAME}_config.json")
    if os.path.exists(config_file):
        return config_file

    return None


def run_migration():
    """
    执行配置迁移：将旧的扁平 key 迁移到新的 object 分组结构。

    此函数应在模块 import 阶段调用，确保在框架加载 schema 之前完成迁移。
    """
    config_path = _find_config_path()
    if not config_path:
        return

    try:
        with open(config_path, encoding="utf-8-sig") as f:
            content = f.read()
            if content.startswith("\ufeff"):
                content = content[1:]
            config = json.loads(content)
    except (json.JSONDecodeError, OSError):
        return

    if not isinstance(config, dict):
        return

    # 检查是否需要迁移（如果已经有 object 分组且没有旧扁平 key，说明已迁移）
    needs_migration = any(key in config for key in _MIGRATION_MAP)
    has_deprecated_keys = any(key in config for key in _DEPRECATED_FLAT_KEYS)
    has_deprecated_grouped_keys = any(
        isinstance(config.get(group_name), dict)
        and any(sub_key in config[group_name] for sub_key in sub_keys)
        for group_name, sub_keys in _DEPRECATED_GROUPED_KEYS.items()
    )
    if not needs_migration and not has_deprecated_keys and not has_deprecated_grouped_keys:
        return

    # 执行迁移
    migrated_count = 0
    removed_count = 0
    for old_key, (group_name, sub_key) in _MIGRATION_MAP.items():
        if old_key not in config:
            continue

        old_value = config[old_key]

        # 确保分组存在
        if group_name not in config:
            config[group_name] = {}
        elif not isinstance(config[group_name], dict):
            continue

        # 只有新位置没有值时才迁移（避免覆盖已有的新格式值）
        if sub_key not in config[group_name]:
            config[group_name][sub_key] = old_value
            migrated_count += 1

        # 删除旧的扁平 key
        del config[old_key]

    for old_key in _DEPRECATED_FLAT_KEYS:
        if old_key in config:
            del config[old_key]
            removed_count += 1

    for group_name, sub_keys in _DEPRECATED_GROUPED_KEYS.items():
        group_config = config.get(group_name)
        if not isinstance(group_config, dict):
            continue
        for sub_key in sub_keys:
            if sub_key in group_config:
                del group_config[sub_key]
                removed_count += 1

    if migrated_count > 0 or removed_count > 0:
        try:
            with open(config_path, "w", encoding="utf-8-sig") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.info(
                f"AngelHeart: 配置迁移完成，{migrated_count} 项已迁移到分组结构，"
                f"{removed_count} 项废弃配置已删除"
            )
        except OSError as e:
            logger.warning(f"AngelHeart: 配置迁移写入失败: {e}")
