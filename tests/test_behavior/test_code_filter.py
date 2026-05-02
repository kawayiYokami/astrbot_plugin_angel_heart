"""
代码块和转发消息过滤器 — 测试用例
"""
import pytest
import sys
import os

# 添加插件目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.behavior.analyzer import MessageCleaner


class TestCodeBlockFilter:
    """代码块检测"""

    def test_markdown_code_block_detected(self):
        cleaner = MessageCleaner()
        msg = "```python\nprint('hello')\n``` 帮我看下这段"
        assert cleaner.is_code_block(msg) == True

    def test_normal_message_not_code_block(self):
        cleaner = MessageCleaner()
        msg = "我叫小貔貅，是后端工程师"
        assert cleaner.is_code_block(msg) == False

    def test_inline_code_not_full_block(self):
        cleaner = MessageCleaner()
        msg = "用 `lambda x: x + 1` 就行"
        assert cleaner.is_code_block(msg) == False

    def test_triple_backtick_without_lang(self):
        cleaner = MessageCleaner()
        msg = "```\nplain code\n```"
        assert cleaner.is_code_block(msg) == True


class TestForwardFilter:
    """转发/引用检测"""

    def test_quote_line_detected(self):
        cleaner = MessageCleaner()
        msg = "> 这篇文章写得真好\n我也觉得"
        assert cleaner.is_forward_or_quote(msg) == True

    def test_normal_message_not_quote(self):
        cleaner = MessageCleaner()
        msg = "写得不错"
        assert cleaner.is_forward_or_quote(msg) == False


class TestShouldSkip:
    """综合跳过判断"""

    def test_code_block_should_skip(self):
        cleaner = MessageCleaner()
        assert cleaner.should_skip("```\ncode\n```") == True

    def test_quote_should_skip(self):
        cleaner = MessageCleaner()
        assert cleaner.should_skip("> 转发内容") == True

    def test_too_short_should_skip(self):
        cleaner = MessageCleaner()
        assert cleaner.should_skip("好") == True
        assert cleaner.should_skip("ok") == True

    def test_normal_message_should_not_skip(self):
        cleaner = MessageCleaner()
        assert cleaner.should_skip("我叫小貔貅，是做后端的") == False

    def test_empty_string_should_skip(self):
        cleaner = MessageCleaner()
        assert cleaner.should_skip("") == True
        assert cleaner.should_skip("   ") == True
