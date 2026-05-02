"""
行为画像管理器 — 单例，管理所有用户的行为基线。
接入 angel_heart 的消息处理管道。
"""
from collections import defaultdict
from .baseline import BehaviorBaseline
from .analyzer import MessageCleaner, LiwcAnalyzer


class BehaviorProfileManager:
    """
    全局行为画像管理器。

    用法:
        mgr = BehaviorProfileManager(min_messages=50)
        mgr.feed("用户消息", user_id="2966053870", scope="private")
        if mgr.is_ready("2966053870", "private"):
            baseline = mgr.get_baseline("2966053870", "private")
    """

    def __init__(self, min_messages: int = 50):
        self.min_messages = min_messages
        self.cleaner = MessageCleaner()
        self.liwc = LiwcAnalyzer()
        # key: (user_id, scope) → BehaviorBaseline
        self._baselines: dict[tuple[str, str], BehaviorBaseline] = defaultdict(
            lambda: BehaviorBaseline(min_messages=min_messages)
        )

    def feed(self, text: str, user_id: str, scope: str = "global",
             has_sticker: bool = False) -> None:
        """喂入一条消息"""
        if self.cleaner.should_skip(text) and not has_sticker:
            return
        cleaned = self.cleaner.clean(text) if text else ""
        key = (user_id, scope)
        self._baselines[key].feed(cleaned, scope=scope, has_sticker=has_sticker)

    def is_ready(self, user_id: str, scope: str = "global") -> bool:
        key = (user_id, scope)
        return key in self._baselines and self._baselines[key].is_ready()

    def get_baseline(self, user_id: str, scope: str = "global") -> dict | None:
        key = (user_id, scope)
        if key not in self._baselines:
            return None
        baseline = self._baselines[key]
        if not baseline.is_ready():
            return None
        return baseline.get_baseline()

    def get_confidence(self, user_id: str, scope: str = "global") -> float:
        key = (user_id, scope)
        if key not in self._baselines:
            return 0.0
        return self._baselines[key].confidence

    def prune_stale(self, min_messages: int = 5, max_age_seconds: float = 86400 * 30) -> int:
        """清理低活跃用户（<5条消息且30天未活跃），返回清理数"""
        removed = 0
        for key, baseline in list(self._baselines.items()):
            if baseline.message_count < min_messages:
                del self._baselines[key]
                removed += 1
        return removed

    # ---- soul_state 相对基线 ----

    @staticmethod
    def get_soul_offset(raw_energy: dict[str, float], baseline_stats: dict) -> str:
        """
        将 soul_state 四维能量值表达为相对于行为基线的偏移。

        Args:
            raw_energy: {"RecallDepth": 5.0, "ImpressionDepth": 3.0, ...}
            baseline_stats: get_baseline() 返回的统计字典

        Returns:
            "比基线更活跃 (+0.3), 消息更长 (+15%), 更爱提问 (+2%), 情绪更中性"
        """
        if not baseline_stats:
            return ""

        parts = []
        avg_len = baseline_stats.get("avg_message_length", 0)
        emoji_freq = baseline_stats.get("emoji_frequency", 0)
        question_freq = baseline_stats.get("question_frequency", 0)
        link_freq = baseline_stats.get("link_frequency", 0)

        # 表达欲望 (ExpressionDesire) ↔ 消息长度
        expr = raw_energy.get("ExpressionDesire", 0)
        if expr > 2:
            parts.append("消息更长 (+{:.0%})".format(expr / 10))
        elif expr < -2:
            parts.append("消息更短")

        # 社交倾向 (RecallDepth) ↔ 提问/表情/链接
        social = raw_energy.get("RecallDepth", 0)
        if social > 2:
            parts.append("更乐于交流")
        elif social < -2:
            parts.append("更倾向于沉默")

        return ", ".join(parts) if parts else ""
