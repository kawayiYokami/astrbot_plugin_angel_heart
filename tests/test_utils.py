import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# 模拟 astrbot.api.logger
sys.modules['astrbot'] = MagicMock()
sys.modules['astrbot.api'] = MagicMock()
sys.modules['astrbot.api.logger'] = MagicMock()

# 模拟 markdown_it 和 mdit_plain 模块
sys.modules['markdown_it'] = MagicMock()
sys.modules['mdit_plain'] = MagicMock()
sys.modules['markdown_it.MarkdownIt'] = MagicMock()
sys.modules['mdit_plain.renderer'] = MagicMock()

# 将项目根目录添加到 Python 路径中，以便能够导入项目模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.utils import strip_markdown


class TestStripMarkdown(unittest.TestCase):
    """测试 core/utils.py 中的 strip_markdown 函数"""

    def test_empty_string(self):
        """测试空字符串"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = ""
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown(""), "")

    def test_plain_text(self):
        """测试纯文本"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "Hello World"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("Hello World"), "Hello World")

    def test_headers(self):
        """测试标题"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "Header 1"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("# Header 1"), "Header 1")

        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "Header 2"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("## Header 2"), "Header 2")

        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "Header 3"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("### Header 3"), "Header 3")

    def test_bold(self):
        """测试加粗"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "bold"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("**bold**"), "bold")

    def test_italic(self):
        """测试斜体"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "italic"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("*italic*"), "italic")

    def test_inline_code(self):
        """测试行内代码"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "code"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("`code`"), "code")

    def test_links(self):
        """测试链接"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "text"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("[text](url)"), "text")

    def test_images(self):
        """测试图片"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "alt"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("![alt](url)"), "alt")

    def test_unordered_lists(self):
        """测试无序列表"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "item"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("* item"), "item")

    def test_ordered_lists(self):
        """测试有序列表"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "item"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("1. item"), "item")

    def test_complex_markdown(self):
        """测试复杂的Markdown文本"""
        input_text = "# Title\n\nThis is **bold** and *italic* with `code` and a [link](url).\n\n* List item 1\n* List item 2"
        expected_output = "Title\nThis is bold and italic with code and a link.\n\nList item 1\nList item 2"
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = expected_output
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown(input_text), expected_output)

    def test_nested_formatting(self):
        """测试嵌套格式"""
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.return_value = "bold italic bold"
            mock_md_it.return_value = mock_instance
            self.assertEqual(strip_markdown("**bold *italic* bold**"), "bold italic bold")

    @patch('core.utils.logger')
    def test_import_error_fallback(self, mock_logger):
        """测试依赖库未安装时的回退逻辑"""
        # 模拟 ImportError
        with patch.dict('sys.modules', {'markdown_it': None}):
            result = strip_markdown("**bold** and *italic*")
            # 检查是否使用了回退方案
            self.assertEqual(result, "bold and italic")
            # 检查是否记录了警告日志
            mock_logger.warning.assert_called()

    @patch('core.utils.logger')
    def test_unexpected_error(self, mock_logger):
        """测试处理过程中发生未知错误的情况"""
        # 模拟在 md.render(text) 中抛出异常
        with patch('markdown_it.MarkdownIt') as mock_md_it:
            mock_instance = MagicMock()
            mock_instance.render.side_effect = Exception("Test Exception")
            mock_md_it.return_value = mock_instance

            result = strip_markdown("**bold**")
            # 检查是否返回了原始文本
            self.assertEqual(result, "**bold**")
            # 检查是否记录了错误日志
            mock_logger.error.assert_called()


class TestPruneOldMessages(unittest.TestCase):
    """测试 core/utils.py 中的 prune_old_messages 函数"""

    def test_prunes_old_messages_correctly(self):
        """核心功能测试：验证剪枝逻辑能正确过滤掉旧消息"""
        # Arrange
        db_history = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        cached_messages = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},  # 旧消息
            {"timestamp": 102, "user": "Charlie", "content": "New message"},  # 新消息
            {"timestamp": 101, "user": "Bob", "content": "Hi"},  # 旧消息
        ]
        expected = [{"timestamp": 102, "user": "Charlie", "content": "New message"}]

        # Act
        from core.utils import prune_old_messages
        result = prune_old_messages(cached_messages, db_history)

        # Assert
        self.assertEqual(result, expected)

    def test_returns_empty_list_if_all_messages_are_old(self):
        """边界条件测试：当所有缓存消息都是旧消息时，返回空列表"""
        # Arrange
        db_history = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        cached_messages = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        expected = []

        # Act
        from core.utils import prune_old_messages
        result = prune_old_messages(cached_messages, db_history)

        # Assert
        self.assertEqual(result, expected)

    def test_returns_all_cached_if_db_history_is_empty(self):
        """边界条件测试：当 db_history 为空时，所有 cached_messages 都应被保留"""
        # Arrange
        db_history = []
        cached_messages = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        expected = cached_messages

        # Act
        from core.utils import prune_old_messages
        result = prune_old_messages(cached_messages, db_history)

        # Assert
        self.assertEqual(result, expected)

    def test_returns_empty_list_if_cached_messages_is_empty(self):
        """边界条件测试：当 cached_messages 为空时，返回空列表"""
        # Arrange
        db_history = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
        ]
        cached_messages = []
        expected = []

        # Act
        from core.utils import prune_old_messages
        result = prune_old_messages(cached_messages, db_history)

        # Assert
        self.assertEqual(result, expected)

    def test_is_robust_to_missing_timestamps(self):
        """健壮性测试：验证代码能处理缺少 timestamp 键的消息"""
        # Arrange
        # 历史记录中有一条消息缺少 timestamp
        db_history = [
            {"user": "Alice", "content": "Hello"},  # 缺少 timestamp
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        # 缓存消息中也有一条消息缺少 timestamp
        cached_messages = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"user": "Charlie", "content": "Missing timestamp"}, # 缺少 timestamp
            {"timestamp": 102, "user": "David", "content": "New message"},
        ]

        # 分析：
        # 1. history_timestamps 集合应只包含 {101} (来自 db_history 中有 timestamp 的消息)
        # 2. 在过滤 cached_messages 时：
        #    - timestamp 100 的消息: 100 not in {101} -> True -> 保留
        #    - 缺少 timestamp 的消息: None not in {101} -> True -> 保留
        #    - timestamp 101 的消息: 101 not in {101} -> False -> 过滤掉 (但缓存里没有101)
        #    - timestamp 102 的消息: 102 not in {101} -> True -> 保留
        # 3. 所以 recent_dialogue 应该是:
        #    [
        #      {"timestamp": 100, "user": "Alice", "content": "Hello"},
        #      {"user": "Charlie", "content": "Missing timestamp"},
        #      {"timestamp": 102, "user": "David", "content": "New message"},
        #    ]
        expected = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"user": "Charlie", "content": "Missing timestamp"},
            {"timestamp": 102, "user": "David", "content": "New message"},
        ]

        # Act
        from core.utils import prune_old_messages
        result = prune_old_messages(cached_messages, db_history)

        # Assert
        self.assertEqual(result, expected)


if __name__ == '__main__':
    unittest.main()