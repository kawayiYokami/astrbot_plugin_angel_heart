"""
行为画像管理器 — 管理所有用户的行为基线。线程安全。
接入 angel_heart 的消息处理管道。
"""
import time
import threading
from collections import defaultdict
from .baseline import BehaviorBaseline
from .analyzer import MessageCleaner, LiwcAnalyzer


class BehaviorProfileManager:
    """全局行为画像管理器。线程安全。"""

    def __init__(self, min_messages: int = 50):
        self.min_messages = min_messages
        self._lock = threading.RLock()
        self.cleaner = MessageCleaner()
        self.liwc = LiwcAnalyzer()
        self._baselines: dict[tuple[str, str], BehaviorBaseline] = {}

    def _get_or_create(self, key: tuple[str, str]) -> BehaviorBaseline:
        if key not in self._baselines:
            self._baselines[key] = BehaviorBaseline(min_messages=self.min_messages)
        return self._baselines[key]

    def feed(self, text: str, user_id: str, scope: str = "global",
             has_sticker: bool = False) -> None:
        if self.cleaner.should_skip(text) and not has_sticker:
            return
        cleaned = self.cleaner.clean(text) if text else ""
        key = (user_id, scope)
        with self._lock:
            bl = self._get_or_create(key)
        bl.feed(cleaned, scope=scope, has_sticker=has_sticker)

    def is_ready(self, user_id: str, scope: str = "global") -> bool:
        key = (user_id, scope)
        with self._lock:
            if key not in self._baselines:
                return False
            return self._baselines[key].is_ready()

    def get_baseline(self, user_id: str, scope: str = "global") -> dict | None:
        key = (user_id, scope)
        with self._lock:
            if key not in self._baselines:
                return None
            bl = self._baselines[key]
        if not bl.is_ready():
            return None
        return bl.get_baseline()

    def get_confidence(self, user_id: str, scope: str = "global") -> float:
        key = (user_id, scope)
        with self._lock:
            if key not in self._baselines:
                return 0.0
            return self._baselines[key].confidence

    def prune_stale(self, min_messages: int = 5, max_age_seconds: float = 86400 * 30) -> int:
        """清理低活跃且超时未活跃的用户基线，返回清理数"""
        now = time.time()
        removed = 0
        with self._lock:
            stale_keys = [
                key for key, bl in self._baselines.items()
                if bl.message_count < min_messages
                and (now - bl.last_seen) > max_age_seconds
            ]
            for key in stale_keys:
                del self._baselines[key]
                removed += 1
        return removed

    @staticmethod
    def get_soul_offset(raw_energy: dict[str, float], baseline_stats: dict) -> str:
        if not baseline_stats:
            return ""
        parts = []
        expr = raw_energy.get("ExpressionDesire", 0)
        if expr > 2:
            parts.append("消息更长 (+{:.0%})".format(expr / 10))
        elif expr < -2:
            parts.append("消息更短")
        social = raw_energy.get("RecallDepth", 0)
        if social > 2:
            parts.append("更乐于交流")
        elif social < -2:
            parts.append("更倾向于沉默")
        return ", ".join(parts) if parts else ""
