"""
行为基线计算 — 测试用例
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.behavior.baseline import BehaviorBaseline


class TestColdStart:
    """冷启动：消息不够时不输出基线"""

    def test_not_ready_when_insufficient_messages(self):
        baseline = BehaviorBaseline(min_messages=50)
        for _ in range(30):
            baseline.feed("今天天气真好")
        assert baseline.is_ready() == False

    def test_ready_after_sufficient_messages(self):
        baseline = BehaviorBaseline(min_messages=50)
        for _ in range(50):
            baseline.feed("测试消息")
        assert baseline.is_ready() == True

    def test_default_min_is_50(self):
        baseline = BehaviorBaseline()
        assert baseline.min_messages == 50


class TestMessageStats:
    """消息统计"""

    def test_message_count_increments(self):
        baseline = BehaviorBaseline(min_messages=1)
        baseline.feed("你好")
        baseline.feed("世界")
        assert baseline.message_count == 2

    def test_avg_length_tracks_correctly(self):
        baseline = BehaviorBaseline(min_messages=1)
        baseline.feed("你好世界")   # 4 chars
        baseline.feed("Hi")         # 2 chars
        baseline.feed("测试")       # 2 chars
        assert baseline.get_baseline()["avg_message_length"] == pytest.approx(8/3)

    def test_empty_message_not_counted(self):
        baseline = BehaviorBaseline(min_messages=1)
        baseline.feed("   ")
        assert baseline.message_count == 0


class TestEmojiStats:
    """表情统计"""

    def test_emoji_detected(self):
        baseline = BehaviorBaseline(min_messages=1)
        baseline.feed("哈哈😄")
        baseline.feed("普通文本")
        assert baseline.get_baseline()["emoji_frequency"] == pytest.approx(0.5)

    def test_no_emoji_is_zero(self):
        baseline = BehaviorBaseline(min_messages=1)
        baseline.feed("纯文本消息")
        baseline.feed("没有表情")
        assert baseline.get_baseline()["emoji_frequency"] == 0.0


class TestPunctuationStats:
    """标点统计"""

    def test_punctuation_density(self):
        baseline = BehaviorBaseline(min_messages=1)
        baseline.feed("你好！今天怎么样？还不错。")
        assert baseline.get_baseline()["punctuation_density"] > 0.0

    def test_no_punctuation_is_zero(self):
        baseline = BehaviorBaseline(min_messages=1)
        baseline.feed("纯文字无标点")
        assert baseline.get_baseline()["punctuation_density"] == 0.0


class TestScopeSeparation:
    """scope 分离统计"""

    def test_private_and_group_separated(self):
        baseline = BehaviorBaseline(min_messages=1)
        baseline.feed("私聊消息", scope="private")
        baseline.feed("群聊消息", scope="group")
        baseline.feed("群聊第二条", scope="group")

        private = baseline.get_baseline("private")
        group = baseline.get_baseline("group")

        assert private["message_count"] == 1
        assert group["message_count"] == 2

    def test_global_aggregates_all(self):
        baseline = BehaviorBaseline(min_messages=1)
        baseline.feed("私聊", scope="private")
        baseline.feed("群聊", scope="group")
        baseline.feed("群聊2", scope="group")

        global_stats = baseline.get_baseline()  # 不指定 scope = 全局
        assert global_stats["message_count"] == 3


class TestBaselineOutput:
    """基线输出格式"""

    def test_output_contains_all_dimensions(self):
        baseline = BehaviorBaseline(min_messages=1)
        for _ in range(60):
            baseline.feed("测试消息内容")

        result = baseline.get_baseline()
        expected_keys = [
            "message_count", "avg_message_length",
            "emoji_frequency", "punctuation_density",
            "question_frequency", "link_frequency",
        ]
        for key in expected_keys:
            assert key in result, f"缺少维度: {key}"

    def test_confidence_low_when_barely_ready(self):
        baseline = BehaviorBaseline(min_messages=50)
        for _ in range(50):
            baseline.feed("消息")
        assert baseline.confidence < 0.8  # 刚过阈值，置信度低

    def test_confidence_grows_with_messages(self):
        baseline = BehaviorBaseline(min_messages=50)
        for _ in range(500):
            baseline.feed("消息内容")
        assert baseline.confidence >= 0.9  # 样本充足，置信度高
