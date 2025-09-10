"""
AngelHeart 插件 - Secretary 角色单元测试
重点测试 perform_analysis 方法中的智能剪枝逻辑
"""

import unittest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
import asyncio

# 假设项目结构允许这样导入
from roles.secretary import Secretary
from models.analysis_result import SecretaryDecision


class TestSecretaryPruningLogic(unittest.IsolatedAsyncioTestCase):
    """测试 Secretary 类中与智能剪枝相关的逻辑"""

    def setUp(self):
        """在每个测试方法运行前执行，用于设置测试夹具 (Test Fixture)"""
        # 1. Arrange (安排) - 创建模拟对象和 Secretary 实例
        self.config_manager = Mock()
        self.config_manager.analyzer_model = "test_model"
        self.config_manager.reply_strategy_guide = "test_guide"
        self.config_manager.waiting_time = 10
        self.config_manager.debug_mode = False

        self.context = Mock()
        self.front_desk = Mock()
        # 模拟 llm_analyzer 实例
        self.mock_llm_analyzer = Mock()
        self.mock_llm_analyzer.analyze_and_decide = AsyncMock()

        # 创建 Secretary 实例，并手动注入模拟的 llm_analyzer 以替代 __init__ 中的创建逻辑
        self.secretary = Secretary(self.config_manager, self.context, self.front_desk)
        self.secretary.llm_analyzer = self.mock_llm_analyzer

        # 定义测试用的 chat_id
        self.chat_id = "test_chat_id_123"

    async def test_perform_analysis_prunes_old_messages(self):
        """核心功能测试：验证剪枝逻辑能正确过滤掉旧消息"""
        # 2. Arrange
        # 模拟历史记录，包含时间戳
        db_history = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        # 模拟缓存消息，包含新旧混合
        cached_messages = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},  # 旧消息
            {"timestamp": 102, "user": "Charlie", "content": "New message"},  # 新消息
            {"timestamp": 101, "user": "Bob", "content": "Hi"}, # 旧消息
        ]
        # 模拟前台返回缓存消息
        self.front_desk.get_messages.return_value = cached_messages

        # 模拟 llm_analyzer 返回一个决策
        expected_decision = SecretaryDecision(
            should_reply=True, reply_strategy="参与讨论", topic="新话题", reply_target="Charlie"
        )
        self.mock_llm_analyzer.analyze_and_decide.return_value = expected_decision

        # 3. Act
        result = await self.secretary.perform_analysis(self.chat_id, db_history)

        # 4. Assert
        # 验证 get_messages 被正确调用
        self.front_desk.get_messages.assert_called_once_with(self.chat_id)
        # 验证 analyze_and_decide 被调用，且传入的是剪枝后的 recent_dialogue
        # recent_dialogue 应该只包含 timestamp 为 102 的消息
        expected_recent_dialogue = [{"timestamp": 102, "user": "Charlie", "content": "New message"}]
        self.mock_llm_analyzer.analyze_and_decide.assert_awaited_once_with(
            historical_context=db_history,
            recent_dialogue=expected_recent_dialogue,
            chat_id=self.chat_id
        )
        # 验证返回的决策是 llm_analyzer 返回的
        self.assertEqual(result, expected_decision)

    async def test_perform_analysis_returns_no_reply_if_no_new_messages(self):
        """边界条件测试：当所有缓存消息都是旧消息时，应返回 '无新消息' 决策"""
        # 2. Arrange
        db_history = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        # 缓存消息完全匹配历史记录
        cached_messages = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        self.front_desk.get_messages.return_value = cached_messages

        # 3. Act
        result = await self.secretary.perform_analysis(self.chat_id, db_history)

        # 4. Assert
        # 验证 get_messages 被调用
        self.front_desk.get_messages.assert_called_once_with(self.chat_id)
        # 验证 analyze_and_decide 没有被调用，因为没有新消息
        self.mock_llm_analyzer.analyze_and_decide.assert_not_awaited()
        # 验证返回了一个 '无新消息' 的决策
        self.assertIsInstance(result, SecretaryDecision)
        self.assertFalse(result.should_reply)
        self.assertEqual(result.reply_strategy, "无新消息")
        self.assertEqual(result.topic, "未知")

    async def test_perform_analysis_all_cached_are_new_if_db_history_empty(self):
        """边界条件测试：当 db_history 为空时，所有缓存消息都应被视为新消息"""
        # 2. Arrange
        db_history = []  # 无历史记录
        cached_messages = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        self.front_desk.get_messages.return_value = cached_messages

        expected_decision = SecretaryDecision(
            should_reply=True, reply_strategy="参与讨论", topic="新话题", reply_target="Alice"
        )
        self.mock_llm_analyzer.analyze_and_decide.return_value = expected_decision

        # 3. Act
        result = await self.secretary.perform_analysis(self.chat_id, db_history)

        # 4. Assert
        self.front_desk.get_messages.assert_called_once_with(self.chat_id)
        # 验证 analyze_and_decide 被调用，且传入了所有缓存消息
        self.mock_llm_analyzer.analyze_and_decide.assert_awaited_once_with(
            historical_context=[],
            recent_dialogue=cached_messages, # 所有消息都是新的
            chat_id=self.chat_id
        )
        self.assertEqual(result, expected_decision)

    async def test_perform_analysis_handles_empty_cached_messages(self):
        """边界条件测试：当 cached_messages 为空时，应正确处理"""
        # 2. Arrange
        db_history = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
        ]
        cached_messages = [] # 无缓存消息
        self.front_desk.get_messages.return_value = cached_messages

        # 3. Act
        result = await self.secretary.perform_analysis(self.chat_id, db_history)

        # 4. Assert
        self.front_desk.get_messages.assert_called_once_with(self.chat_id)
        self.mock_llm_analyzer.analyze_and_decide.assert_not_awaited()
        # 验证返回了一个 '无新消息' 的决策
        self.assertIsInstance(result, SecretaryDecision)
        self.assertFalse(result.should_reply)
        self.assertEqual(result.reply_strategy, "无新消息")
        self.assertEqual(result.topic, "未知")

    async def test_perform_analysis_is_robust_to_missing_timestamps(self):
        """健壮性测试：验证代码能处理缺少 timestamp 的消息"""
        # 2. Arrange
        # 历史记录中有一条消息缺少 timestamp
        db_history = [
            {"user": "Alice", "content": "Hello"}, # 缺少 timestamp
            {"timestamp": 101, "user": "Bob", "content": "Hi"},
        ]
        # 缓存消息中也有一条消息缺少 timestamp
        cached_messages = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"user": "Charlie", "content": "Missing timestamp"}, # 缺少 timestamp
            {"timestamp": 102, "user": "David", "content": "New message"},
        ]
        self.front_desk.get_messages.return_value = cached_messages

        expected_decision = SecretaryDecision(
            should_reply=True, reply_strategy="参与讨论", topic="新话题", reply_target="David"
        )
        self.mock_llm_analyzer.analyze_and_decide.return_value = expected_decision

        # 3. Act
        result = await self.secretary.perform_analysis(self.chat_id, db_history)

        # 4. Assert
        self.front_desk.get_messages.assert_called_once_with(self.chat_id)
        # 分析：
        # 1. history_timestamps 集合应只包含 {101} (来自 db_history 中有 timestamp 的消息)
        # 2. 在过滤 cached_messages 时：
        #    - timestamp 100 的消息会被过滤掉 (因为 100 不在 {101} 中) -> 实际上应该保留，逻辑有误，重新分析
        #    重新分析：
        #    - history_timestamps = {101}
        #    - cached_messages 中 timestamp 为 100 和 102 的消息，100 not in {101} (True), 102 not in {101} (True)
        #    - 缺少 timestamp 的消息 msg.get("timestamp") 会是 None, None not in {101} (True)
        #    - 所以，最终的 recent_dialogue 应该包含所有 cached_messages
        #    等一下，这和我们的预期不符。让我们重新审视逻辑。
        #    逻辑是正确的：msg.get("timestamp") not in history_timestamps
        #    对于 timestamp 100: 100 not in {101} -> True -> 保留
        #    对于 缺少 timestamp: None not in {101} -> True -> 保留
        #    对于 timestamp 101: 101 not in {101} -> False -> 过滤掉 (但缓存里没有101)
        #    对于 timestamp 102: 102 not in {101} -> True -> 保留
        #    所以 recent_dialogue 应该是:
        #    [
        #      {"timestamp": 100, "user": "Alice", "content": "Hello"},
        #      {"user": "Charlie", "content": "Missing timestamp"},
        #      {"timestamp": 102, "user": "David", "content": "New message"},
        #    ]
        #    这个逻辑是合理的，缺少 timestamp 的消息被视为新消息。
        expected_recent_dialogue = [
            {"timestamp": 100, "user": "Alice", "content": "Hello"},
            {"user": "Charlie", "content": "Missing timestamp"},
            {"timestamp": 102, "user": "David", "content": "New message"},
        ]
        self.mock_llm_analyzer.analyze_and_decide.assert_awaited_once_with(
            historical_context=db_history,
            recent_dialogue=expected_recent_dialogue,
            chat_id=self.chat_id
        )
        self.assertEqual(result, expected_decision)


if __name__ == '__main__':
    unittest.main()