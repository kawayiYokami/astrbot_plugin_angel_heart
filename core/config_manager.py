"""
AngelHeart 插件 - 配置管理器
用于集中管理插件的所有配置项。
支持新版嵌套 object 结构，兼容旧版扁平结构读取。
"""


class ConfigManager:
    """
    配置管理器 - 提供对插件配置的中心化访问。

    配置格式（新版）：
    {
        "analyzer_model": "...",
        "timing": {"waiting_time": 30.0, ...},
        "leave_reply": {"leave_echo_reply": false, ...},
        ...
    }
    """

    def __init__(self, config_data: dict):
        self._config = config_data or {}

    def _get_grouped(self, group: str, key: str, default=None):
        """从分组中读取配置，兼容旧的扁平格式"""
        # 优先从新的嵌套结构读取
        grp = self._config.get(group)
        if isinstance(grp, dict) and key in grp:
            return grp[key]
        # 回退到旧的扁平 key
        return self._config.get(key, default)

    # ========== 顶层配置 ==========

    @property
    def analyzer_model(self) -> str:
        return self._config.get("analyzer_model", "")

    @property
    def image_caption_provider_id(self) -> str:
        return self._config.get("image_caption_provider_id", "")

    @property
    def is_reasoning_model(self) -> bool:
        return self._config.get("is_reasoning_model", False)

    # ========== timing ==========

    @property
    def waiting_time(self) -> float:
        return self._get_grouped("timing", "waiting_time", 30.0)

    @property
    def assistant_debounce_time(self) -> float:
        """助理防抖等待时长（秒）。"""
        return self._get_grouped("timing", "assistant_debounce_time", 1.0)

    @property
    def secretary_debounce_time(self) -> float:
        """前台巡检最长等待时长（秒）。默认复用 waiting_time。"""
        return self._get_grouped("timing", "secretary_debounce_time", self.waiting_time)

    @property
    def accelerate_debounce_time(self) -> float:
        """加速防抖等待时长（秒）。"""
        return self._get_grouped("timing", "accelerate_debounce_time", 1.0)

    @property
    def no_reply_cooldown(self) -> float:
        return self._get_grouped("timing", "no_reply_cooldown", 3.0)

    @property
    def observation_timeout(self) -> int:
        return self._get_grouped("timing", "observation_timeout", 60)

    # ========== energy ==========

    @property
    def initial_energy(self) -> float:
        return self._get_grouped("energy", "initial_energy", 100.0)

    @property
    def max_energy(self) -> float:
        return self._get_grouped("energy", "max_energy", 100.0)

    @property
    def min_energy(self) -> float:
        return self._get_grouped("energy", "min_energy", -100.0)

    @property
    def recovery_per_second(self) -> float:
        return self._get_grouped("energy", "recovery_per_second", 0.6)

    @property
    def base_reply_cost(self) -> float:
        return self._get_grouped("energy", "base_reply_cost", 14.0)

    @property
    def reply_cost_per_character(self) -> float:
        return self._get_grouped("energy", "reply_cost_per_character", 0.12)

    # ========== leave_reply ==========

    @property
    def leave_echo_reply(self) -> bool:
        return self._get_grouped("leave_reply", "leave_echo_reply", False)

    @property
    def leave_dense_reply(self) -> bool:
        return self._get_grouped("leave_reply", "leave_dense_reply", False)

    @property
    def echo_detection_threshold(self) -> int:
        return self._get_grouped("leave_reply", "echo_detection_threshold", 3)

    @property
    def echo_detection_window(self) -> int:
        return self._get_grouped("leave_reply", "echo_detection_window", 30)

    @property
    def dense_conversation_threshold(self) -> int:
        return self._get_grouped("leave_reply", "dense_conversation_threshold", 30)

    @property
    def dense_conversation_window(self) -> int:
        return self._get_grouped("leave_reply", "dense_conversation_window", 600)

    @property
    def min_participant_count(self) -> int:
        return self._get_grouped("leave_reply", "min_participant_count", 5)

    @property
    def leave_reply_cooldown_duration(self) -> int:
        return self._get_grouped("leave_reply", "familiarity_cooldown_duration", 1800)

    # ========== wake_interaction ==========

    @property
    def analysis_on_mention_only(self) -> bool:
        return self._get_grouped("wake_interaction", "analysis_on_mention_only", False)

    @property
    def force_reply_when_summoned(self) -> bool:
        return self._get_grouped("wake_interaction", "force_reply_when_summoned", True)

    @property
    def reply_even_not_questioned(self) -> bool:
        return self._get_grouped("wake_interaction", "reply_even_not_questioned", True)

    @property
    def block_unapproved_wake_non_command(self) -> bool:
        return self._get_grouped("wake_interaction", "block_unapproved_wake_non_command", False)

    @property
    def alias(self) -> str:
        return self._get_grouped("wake_interaction", "alias", "AngelHeart")

    @property
    def slap_words(self) -> str:
        return self._get_grouped("wake_interaction", "slap_words", "")

    @property
    def speak_words(self) -> str:
        return self._get_grouped("wake_interaction", "speak_words", "张嘴")

    @property
    def silence_duration(self) -> int:
        return self._get_grouped("wake_interaction", "silence_duration", 600)

    # ========== access_control ==========

    @property
    def whitelist_enabled(self) -> bool:
        return self._get_grouped("access_control", "whitelist_enabled", False)

    @property
    def chat_ids(self) -> list:
        return self._get_grouped("access_control", "chat_ids", [])

    @property
    def group_chat_enhancement(self) -> bool:
        return self._get_grouped("access_control", "group_chat_enhancement", True)

    @property
    def takeover_private_chat_context(self) -> bool:
        return self._get_grouped("access_control", "takeover_private_chat_context", False)

    # ========== personality ==========

    @property
    def ai_self_identity(self) -> str:
        return self._get_grouped(
            "personality", "ai_self_identity",
            "你拥有强大的检索能力和庞大的知识储备和记忆能力。\n你喜爱跟群友进行语言游戏沟通。\n群友对AI的善意测试你必须回应满足群友的期待，展示你的能力。",
        )

    @property
    def reply_strategy_guide(self) -> str:
        return self._get_grouped("personality", "reply_strategy_guide", "")

    # ========== context_compression ==========

    @property
    def max_conversation_tokens(self) -> int:
        return self._get_grouped("context_compression", "max_conversation_tokens", 100000)

    @property
    def context_compression_threshold(self) -> float:
        return self._get_grouped("context_compression", "context_compression_threshold", 0.82)

    @property
    def context_content_retain_tokens(self) -> int:
        # 新 key: content_retain_tokens; 旧 key: context_content_retain_tokens
        grp = self._config.get("context_compression")
        if isinstance(grp, dict) and "content_retain_tokens" in grp:
            return grp["content_retain_tokens"]
        return self._config.get("context_content_retain_tokens", 10000)

    @property
    def context_tool_retain_tokens(self) -> int:
        grp = self._config.get("context_compression")
        if isinstance(grp, dict) and "tool_retain_tokens" in grp:
            return grp["tool_retain_tokens"]
        return self._config.get("context_tool_retain_tokens", 10000)

    @property
    def context_forgetting_timeout(self) -> int:
        grp = self._config.get("context_compression")
        if isinstance(grp, dict) and "forgetting_timeout" in grp:
            return grp["forgetting_timeout"]
        return self._config.get("context_forgetting_timeout", 86400)

    # ========== debug ==========

    @property
    def debug_mode(self) -> bool:
        return self._get_grouped("debug", "debug_mode", False)

    @property
    def strip_markdown_enabled(self) -> bool:
        return self._get_grouped("debug", "strip_markdown_enabled", True)

    # ========== 工具方法 ==========

    def get_config_summary(self) -> dict:
        return {
            "timing": {
                "waiting_time": self.waiting_time,
                "assistant_debounce_time": self.assistant_debounce_time,
                "secretary_debounce_time": self.secretary_debounce_time,
                "accelerate_debounce_time": self.accelerate_debounce_time,
                "cache_expiry": self.cache_expiry,
                "observation_timeout": self.observation_timeout,
            },
            "context_compression": {
                "max_conversation_tokens": self.max_conversation_tokens,
                "context_content_retain_tokens": self.context_content_retain_tokens,
                "context_tool_retain_tokens": self.context_tool_retain_tokens,
                "context_forgetting_timeout": self.context_forgetting_timeout,
            },
            "wake_interaction": {
                "alias": self.alias,
                "analysis_on_mention_only": self.analysis_on_mention_only,
                "force_reply_when_summoned": self.force_reply_when_summoned,
            },
            "access_control": {
                "whitelist_enabled": self.whitelist_enabled,
                "group_chat_enhancement": self.group_chat_enhancement,
            },
        }
