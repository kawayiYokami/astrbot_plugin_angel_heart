"""
AngelHeart 插件 - 上下文注入测试
测试聊天记录、秘书决策和needs_search的注入与解析功能。
"""

import json
import pytest
from unittest.mock import Mock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 条件导入：当缺少astrbot依赖时使用Mock
try:
    from astrbot.api import logger
except ImportError:
    # 创建Mock logger用于测试
    class MockLogger:
        def debug(self, msg): pass
        def info(self, msg): pass  
        def warning(self, msg): pass
        def error(self, msg): pass
    logger = MockLogger()

from core.utils import json_serialize_context
from models.analysis_result import SecretaryDecision


class TestContextInjection:
    """测试上下文注入功能"""

    def test_json_serialize_context_basic(self):
        """测试基本序列化功能"""
        # 模拟聊天记录
        chat_records = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Hello"}],
                "sender_id": "123",
                "sender_name": "User1",
                "timestamp": 1696110000.0
            },
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Hi"}],
                "sender_id": "bot",
                "sender_name": "AngelHeart",
                "timestamp": 1696110001.0
            }
        ]

        # 模拟秘书决策
        decision = SecretaryDecision(
            should_reply=True,
            reply_strategy="表示共情",
            topic="日常问候",
            persona_name="AngelHeart",
            alias="天使心",
            reply_target="User1",
            needs_search=False
        )

        # 序列化
        result = json_serialize_context(chat_records, decision)

        # 验证结果是字符串
        assert isinstance(result, str)

        # 解析并验证内容
        context = json.loads(result)
        assert 'chat_records' in context
        assert 'secretary_decision' in context
        assert 'needs_search' in context

        assert len(context['chat_records']) == 2
        assert context['secretary_decision']['should_reply'] is True
        assert context['secretary_decision']['reply_strategy'] == "表示共情"
        assert context['needs_search'] is False

    def test_json_serialize_context_empty_records(self):
        """测试空聊天记录序列化"""
        chat_records = []
        decision = SecretaryDecision(
            should_reply=False,
            reply_strategy="不参与",
            topic="未知",
            needs_search=True
        )

        result = json_serialize_context(chat_records, decision, needs_search=True)
        context = json.loads(result)

        assert len(context['chat_records']) == 0
        assert context['secretary_decision']['should_reply'] is False
        assert context['needs_search'] is True

    def test_json_serialize_context_invalid_records(self):
        """测试无效聊天记录的处理"""
        chat_records = [
            {"role": "user", "content": "valid"},
            "invalid_record",  # 非字典类型
            None,  # None值
            {"role": "assistant", "content": "valid"}
        ]
        decision = SecretaryDecision(should_reply=True, reply_strategy="测试", topic="测试话题")

        result = json_serialize_context(chat_records, decision)
        context = json.loads(result)

        # 应该只包含有效的记录
        assert len(context['chat_records']) == 2
        assert context['chat_records'][0]["role"] == "user"
        assert context['chat_records'][1]["role"] == "assistant"

    def test_json_serialize_context_invalid_input(self):
        """测试无效输入的处理"""
        # 测试非列表的chat_records
        result = json_serialize_context("not_a_list", SecretaryDecision(should_reply=True, reply_strategy="测试", topic="测试话题"))
        context = json.loads(result)

        assert context['chat_records'] == []
        assert context['secretary_decision']['should_reply'] is True

    def test_json_serialize_context_dict_decision(self):
        """测试字典类型的decision参数"""
        chat_records = [{"role": "user", "content": "test"}]
        decision_dict = {"should_reply": True, "reply_strategy": "测试"}

        result = json_serialize_context(chat_records, decision_dict, needs_search=False)
        context = json.loads(result)

        assert context['secretary_decision'] == decision_dict

    def test_json_serialize_context_serialization_error(self):
        """测试序列化错误处理"""
        # 创建一个包含不可序列化对象的情况
        class UnserializableObject:
            pass

        chat_records = [{"role": "user", "content": "test"}]
        decision = SecretaryDecision(should_reply=True, reply_strategy="测试", topic="测试话题")

        # 手动修改decision对象，使其包含不可序列化的数据
        decision_dict = decision.model_dump()
        decision_dict["unserializable"] = UnserializableObject()

        # 由于无法直接修改Pydantic模型，我们使用一个字典代替
        result = json_serialize_context(chat_records, decision_dict, needs_search=False)
        context = json.loads(result)

        # 由于我们使用了 default=str，不可序列化的对象会被转换为字符串
        # 所以不会触发错误，而是成功序列化
        assert 'error' not in context
        assert 'unserializable' in context['secretary_decision']

    def test_access_example(self):
        """测试访问示例代码（模拟其他插件的读取）"""
        # 创建测试上下文
        chat_records = [{"role": "user", "content": "test"}]
        decision = SecretaryDecision(should_reply=True, reply_strategy="test", topic="test")
        serialized = json_serialize_context(chat_records, decision)

        # 模拟事件对象
        mock_event = Mock()
        mock_event.angelheart_context = serialized

        # 模拟读取逻辑
        if hasattr(mock_event, 'angelheart_context'):
            context = json.loads(mock_event.angelheart_context)
            chat_records_read = context['chat_records']
            secretary_decision = context['secretary_decision']
            needs_search = context['needs_search']

            assert len(chat_records_read) == 1
            assert secretary_decision['should_reply'] is True
            assert needs_search is False

    def test_invalid_json_handling(self):
        """测试无效JSON处理"""
        mock_event = Mock()
        mock_event.angelheart_context = "invalid json"

        # 模拟异常处理
        try:
            context = json.loads(mock_event.angelheart_context)
            assert False, "Should have raised exception"
        except json.JSONDecodeError:
            # 正确处理异常
            assert True

    def test_error_context_handling(self):
        """测试错误上下文的处理"""
        # 创建一个包含错误的上下文
        error_context = {
            "chat_records": [],
            "secretary_decision": {"should_reply": False, "error": "序列化失败"},
            "needs_search": False,
            "error": "序列化失败"
        }
        serialized = json.dumps(error_context)

        # 模拟事件对象
        mock_event = Mock()
        mock_event.angelheart_context = serialized

        # 模拟读取逻辑
        if hasattr(mock_event, 'angelheart_context'):
            context = json.loads(mock_event.angelheart_context)

            # 检查错误处理
            assert 'error' in context
            assert context['error'] == "序列化失败"
            assert context['secretary_decision']['error'] == "序列化失败"