"""
AngelHeart 状态管理自检测试模块

用于在插件初始化时验证状态管理逻辑的正确性，不调用真实LLM。
"""

import asyncio
import time

try:
    from astrbot.api import logger
except ImportError:
    import logging

    logger = logging.getLogger(__name__)

from .angel_heart_status import AngelHeartStatus
from .config_manager import ConfigManager
from .angel_heart_context import AngelHeartContext
from ..models.analysis_result import SecretaryDecision


class MockEvent:
    """模拟消息事件"""

    def __init__(self, chat_id: str, content: str, sender_id: str = "user123"):
        self.unified_msg_origin = chat_id
        self.message_str = content
        self.sender_id = sender_id
        self.timestamp = time.time()

    def get_sender_name(self):
        return f"用户{self.sender_id}"


class MockLLMAnalyzer:
    """模拟LLM分析器"""

    def __init__(self):
        self.call_count = 0

    async def analyze_and_decide(self, messages=None, chat_id=None, historical_context=None, recent_dialogue=None):
        """模拟分析，返回预设决策"""
        self.call_count += 1
        return SecretaryDecision(
            should_reply=True,
            reply_strategy="测试回复",
            topic="测试话题",
            alias="测试助手"
        )


class StateTestRunner:
    """状态测试运行器"""

    def __init__(self, config_manager: ConfigManager, context, angel_context: AngelHeartContext):
        self.config_manager = config_manager
        self.context = context
        self.angel_context = angel_context
        self.test_results = []

        # 设置测试配置
        self._setup_test_config()

        # 保存原始配置
        self.original_config = self.config_manager._config.copy()

    def _setup_test_config(self):
        """设置测试用的配置参数"""
        # 确保在被呼唤时分析（用于测试召唤功能）
        self.config_manager._config["analysis_on_mention_only"] = True

        # 设置测试用的昵称
        self.config_manager._config["alias"] = "测试助手|AI助手"

        # 设置较小的阈值便于测试
        self.config_manager._config["echo_detection_threshold"] = 3
        self.config_manager._config["dense_conversation_threshold"] = 5
        self.config_manager._config["dense_conversation_window"] = 600  # 10分钟
        self.config_manager._config["echo_detection_window"] = 30  # 30秒

        # 保存原始配置
        self.original_config = self.config_manager._config.copy()

    async def run_all_tests(self):
        """运行所有状态测试"""
        logger.info("🧪 开始AngelHeart状态管理自检测试...")

        test_methods = [
            self.test_not_present_to_familiarity_echo,
            self.test_not_present_to_familiarity_dense,
            self.test_not_present_to_summoned,
            self.test_familiarity_timeout,
            self.test_observation_timeout,
            self.test_status_stability
        ]

        passed = 0
        total = len(test_methods)

        for test_method in test_methods:
            try:
                result = await test_method()
                if result:
                    passed += 1
                    logger.info(f"✅ {test_method.__name__}: 通过")
                else:
                    logger.error(f"❌ {test_method.__name__}: 失败")
            except Exception as e:
                logger.error(f"💥 {test_method.__name__}: 异常 - {e}")

        logger.info(f"📊 测试完成: {passed}/{total} 通过")
        return passed == total

    async def test_not_present_to_familiarity_echo(self):
        """测试：不在场 → 复读触发 → 混脸熟"""
        chat_id = "test_echo"

        # 清理上下文
        self.angel_context.conversation_ledger.set_messages(chat_id, [])

        # 添加复读消息
        for i in range(3):
            message = {
                "content": "复读内容",
                "sender_id": "user123",
                "role": "user",
                "timestamp": time.time()
            }
            self.angel_context.conversation_ledger.add_message(chat_id, message)

        # 模拟前台调用秘书
        from ..roles.secretary import Secretary
        mock_llm = MockLLMAnalyzer()
        secretary = Secretary(self.config_manager, self.context, self.angel_context)
        secretary.llm_analyzer = mock_llm

        event = MockEvent(chat_id, "复读内容")

        # 添加调试：转换前状态
        status_before = self.angel_context.get_chat_status(chat_id)
        logger.debug(f"AngelHeart[{chat_id}]: 转换前状态: {status_before.value}")

        decision = await secretary.handle_message_by_state(event)

        # 添加调试：转换后状态
        status_after = self.angel_context.get_chat_status(chat_id)
        logger.debug(f"AngelHeart[{chat_id}]: 转换后状态: {status_after.value}")

        # 验证决策正确性
        logger.debug(f"AngelHeart[{chat_id}]: 测试结果 - should_reply: {decision.should_reply}, 策略: {decision.reply_strategy}")
        return decision.should_reply

    async def test_not_present_to_familiarity_dense(self):
        """测试：不在场 → 密集对话触发 → 混脸熟"""
        chat_id = "test_dense"

        # 清理上下文
        self.angel_context.conversation_ledger.set_messages(chat_id, [])

        # 添加密集对话消息
        participants = [f"user{i}" for i in range(6)]  # 6个不同用户
        for i, user in enumerate(participants):
            message = {
                "content": f"消息{i}",
                "sender_id": user,
                "role": "user",
                "timestamp": time.time()
            }
            self.angel_context.conversation_ledger.add_message(chat_id, message)

        # 模拟前台调用秘书
        from ..roles.secretary import Secretary
        mock_llm = MockLLMAnalyzer()
        secretary = Secretary(self.config_manager, self.context, self.angel_context)
        secretary.llm_analyzer = mock_llm

        event = MockEvent(chat_id, "新消息")
        decision = await secretary.handle_message_by_state(event)

        # 验证决策正确性
        return decision.should_reply

    async def test_not_present_to_summoned(self):
        """测试：不在场 → 被呼唤 → 被呼唤状态"""
        chat_id = "test_summoned"

        # 清理上下文
        self.angel_context.conversation_ledger.set_messages(chat_id, [])

        # 添加呼唤消息
        message = {
            "content": "测试助手",
            "sender_id": "user123",
            "role": "user",
            "timestamp": time.time()
        }
        self.angel_context.conversation_ledger.add_message(chat_id, message)

        # 模拟前台调用秘书
        from ..roles.secretary import Secretary
        mock_llm = MockLLMAnalyzer()
        secretary = Secretary(self.config_manager, self.context, self.angel_context)
        secretary.llm_analyzer = mock_llm

        event = MockEvent(chat_id, "测试助手 帮我个忙")
        decision = await secretary.handle_message_by_state(event)

        # 验证决策正确性
        return decision.should_reply

    async def test_familiarity_timeout(self):
        """测试：混脸熟 → 超时 → 不在场"""
        chat_id = "test_timeout"

        # 清理上下文
        self.angel_context.conversation_ledger.set_messages(chat_id, [])

        # 先进入混脸熟状态
        await self.angel_context.transition_to_status(chat_id, AngelHeartStatus.GETTING_FAMILIAR, "测试设置")

        # 模拟时间流逝（超过familiarity_timeout）
        original_timeout = self.config_manager.familiarity_timeout
        self.config_manager._config["familiarity_timeout"] = 1  # 1秒超时

        # 等待超时
        await asyncio.sleep(1.1)

        # 触发状态检查
        from ..core.angel_heart_status import StatusChecker
        status_checker = StatusChecker(self.config_manager, self.angel_context)
        new_status = await status_checker.determine_status(chat_id)

        # 恢复原始配置
        self.config_manager._config["familiarity_timeout"] = original_timeout

        return new_status == AngelHeartStatus.NOT_PRESENT

    async def test_observation_timeout(self):
        """测试：观测中 → 超时 → 不在场"""
        chat_id = "test_observation"

        # 清理上下文
        self.angel_context.conversation_ledger.set_messages(chat_id, [])

        # 先进入观测中状态
        await self.angel_context.transition_to_status(chat_id, AngelHeartStatus.OBSERVATION, "测试设置")
        await self.angel_context.update_last_analysis_time(chat_id)  # 设置为当前时间，然后手动调整
        # 手动设置为100秒前（用于测试超时）
        self.angel_context.last_analysis_time[chat_id] = time.time() - 100

        # 模拟时间流逝（超过observation_timeout）
        original_timeout = self.config_manager.observation_timeout
        self.config_manager._config["observation_timeout"] = 1  # 1秒超时

        # 等待超时
        await asyncio.sleep(1.1)

        # 触发状态检查
        current_status = self.angel_context.get_chat_status(chat_id)

        # 恢复原始配置
        self.config_manager._config["observation_timeout"] = original_timeout

        return current_status == AngelHeartStatus.NOT_PRESENT

    async def test_status_stability(self):
        """测试：状态稳定性 - 确保没有无限递归"""
        chat_id = "test_stability"

        # 清理上下文
        self.angel_context.conversation_ledger.set_messages(chat_id, [])

        # 模拟前台调用秘书，记录调用次数
        from ..roles.secretary import Secretary
        mock_llm = MockLLMAnalyzer()
        secretary = Secretary(self.config_manager, self.context, self.angel_context)
        secretary.llm_analyzer = mock_llm

        event = MockEvent(chat_id, "普通消息")

        # 多次调用，确保不会无限递归
        start_time = time.time()
        for i in range(10):
            await secretary.handle_message_by_state(event)
            if time.time() - start_time > 5:  # 超过5秒认为可能有递归问题
                return False

        # 验证LLM调用次数合理（不应该无限调用）
        return mock_llm.call_count < 20  # 合理的调用次数上限


async def run_state_self_test(config_manager: ConfigManager, context, angel_context: AngelHeartContext):
    """运行状态自检测试"""
    try:
        runner = StateTestRunner(config_manager, context, angel_context)
        success = await runner.run_all_tests()

        if success:
            logger.info("🎉 AngelHeart状态管理自检测试全部通过！")
        else:
            logger.warning("⚠️ AngelHeart状态管理自检测试存在问题，请检查日志")

        return success
    except Exception as e:
        logger.error(f"💥 状态自检测试异常: {e}", exc_info=True)
        return False