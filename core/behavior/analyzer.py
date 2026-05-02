"""
行为画像分析器 — LIWC 词频统计 + 消息清洗
纯统计，零 LLM 依赖。
"""
import re
from dataclasses import dataclass, field
from .liwc_dict import (
    POSITIVE_EMOTION, NEGATIVE_EMOTION,
    FIRST_PERSON, SECOND_PERSON,
    CAUSAL_WORDS, INSIGHT_WORDS,
)


# ============================================================
# 消息清洗器
# ============================================================

class MessageCleaner:
    """过滤代码块、转发、引用，清洗消息文本"""

    CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
    QUOTE_RE = re.compile(r"^>", re.MULTILINE)

    def is_code_block(self, text: str) -> bool:
        """检测文本是否包含 markdown 代码块 (```...```)"""
        return bool(self.CODE_BLOCK_RE.search(text))

    def is_forward_or_quote(self, text: str) -> bool:
        """检测文本是否以引用符 (>) 开头，用于判断转发/引用消息"""
        return bool(self.QUOTE_RE.search(text))

    def should_skip(self, text: str) -> bool:
        """判断消息是否应跳过分析（过短/纯代码/纯引用）"""
        stripped = text.strip()
        if len(stripped) < 3:
            return True
        if self.is_code_block(stripped):
            return True
        if self.is_forward_or_quote(stripped):
            return True
        return False

    def clean(self, text: str) -> str:
        """移除代码块和引用前缀，返回可分析的纯文本"""
        text = self.CODE_BLOCK_RE.sub("", text)
        text = self.QUOTE_RE.sub("", text)
        return text.strip()


# ============================================================
# 分词器（极简中文分词 + 子串匹配）
# ============================================================

class SimpleTokenizer:
    """按标点拆分中文文本，支持子串级词典匹配"""

    SPLIT_RE = re.compile(r"[，。！？、；：\s,\.!\?;:]+")

    def tokenize(self, text: str) -> list[str]:
        """按标点和空格将文本拆分为词级单元列表"""
        if not text or not text.strip():
            return []
        raw = self.SPLIT_RE.split(text)
        return [t.strip() for t in raw if t.strip()]

    @staticmethod
    def token_contains(token: str, word: str) -> bool:
        """检查词典词是否作为子串出现在 token 中"""
        return word in token


# ============================================================
# LIWC 分析结果
# ============================================================

@dataclass
class LiwcResult:
    total_word_count: int = 0
    positive_emotion_count: int = 0
    negative_emotion_count: int = 0
    first_person_count: int = 0
    second_person_count: int = 0
    causal_word_count: int = 0
    insight_word_count: int = 0

    @property
    def positive_emotion_ratio(self) -> float:
        if self.total_word_count == 0:
            return 0.0
        return self.positive_emotion_count / self.total_word_count

    @property
    def negative_emotion_ratio(self) -> float:
        if self.total_word_count == 0:
            return 0.0
        return self.negative_emotion_count / self.total_word_count

    @property
    def first_person_ratio(self) -> float:
        if self.total_word_count == 0:
            return 0.0
        return self.first_person_count / self.total_word_count

    @property
    def second_person_ratio(self) -> float:
        if self.total_word_count == 0:
            return 0.0
        return self.second_person_count / self.total_word_count

    @property
    def causal_word_ratio(self) -> float:
        if self.total_word_count == 0:
            return 0.0
        return self.causal_word_count / self.total_word_count

    @property
    def insight_word_ratio(self) -> float:
        if self.total_word_count == 0:
            return 0.0
        return self.insight_word_count / self.total_word_count


# ============================================================
# LIWC 分析器（子串匹配）
# ============================================================

class LiwcAnalyzer:
    """对文本做 LIWC 词频统计，支持子串级匹配"""

    def __init__(self):
        self.tokenizer = SimpleTokenizer()

    def analyze(self, text: str) -> LiwcResult:
        """分析文本，返回六类词汇的计数和比例"""
        tokens = self.tokenizer.tokenize(text)
        result = LiwcResult()
        result.total_word_count = len(tokens)

        for token in tokens:
            self._match_category(result, token, POSITIVE_EMOTION, "pos")
            self._match_category(result, token, NEGATIVE_EMOTION, "neg")
            self._match_category(result, token, FIRST_PERSON, "fp")
            self._match_category(result, token, SECOND_PERSON, "sp")
            self._match_category(result, token, CAUSAL_WORDS, "cau")
            self._match_category(result, token, INSIGHT_WORDS, "ins")

        return result

    def _match_category(self, result: LiwcResult, token: str,
                        word_set: set, cat: str) -> None:
        """token 中包含词典任一词即 +1，同类别只计一次"""
        for word in word_set:
            if self.tokenizer.token_contains(token, word):
                if cat == "pos":
                    result.positive_emotion_count += 1
                elif cat == "neg":
                    result.negative_emotion_count += 1
                elif cat == "fp":
                    result.first_person_count += 1
                elif cat == "sp":
                    result.second_person_count += 1
                elif cat == "cau":
                    result.causal_word_count += 1
                elif cat == "ins":
                    result.insight_word_count += 1
                break  # 同类别只计一次
