"""
LIWC 分析器 — 测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.behavior.analyzer import LiwcAnalyzer


class TestLiwcBasic:
    """基础功能"""

    def test_empty_text_returns_zeros(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("")
        assert result.positive_emotion_ratio == 0.0
        assert result.negative_emotion_ratio == 0.0
        assert result.total_word_count == 0

    def test_whitespace_only_returns_zeros(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("   \n  \t  ")
        assert result.total_word_count == 0

    def test_word_count_correct(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("今天 天气 真的 很不错")
        assert result.total_word_count == 4


class TestEmotionDetection:
    """情绪词检测"""

    def test_positive_words_detected(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("我很开心，今天真是太棒了")
        assert result.positive_emotion_ratio > 0.0

    def test_negative_words_detected(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("我好难过，这太糟糕了")
        assert result.negative_emotion_ratio > 0.0

    def test_neutral_text_has_no_emotion(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("今天星期二，天气晴")
        assert result.positive_emotion_ratio == 0.0
        assert result.negative_emotion_ratio == 0.0

    def test_mixed_emotion_proportions(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("开心 开心 难过")
        # 2 positive, 1 negative out of 3
        assert result.positive_emotion_ratio == pytest.approx(2/3)
        assert result.negative_emotion_ratio == pytest.approx(1/3)


class TestPronounDetection:
    """代词检测"""

    def test_first_person_detected(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("我觉得我应该说点什么")
        assert result.first_person_ratio > 0.0

    def test_second_person_detected(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("你怎么看这个问题")
        assert result.second_person_ratio > 0.0

    def test_no_pronouns(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("天气真好")
        assert result.first_person_ratio == 0.0
        assert result.second_person_ratio == 0.0


class TestCognitiveWords:
    """认知词检测"""

    def test_causal_words_detected(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("因为下雨所以没去")
        assert result.causal_word_ratio > 0.0

    def test_insight_words_detected(self):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze("我明白了，原来是这样")
        assert result.insight_word_ratio > 0.0


class TestRatioBounds:
    """所有比例必须在 [0, 1] 内"""

    @pytest.mark.parametrize("text", [
        "我很开心",
        "难过悲伤痛苦",
        "我觉得你应该明白因为所以",
        "今天星期二天气晴",
        "",
    ])
    def test_all_ratios_in_range(self, text):
        analyzer = LiwcAnalyzer()
        result = analyzer.analyze(text)
        assert 0.0 <= result.positive_emotion_ratio <= 1.0
        assert 0.0 <= result.negative_emotion_ratio <= 1.0
        assert 0.0 <= result.first_person_ratio <= 1.0
        assert 0.0 <= result.second_person_ratio <= 1.0
        assert 0.0 <= result.causal_word_ratio <= 1.0
        assert 0.0 <= result.insight_word_ratio <= 1.0
