"""
AngelHeart 插件 - 配置管理器
用于集中管理插件的所有配置项。
"""


class ConfigManager:
    """
    配置管理器 - 提供对插件配置的中心化访问
    """

    def __init__(self, config_data: dict):
        """
        初始化配置管理器。

        Args:
            config_data (dict): 原始配置字典。
        """
        self._config = config_data or {}

    @property
    def waiting_time(self) -> float:
        """等待时间（秒）- 冷却时间间隔"""
        return self._config.get("waiting_time", 7.0)

    @property
    def no_reply_cooldown(self) -> float:
        """不回复时的冷却时间（秒）"""
        return self._config.get("no_reply_cooldown", 3.0)

    @property
    def cache_expiry(self) -> int:
        """缓存过期时间（秒）"""
        return self._config.get("cache_expiry", 3600)

    @property
    def analyzer_model(self) -> str:
        """用于分析的LLM模型名称"""
        return self._config.get("analyzer_model", "")

    @property
    def reply_strategy_guide(self) -> str:
        """回复策略指导文本"""
        return self._config.get("reply_strategy_guide", "")

    @property
    def whitelist_enabled(self) -> bool:
        """是否启用白名单"""
        return self._config.get("whitelist_enabled", False)

    @property
    def chat_ids(self) -> list:
        """白名单聊天ID列表"""
        return self._config.get("chat_ids", [])

    @property
    def debug_mode(self) -> bool:
        """调试模式开关"""
        return self._config.get("debug_mode", False)

    @property
    def strip_markdown_enabled(self) -> bool:
        """是否启用Markdown清洗"""
        return self._config.get("strip_markdown_enabled", True)

    @property
    def alias(self) -> str:
        """AI助手的昵称"""
        return self._config.get("alias", "AngelHeart")

    @property
    def analysis_on_mention_only(self) -> bool:
        """是否仅在被呼唤时才进行分析"""
        return self._config.get("analysis_on_mention_only", False)

    @property
    def slap_words(self) -> str:
        """用于触发闭嘴的关键词，多个词用'|'分隔"""
        return self._config.get("slap_words", "")

    @property
    def silence_duration(self) -> int:
        """触发闭嘴后的静默时长（秒）"""
        return self._config.get("silence_duration", 600)

    @property
    def group_chat_enhancement(self) -> bool:
        """是否启用群聊上下文增强模式"""
        return self._config.get("group_chat_enhancement", True)

    @property
    def is_reasoning_model(self) -> bool:
        """是否是思维模型（如果是，则直接输出json）"""
        return self._config.get("is_reasoning_model", False)

    @property
    def ai_self_identity(self) -> str:
        """AI的自我身份定位"""
        return self._config.get(
            "ai_self_identity",
            "你拥有强大的检索能力和庞大的知识储备和记忆能力。\n你喜爱跟群友进行语言游戏沟通。\n群友对AI的善意测试你必须回应满足群友的期待，展示你的能力。",
        )

    @property
    def patience_interval(self) -> int:
        """久等间隔（秒）"""
        return self._config.get("patience_interval", 10)

    @property
    def comfort_words(self) -> str:
        """安心词列表，多个词用'|'分隔"""
        return self._config.get("comfort_words", "嗯嗯|我在|别急")

    # ========== 4状态机制新增配置 ==========

    @property
    def echo_detection_threshold(self) -> int:
        """
        复读检测阈值：连续多少条相同消息触发混脸熟

        Returns:
            int: 阈值，默认3条
        """
        return self._config.get("echo_detection_threshold", 3)

    @property
    def dense_conversation_threshold(self) -> int:
        """
        密集发言阈值：10分钟内多少条消息触发混脸熟

        Returns:
            int: 阈值，默认30条
        """
        return self._config.get("dense_conversation_threshold", 30)

    @property
    def familiarity_timeout(self) -> int:
        """
        混脸熟超时时间：多长时间无活动自动降级（秒）

        Returns:
            int: 超时时间，默认600秒（10分钟）
        """
        return self._config.get("familiarity_timeout", 600)

    @property
    def familiarity_cooldown_duration(self) -> int:
        """
        混脸熟冷却时间：混脸熟状态结束后多久才能再次触发（秒）

        Returns:
            int: 冷却时间，默认1800秒（30分钟）
        """
        return self._config.get("familiarity_cooldown_duration", 1800)

    @property
    def observation_timeout(self) -> int:
        """
        观测中超时时间：多长时间无活动自动降级（秒）

        Returns:
            int: 超时时间，默认600秒（10分钟）
        """
        return self._config.get("observation_timeout", 600)

    @property
    def echo_detection_window(self) -> int:
        """
        复读检测时间窗口：多长时间内的消息算作复读（秒）

        Returns:
            int: 时间窗口，默认30秒
        """
        return self._config.get("echo_detection_window", 30)

    @property
    def dense_conversation_window(self) -> int:
        """
        密集发言检测时间窗口：多长时间内的消息算作密集（秒）

        Returns:
            int: 时间窗口，默认600秒（10分钟）
        """
        return self._config.get("dense_conversation_window", 600)

    @property
    def min_participant_count(self) -> int:
        """
        密集发言最小参与人数：至少多少不同的人参与才算密集

        Returns:
            int: 最小参与人数，默认5人
        """
        return self._config.get("min_participant_count", 5)

    @property
    def leave_echo_reply(self) -> bool:
        """
        离场应答-复读模式开关

        Returns:
            bool: 是否启用离场时复读应答，默认False
        """
        return self._config.get("leave_echo_reply", False)

    @property
    def leave_dense_reply(self) -> bool:
        """
        离场应答-密集对话参与开关

        Returns:
            bool: 是否启用离场时参与密集对话应答，默认False
        """
        return self._config.get("leave_dense_reply", False)

    @property
    def max_conversation_tokens(self) -> int:
        """当单个会话的估算Token数超过此限制时，触发清理。0为禁用。"""
        return self._config.get("max_conversation_tokens", 100000)

    @property
    def tool_decoration_enabled(self) -> bool:
        """是否启用工具修饰"""
        return self._config.get("tool_decoration_enabled", False)

    @property
    def tool_decoration_cooldown(self) -> float:
        """工具修饰冷却时间（秒）"""
        return self._config.get("tool_decoration_cooldown", 7.0)

    @property
    def tool_decorations(self) -> dict:
        """工具修饰语配置字典"""
        import json

        decorations = self._config.get("tool_decorations", "{}")

        # 如果已经是字典，直接返回
        if isinstance(decorations, dict):
            return decorations

        # 如果是字符串，尝试解析为JSON
        if isinstance(decorations, str):
            try:
                return json.loads(decorations)
            except json.JSONDecodeError:
                # JSON解析失败，返回空字典
                return {}

        return {}

    def get_config_summary(self) -> dict:
        """
        获取配置摘要，用于调试和监控

        Returns:
            dict: 配置摘要
        """
        return {
            "basic": {
                "waiting_time": self.waiting_time,
                "cache_expiry": self.cache_expiry,
                "max_conversation_tokens": self.max_conversation_tokens,
                "alias": self.alias,
                "analysis_on_mention_only": self.analysis_on_mention_only,
                "comfort_words": self.comfort_words,
                "slap_words": self.slap_words,
                "silence_duration": self.silence_duration,
            },
            "status_mechanism": {
                "echo_detection_threshold": self.echo_detection_threshold,
                "dense_conversation_threshold": self.dense_conversation_threshold,
                "familiarity_timeout": self.familiarity_timeout,
                "observation_timeout": self.observation_timeout,
                "familiarity_cooldown_duration": self.familiarity_cooldown_duration,
                "leave_echo_reply": self.leave_echo_reply,
                "leave_dense_reply": self.leave_dense_reply,
            },
            "detection_windows": {
                "echo_detection_window": self.echo_detection_window,
                "dense_conversation_window": self.dense_conversation_window,
                "min_participant_count": self.min_participant_count,
            },
        }
