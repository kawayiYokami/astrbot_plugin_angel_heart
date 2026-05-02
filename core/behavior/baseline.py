"""
行为基线计算 — 累积消息统计，生成长期行为画像基线
纯数学计算，零外部依赖。
"""
import re
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field


EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001F9FF"
    r"\U0001FA00-\U0001FA6F"
    r"\U0001FA70-\U0001FAFF"
    r"\u2600-\u27BF"
    r"\uFE00-\uFE0F"
    r"\U0001F600-\U0001F64F"
    r"]",
)
PUNCTUATION_RE = re.compile(r"[，。！？、；：,\.!\?;:]")
QUESTION_RE = re.compile(r"[？\?]")
URL_RE = re.compile(r"https?://\S+")


@dataclass
class _ScopeStats:
    message_count: int = 0
    total_length: int = 0
    emoji_count: int = 0
    punct_count: int = 0
    question_count: int = 0
    link_count: int = 0
    sticker_count: int = 0
    last_seen: float = 0.0


class BehaviorBaseline:
    """累积消息统计，计算长期行为基线。线程安全。"""

    def __init__(self, min_messages: int = 50):
        self.min_messages = min_messages
        self._lock = threading.RLock()
        self._global = _ScopeStats()
        self._scopes: dict[str, _ScopeStats] = defaultdict(_ScopeStats)

    def feed(self, text: str, scope: str = "global", has_sticker: bool = False) -> None:
        stripped = text.strip()
        if not stripped and not has_sticker:
            return
        length = len(stripped) if stripped else 0
        emoji_n = len(EMOJI_RE.findall(stripped)) if stripped else 0
        punct_n = len(PUNCTUATION_RE.findall(stripped)) if stripped else 0
        question_n = len(QUESTION_RE.findall(stripped)) if stripped else 0
        link_n = len(URL_RE.findall(stripped)) if stripped else 0
        sticker_n = 1 if has_sticker else 0
        now = time.time()

        with self._lock:
            self._add(self._global, length, emoji_n, punct_n, question_n, link_n, sticker_n, now)
            self._add(self._scopes[scope], length, emoji_n, punct_n, question_n, link_n, sticker_n, now)

    @staticmethod
    def _add(s: _ScopeStats, length: int, emoji: int, punct: int,
             question: int, link: int, sticker: int = 0, now: float = 0.0) -> None:
        s.message_count += 1
        s.total_length += length
        s.emoji_count += emoji
        s.punct_count += punct
        s.question_count += question
        s.link_count += link
        s.sticker_count += sticker
        s.last_seen = now

    def is_ready(self, scope: str | None = None) -> bool:
        with self._lock:
            stats = self._get_stats(scope)
            return stats.message_count >= self.min_messages

    @property
    def message_count(self) -> int:
        with self._lock:
            return self._global.message_count

    @property
    def last_seen(self) -> float:
        with self._lock:
            return self._global.last_seen

    @property
    def confidence(self) -> float:
        with self._lock:
            n = self._global.message_count
        if n < self.min_messages:
            return 0.0
        ratio = n / self.min_messages
        return min(1.0, 0.5 + 0.5 * (1 - 1 / max(ratio, 1)))

    def get_baseline(self, scope: str | None = None) -> dict:
        with self._lock:
            stats = self._get_stats(scope)
            n = stats.message_count
            if n == 0:
                return self._empty_baseline()
            return {
                "message_count": n,
                "avg_message_length": stats.total_length / n,
                "emoji_frequency": stats.emoji_count / n,
                "sticker_frequency": stats.sticker_count / n,
                "punctuation_density": stats.punct_count / max(stats.total_length, 1),
                "question_frequency": stats.question_count / n,
                "link_frequency": stats.link_count / n,
            }

    def _get_stats(self, scope: str | None = None) -> _ScopeStats:
        if scope is None:
            return self._global
        return self._scopes[scope]

    @staticmethod
    def _empty_baseline() -> dict:
        return {
            "message_count": 0, "avg_message_length": 0.0,
            "emoji_frequency": 0.0, "sticker_frequency": 0.0,
            "punctuation_density": 0.0, "question_frequency": 0.0,
            "link_frequency": 0.0,
        }
