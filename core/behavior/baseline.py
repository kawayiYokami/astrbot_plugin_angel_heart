"""
行为基线计算 — 累积消息统计，生成长期行为画像基线
纯数学计算，零外部依赖。
"""
import re
from collections import defaultdict
from dataclasses import dataclass, field


# ============================================================
# 标点/表情/链接 检测器
# ============================================================

EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001F9FF"      # 表情符号
    r"\U0001FA00-\U0001FA6F"       # 扩展表情
    r"\U0001FA70-\U0001FAFF"       # 更多扩展
    r"\u2600-\u27BF"               # 杂项符号
    r"\uFE00-\uFE0F"               # 变体选择器
    r"\U0001F600-\U0001F64F"       # 表情人脸
    r"]",
)

PUNCTUATION_RE = re.compile(r"[，。！？、；：,\.!\?;:]")
QUESTION_RE = re.compile(r"[？\?]")
URL_RE = re.compile(r"https?://\S+")


# ============================================================
# 会话统计累加器
# ============================================================

@dataclass
class _ScopeStats:
    """单个 scope 的累计统计"""
    message_count: int = 0
    total_length: int = 0
    emoji_count: int = 0
    punct_count: int = 0
    question_count: int = 0
    link_count: int = 0
    sticker_count: int = 0  # QQ表情包/GIF/图片


# ============================================================
# 行为基线
# ============================================================

class BehaviorBaseline:
    """
    累积消息统计，计算长期行为基线。

    用法:
        baseline = BehaviorBaseline(min_messages=50)
        baseline.feed("用户发来一条消息", scope="private")
        if baseline.is_ready():
            stats = baseline.get_baseline("private")
    """

    def __init__(self, min_messages: int = 50):
        self.min_messages = min_messages
        self._global = _ScopeStats()
        self._scopes: dict[str, _ScopeStats] = defaultdict(_ScopeStats)

    # ---- 喂入消息 ----

    def feed(self, text: str, scope: str = "global", has_sticker: bool = False) -> None:
        stripped = text.strip()
        if not stripped and not has_sticker:
            return  # 纯贴图消息（无文本）也要统计

        length = len(stripped) if stripped else 0
        emoji_n = len(EMOJI_RE.findall(stripped)) if stripped else 0
        punct_n = len(PUNCTUATION_RE.findall(stripped)) if stripped else 0
        question_n = len(QUESTION_RE.findall(stripped)) if stripped else 0
        link_n = len(URL_RE.findall(stripped)) if stripped else 0
        sticker_n = 1 if has_sticker else 0

        self._add(self._global, length, emoji_n, punct_n, question_n, link_n, sticker_n)
        self._add(self._scopes[scope], length, emoji_n, punct_n, question_n, link_n, sticker_n)

    @staticmethod
    def _add(s: _ScopeStats, length: int, emoji: int, punct: int,
             question: int, link: int, sticker: int = 0) -> None:
        s.message_count += 1
        s.total_length += length
        s.emoji_count += emoji
        s.punct_count += punct
        s.question_count += question
        s.link_count += link
        s.sticker_count += sticker

    # ---- 就绪判断 ----

    def is_ready(self, scope: str | None = None) -> bool:
        stats = self._get_stats(scope)
        return stats.message_count >= self.min_messages

    @property
    def message_count(self) -> int:
        return self._global.message_count

    # ---- 置信度 ----

    @property
    def confidence(self) -> float:
        """0~1，消息越多置信度越高"""
        if self._global.message_count < self.min_messages:
            return 0.0
        ratio = self._global.message_count / self.min_messages
        return min(1.0, 0.5 + 0.5 * (1 - 1 / max(ratio, 1)))

    # ---- 基线输出 ----

    def get_baseline(self, scope: str | None = None) -> dict:
        """输出基线统计字典"""
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

    # ---- 内部 ----

    def _get_stats(self, scope: str | None = None) -> _ScopeStats:
        if scope is None:
            return self._global
        return self._scopes[scope]

    @staticmethod
    def _empty_baseline() -> dict:
        return {
            "message_count": 0,
            "avg_message_length": 0.0,
            "emoji_frequency": 0.0,
            "sticker_frequency": 0.0,
            "punctuation_density": 0.0,
            "question_frequency": 0.0,
            "link_frequency": 0.0,
        }
