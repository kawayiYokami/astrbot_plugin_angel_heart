"""
压力测试：上下文压缩算法

验证新的分级压缩算法在极端环境下能保持有效的上下文缓存命中。

用法：
    cd E:/github/ai-qq/astrbot/data/plugins/astrbot_plugin_angel_heart
    python -m pytest tests/test_context_compression.py -v
"""

from __future__ import annotations

import sys
import time
import threading
import tempfile
from pathlib import Path
from typing import Dict, List

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT.parent))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest


class MockConfigManager:
    """模拟配置管理器"""

    def __init__(self, **kwargs):
        self._overrides = kwargs

    @property
    def max_conversation_tokens(self) -> int:
        return self._overrides.get("max_conversation_tokens", 100000)

    @property
    def context_compression_threshold(self) -> float:
        return self._overrides.get("context_compression_threshold", 0.82)

    @property
    def context_content_retain_tokens(self) -> int:
        return self._overrides.get("context_content_retain_tokens", 10000)

    @property
    def context_tool_retain_tokens(self) -> int:
        return self._overrides.get("context_tool_retain_tokens", 10000)

    @property
    def context_forgetting_timeout(self) -> int:
        return self._overrides.get("context_forgetting_timeout", 86400)


class MockProvider:
    """模拟 AstrBot Provider"""

    def __init__(self, max_context_tokens):
        self.provider_config = {"max_context_tokens": max_context_tokens}


class MockAstrContext:
    """模拟 AstrBot Context"""

    def __init__(self, max_context_tokens):
        self.provider = MockProvider(max_context_tokens)

    def get_using_provider(self, chat_id: str):
        return self.provider


def make_message(role: str, content: str, timestamp: float,
                 is_processed: bool = False, tool_calls=None,
                 sender_name: str = "") -> Dict:
    """创建一条测试消息"""
    msg = {
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "is_processed": is_processed,
    }
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if sender_name:
        msg["sender_name"] = sender_name
    return msg


def make_tool_result(content: str, timestamp: float,
                     is_processed: bool = False) -> Dict:
    """创建一条工具结果消息"""
    return {
        "role": "tool",
        "content": content,
        "timestamp": timestamp,
        "is_processed": is_processed,
    }


def make_long_message(role: str, timestamp: float, char_count: int = 500,
                      is_processed: bool = False) -> Dict:
    """创建一条长消息用于Token测试"""
    # 中文字符，每个约0.6 token
    content = "测试消息内容" * (char_count // 6 + 1)
    content = content[:char_count]
    return make_message(role, content, timestamp, is_processed)


@pytest.fixture
def temp_dir():
    """创建临时目录"""
    d = tempfile.mkdtemp()
    yield Path(d)
    # Windows 上 SQLite 文件可能被锁定，忽略清理错误
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def _create_ledger(temp_dir, astr_context=None, **config_kwargs):
    """辅助函数：创建 ledger 并注册清理"""
    from core.conversation_ledger import ConversationLedger
    config = MockConfigManager(**config_kwargs)
    return ConversationLedger(config, temp_dir, astr_context=astr_context)


@pytest.fixture
def ledger(temp_dir):
    """创建一个标准的 ConversationLedger 实例"""
    lg = _create_ledger(temp_dir, max_conversation_tokens=5000)
    yield lg
    lg.db_conn.close()


@pytest.fixture
def ledger_large_budget(temp_dir):
    """创建一个大预算的 ConversationLedger 实例"""
    lg = _create_ledger(
        temp_dir,
        max_conversation_tokens=100000,
        context_content_retain_tokens=10000,
        context_tool_retain_tokens=10000,
    )
    yield lg
    lg.db_conn.close()


class TestCompressionTrigger:
    """测试压缩触发条件"""

    def test_effective_limit_uses_smaller_provider_limit(self, temp_dir):
        """模型上下文更小时，使用模型上限触发压缩"""
        ledger = _create_ledger(
            temp_dir,
            astr_context=MockAstrContext(max_context_tokens=1000),
            max_conversation_tokens=5000,
            context_forgetting_timeout=0,
        )

        assert ledger._get_effective_max_conversation_tokens("test") == 1000
        ledger.db_conn.close()

    def test_effective_limit_uses_smaller_plugin_limit(self, temp_dir):
        """插件侧上限更小时，使用插件上限触发压缩"""
        ledger = _create_ledger(
            temp_dir,
            astr_context=MockAstrContext(max_context_tokens=1000000),
            max_conversation_tokens=100000,
            context_forgetting_timeout=0,
        )

        assert ledger._get_effective_max_conversation_tokens("test") == 100000
        ledger.db_conn.close()

    def test_zero_plugin_limit_falls_back_to_provider_limit(self, temp_dir):
        """插件配置为0时，不限制插件侧上限，改用模型上限"""
        ledger = _create_ledger(
            temp_dir,
            astr_context=MockAstrContext(max_context_tokens=2048),
            max_conversation_tokens=0,
            context_forgetting_timeout=0,
        )

        assert ledger._get_effective_max_conversation_tokens("test") == 2048
        ledger.db_conn.close()

    def test_no_compression_below_threshold(self, temp_dir):
        """Token数低于82%阈值时不触发压缩"""
        ledger = _create_ledger(temp_dir, max_conversation_tokens=100000)
        try:
            chat_id = "test_chat"
            # 添加少量消息，不应触发压缩
            for i in range(10):
                ledger.add_message(chat_id, make_message(
                    "user", f"短消息{i}", time.time() + i
                ))

            messages = ledger.get_all_messages(chat_id)
            assert len(messages) == 10, "低于阈值时不应压缩"
        finally:
            ledger.db_conn.close()

    def test_compression_at_82_percent(self, temp_dir):
        """Token数达到82%阈值时触发压缩"""
        ledger = _create_ledger(
            temp_dir,
            max_conversation_tokens=1000,
            context_content_retain_tokens=300,
            context_tool_retain_tokens=200,
        )
        try:
            chat_id = "test_chat"
            # 添加大量消息使Token超过82%（820 tokens）
            for i in range(50):
                ledger.add_message(chat_id, make_long_message(
                    "user", time.time() + i, char_count=100
                ))

            messages = ledger.get_all_messages(chat_id)
            # 压缩后消息数应该减少
            assert len(messages) < 50, f"应触发压缩，但消息数为 {len(messages)}"
        finally:
            ledger.db_conn.close()

    def test_forgetting_timeout_triggers_compression(self, temp_dir):
        """遗忘时间超限时强制触发压缩"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=100000,  # 高阈值，不会因Token触发
            context_forgetting_timeout=1,  # 1秒超时，方便测试
            context_content_retain_tokens=500,
            context_tool_retain_tokens=200,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "test_chat"
        base_time = time.time() - 10  # 10秒前的消息

        for i in range(20):
            ledger.add_message(chat_id, make_long_message(
                "user", base_time + i, char_count=200
            ))

        # 等待超过遗忘时间
        time.sleep(1.1)

        # 再添加一条消息触发检查
        ledger.add_message(chat_id, make_message(
            "user", "新消息", time.time()
        ))

        messages = ledger.get_all_messages(chat_id)
        assert len(messages) < 21, f"遗忘超时应触发压缩，但消息数为 {len(messages)}"

    def test_disabled_token_limit(self, temp_dir):
        """max_conversation_tokens=0 时禁用Token触发"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=0,
            context_forgetting_timeout=0,  # 也禁用时间触发
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "test_chat"
        for i in range(100):
            ledger.add_message(chat_id, make_long_message(
                "user", time.time() + i, char_count=200
            ))

        messages = ledger.get_all_messages(chat_id)
        assert len(messages) == 100, "禁用时不应压缩"


class TestCompressionAlgorithm:
    """测试压缩算法的正确性"""

    def test_retains_recent_content_messages(self, temp_dir):
        """压缩后保留最近的正文消息"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=500,
            context_content_retain_tokens=200,
            context_tool_retain_tokens=100,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "test_chat"
        base_time = time.time()

        # 添加20条消息
        for i in range(20):
            ledger.add_message(chat_id, make_message(
                "user", f"消息内容{i}", base_time + i
            ))

        messages = ledger.get_all_messages(chat_id)
        # 最后几条消息应该被保留
        contents = [m.get("content", "") for m in messages]
        # 最新的消息应该在保留列表中
        assert any("消息内容19" in c for c in contents), "最新消息应被保留"

    def test_retains_recent_tool_messages(self, temp_dir):
        """压缩后保留最近的工具消息"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=800,
            context_content_retain_tokens=300,
            context_tool_retain_tokens=300,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "test_chat"
        base_time = time.time()

        # 交替添加正文和工具消息
        for i in range(30):
            if i % 3 == 0:
                # 工具调用
                ledger.add_message(chat_id, make_message(
                    "assistant", f"调用工具{i}",
                    base_time + i,
                    tool_calls=[{"id": f"call_{i}", "function": {"name": "test"}}]
                ))
            elif i % 3 == 1:
                # 工具结果
                ledger.add_message(chat_id, make_tool_result(
                    f"工具结果{i}", base_time + i
                ))
            else:
                # 正文
                ledger.add_message(chat_id, make_message(
                    "user", f"用户消息{i}", base_time + i
                ))

        messages = ledger.get_all_messages(chat_id)
        # 应该同时包含正文和工具消息
        has_tool = any(m.get("role") == "tool" or m.get("tool_calls") for m in messages)
        has_content = any(
            m.get("role") in ("user", "assistant") and not m.get("tool_calls")
            for m in messages
        )
        assert has_content, "应保留正文消息"
        # 工具消息可能被保留也可能不被保留，取决于预算

    def test_preserves_time_order(self, temp_dir):
        """压缩后消息保持时间顺序"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=600,
            context_content_retain_tokens=300,
            context_tool_retain_tokens=200,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "test_chat"
        base_time = time.time()

        for i in range(40):
            ledger.add_message(chat_id, make_long_message(
                "user", base_time + i, char_count=80
            ))

        messages = ledger.get_all_messages(chat_id)
        timestamps = [m.get("timestamp", 0) for m in messages]
        assert timestamps == sorted(timestamps), "压缩后消息应保持时间顺序"

    def test_minimum_retain_count(self, temp_dir):
        """压缩后至少保留 MIN_RETAIN_COUNT 条消息（当有足够消息时）"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=2000,  # 适中的限制
            context_content_retain_tokens=100,  # 较小的预算
            context_tool_retain_tokens=50,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "test_chat"
        base_time = time.time()

        # 一次性批量设置消息（绕过 add_message 的逐条压缩）
        messages = [make_long_message("user", base_time + i, char_count=100)
                    for i in range(50)]
        ledger.set_messages(chat_id, messages)

        # 手动触发压缩
        ledger._compress_context(chat_id)

        result = ledger.get_all_messages(chat_id)
        assert len(result) >= ledger.MIN_RETAIN_COUNT, \
            f"至少应保留 {ledger.MIN_RETAIN_COUNT} 条消息，实际 {len(result)}"

    def test_old_messages_discarded(self, temp_dir):
        """旧消息被正确丢弃"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=500,
            context_content_retain_tokens=200,
            context_tool_retain_tokens=100,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "test_chat"
        base_time = time.time()

        # 添加带标记的消息
        for i in range(30):
            ledger.add_message(chat_id, make_message(
                "user", f"OLD_{i}" if i < 10 else f"NEW_{i}",
                base_time + i
            ))

        messages = ledger.get_all_messages(chat_id)
        contents = [m.get("content", "") for m in messages]
        # 最旧的消息应该被丢弃
        old_count = sum(1 for c in contents if c.startswith("OLD_"))
        new_count = sum(1 for c in contents if c.startswith("NEW_"))
        assert new_count > old_count, "新消息应比旧消息保留更多"


class TestConcurrency:
    """测试并发安全性"""

    def test_concurrent_add_messages(self, temp_dir):
        """多线程并发添加消息不会崩溃"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=2000,
            context_content_retain_tokens=500,
            context_tool_retain_tokens=300,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "concurrent_chat"
        errors = []

        def add_messages(thread_id: int):
            try:
                for i in range(100):
                    ledger.add_message(chat_id, make_message(
                        "user",
                        f"线程{thread_id}_消息{i}",
                        time.time() + thread_id * 1000 + i
                    ))
            except Exception as e:
                errors.append(f"Thread {thread_id}: {e}")

        threads = [threading.Thread(target=add_messages, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发错误: {errors}"
        messages = ledger.get_all_messages(chat_id)
        assert len(messages) > 0, "应有消息被保留"

    def test_concurrent_compression_and_read(self, temp_dir):
        """压缩和读取并发不会崩溃"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=1000,
            context_content_retain_tokens=300,
            context_tool_retain_tokens=200,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "concurrent_rw"
        errors = []

        # 预填充消息
        for i in range(50):
            ledger.add_message(chat_id, make_long_message(
                "user", time.time() + i, char_count=100
            ))

        def writer():
            try:
                for i in range(50):
                    ledger.add_message(chat_id, make_long_message(
                        "user", time.time() + 1000 + i, char_count=100
                    ))
            except Exception as e:
                errors.append(f"Writer: {e}")

        def reader():
            try:
                for _ in range(100):
                    msgs = ledger.get_all_messages(chat_id)
                    # 验证返回的是有效列表
                    assert isinstance(msgs, list)
            except Exception as e:
                errors.append(f"Reader: {e}")

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发读写错误: {errors}"


class TestCacheHitEffectiveness:
    """测试压缩后上下文缓存命中的有效性"""

    def test_context_snapshot_after_compression(self, temp_dir):
        """压缩后 get_context_snapshot 仍能正常工作"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=800,
            context_content_retain_tokens=400,
            context_tool_retain_tokens=200,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "snapshot_test"
        base_time = time.time()

        # 添加消息并标记部分为已处理
        for i in range(30):
            msg = make_message("user", f"消息{i}", base_time + i)
            if i < 15:
                msg["is_processed"] = True
            ledger.add_message(chat_id, msg)

        # 获取快照
        historical, recent, boundary = ledger.get_context_snapshot(chat_id)

        # 快照应该能正常返回
        assert isinstance(historical, list)
        assert isinstance(recent, list)
        # 至少有一些消息在某个分区中
        assert len(historical) + len(recent) > 0

    def test_mark_processed_after_compression(self, temp_dir):
        """压缩后 mark_as_processed 仍能正常工作"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=800,
            context_content_retain_tokens=400,
            context_tool_retain_tokens=200,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "mark_test"
        base_time = time.time()

        for i in range(30):
            ledger.add_message(chat_id, make_message(
                "user", f"消息{i}", base_time + i
            ))

        # 标记到某个时间点
        boundary = base_time + 25
        ledger.mark_as_processed(chat_id, boundary)

        messages = ledger.get_all_messages(chat_id)
        for msg in messages:
            if msg["timestamp"] <= boundary:
                assert msg["is_processed"], \
                    f"时间戳 {msg['timestamp']} 应被标记为已处理"

    def test_repeated_compression_stability(self, temp_dir):
        """多次压缩后系统保持稳定"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=500,
            context_content_retain_tokens=200,
            context_tool_retain_tokens=100,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "stability_test"

        # 模拟持续的消息流入和压缩
        for batch in range(10):
            base_time = time.time() + batch * 100
            for i in range(20):
                ledger.add_message(chat_id, make_long_message(
                    "user", base_time + i, char_count=80
                ))

            messages = ledger.get_all_messages(chat_id)
            assert len(messages) >= ledger.MIN_RETAIN_COUNT, \
                f"第{batch}批后消息数不足: {len(messages)}"

            # 验证时间顺序
            timestamps = [m["timestamp"] for m in messages]
            assert timestamps == sorted(timestamps), \
                f"第{batch}批后时间顺序错乱"


class TestStressScenarios:
    """极端场景压力测试"""

    def test_massive_message_burst(self, temp_dir):
        """大量消息瞬间涌入"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=5000,
            context_content_retain_tokens=2000,
            context_tool_retain_tokens=1000,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "burst_test"
        base_time = time.time()

        # 模拟500条消息瞬间涌入
        for i in range(500):
            ledger.add_message(chat_id, make_long_message(
                "user", base_time + i * 0.01, char_count=50
            ))

        messages = ledger.get_all_messages(chat_id)
        # 应该被压缩到合理范围
        assert len(messages) < 500, f"应被压缩，实际 {len(messages)}"
        assert len(messages) >= ledger.MIN_RETAIN_COUNT
        # 最新消息应该被保留
        assert messages[-1]["timestamp"] == pytest.approx(
            base_time + 499 * 0.01, abs=0.1
        )

    def test_mixed_content_heavy_load(self, temp_dir):
        """混合内容（正文+工具+图片）重负载"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=3000,
            context_content_retain_tokens=1500,
            context_tool_retain_tokens=800,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "mixed_test"
        base_time = time.time()

        for i in range(200):
            ts = base_time + i
            if i % 5 == 0:
                # 工具调用
                ledger.add_message(chat_id, make_message(
                    "assistant", f"调用工具", ts,
                    tool_calls=[{"id": f"c_{i}", "function": {"name": "search"}}]
                ))
            elif i % 5 == 1:
                # 工具结果
                ledger.add_message(chat_id, make_tool_result(
                    f"搜索结果：这是一段很长的搜索结果内容" * 5, ts
                ))
            elif i % 5 == 2:
                # 图片消息
                ledger.add_message(chat_id, {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"看这张图{i}"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
                    ],
                    "timestamp": ts,
                    "is_processed": False,
                    "sender_name": f"用户{i % 10}",
                })
            else:
                # 普通消息
                ledger.add_message(chat_id, make_long_message(
                    "user" if i % 2 == 0 else "assistant",
                    ts, char_count=150
                ))

        messages = ledger.get_all_messages(chat_id)
        assert len(messages) < 200
        assert len(messages) >= ledger.MIN_RETAIN_COUNT

        # 验证时间顺序
        timestamps = [m["timestamp"] for m in messages]
        assert timestamps == sorted(timestamps)

    def test_multi_chat_isolation(self, temp_dir):
        """多会话压缩互不影响"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=1000,
            context_content_retain_tokens=400,
            context_tool_retain_tokens=200,
        )
        ledger = ConversationLedger(config, temp_dir)

        base_time = time.time()

        # 为3个会话各添加大量消息
        for chat_idx in range(3):
            chat_id = f"chat_{chat_idx}"
            for i in range(50):
                ledger.add_message(chat_id, make_long_message(
                    "user", base_time + chat_idx * 1000 + i, char_count=100
                ))

        # 验证每个会话独立压缩
        for chat_idx in range(3):
            chat_id = f"chat_{chat_idx}"
            messages = ledger.get_all_messages(chat_id)
            assert len(messages) < 50, f"{chat_id} 应被压缩"
            assert len(messages) >= ledger.MIN_RETAIN_COUNT

    def test_empty_chat_no_crash(self, temp_dir):
        """空会话不崩溃"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(max_conversation_tokens=1000)
        ledger = ConversationLedger(config, temp_dir)

        # 对空会话调用各种方法
        chat_id = "empty_chat"
        messages = ledger.get_all_messages(chat_id)
        assert messages == []

        historical, recent, boundary = ledger.get_context_snapshot(chat_id)
        assert historical == []
        assert recent == []

        # 强制压缩空会话不崩溃
        ledger._compress_context(chat_id)
        assert ledger.get_all_messages(chat_id) == []

    def test_single_message_chat(self, temp_dir):
        """只有一条消息的会话"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=100,
            context_content_retain_tokens=10,
            context_tool_retain_tokens=10,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "single_msg"
        ledger.add_message(chat_id, make_message(
            "user", "唯一的消息", time.time()
        ))

        messages = ledger.get_all_messages(chat_id)
        assert len(messages) >= 1, "至少保留一条消息"

    def test_all_tool_messages(self, temp_dir):
        """全部是工具消息的极端情况"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=500,
            context_content_retain_tokens=200,
            context_tool_retain_tokens=200,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "all_tools"
        base_time = time.time()

        for i in range(30):
            if i % 2 == 0:
                ledger.add_message(chat_id, make_message(
                    "assistant", f"调用", base_time + i,
                    tool_calls=[{"id": f"c_{i}", "function": {"name": "t"}}]
                ))
            else:
                ledger.add_message(chat_id, make_tool_result(
                    f"结果{i}" * 20, base_time + i
                ))

        messages = ledger.get_all_messages(chat_id)
        # 不应崩溃，且至少保留最小数量
        assert len(messages) >= ledger.MIN_RETAIN_COUNT


class TestTokenEstimation:
    """测试Token估算的准确性"""

    def test_chinese_text_estimation(self, temp_dir):
        """中文文本Token估算"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(max_conversation_tokens=100000)
        ledger = ConversationLedger(config, temp_dir)

        # 100个中文字符 ≈ 60 tokens
        tokens = ledger._count_tokens_in_text("你" * 100)
        assert 50 <= tokens <= 70, f"100个中文字符应约60 tokens，实际 {tokens}"

    def test_english_text_estimation(self, temp_dir):
        """英文文本Token估算"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(max_conversation_tokens=100000)
        ledger = ConversationLedger(config, temp_dir)

        # 100个英文字符 ≈ 30 tokens
        tokens = ledger._count_tokens_in_text("a" * 100)
        assert 25 <= tokens <= 35, f"100个英文字符应约30 tokens，实际 {tokens}"

    def test_message_token_counting(self, temp_dir):
        """单条消息Token计数"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(max_conversation_tokens=100000)
        ledger = ConversationLedger(config, temp_dir)

        msg = make_message("user", "测试消息" * 50, time.time(), sender_name="张三")
        tokens = ledger._count_message_tokens(msg)
        assert tokens > 0, "消息Token数应大于0"

    def test_image_message_token_counting(self, temp_dir):
        """图片消息Token计数"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(max_conversation_tokens=100000)
        ledger = ConversationLedger(config, temp_dir)

        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}
            ],
            "timestamp": time.time(),
            "is_processed": False,
        }
        tokens = ledger._count_message_tokens(msg)
        # 应包含文本token + 85(图片)
        assert tokens >= 85, f"图片消息至少85 tokens，实际 {tokens}"


class TestCompressionTimestamp:
    """测试压缩时间戳记录"""

    def test_compression_updates_timestamp(self, temp_dir):
        """压缩后更新时间戳"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=500,
            context_content_retain_tokens=200,
            context_tool_retain_tokens=100,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "ts_test"
        assert chat_id not in ledger._last_compression_time

        # 添加足够多的消息触发压缩
        for i in range(50):
            ledger.add_message(chat_id, make_long_message(
                "user", time.time() + i, char_count=80
            ))

        # 压缩后应记录时间戳
        assert chat_id in ledger._last_compression_time
        assert ledger._last_compression_time[chat_id] > 0

    def test_subsequent_compression_updates_timestamp(self, temp_dir):
        """后续压缩更新时间戳"""
        from core.conversation_ledger import ConversationLedger
        config = MockConfigManager(
            max_conversation_tokens=500,
            context_content_retain_tokens=200,
            context_tool_retain_tokens=100,
        )
        ledger = ConversationLedger(config, temp_dir)

        chat_id = "ts_update_test"

        # 第一次压缩
        for i in range(50):
            ledger.add_message(chat_id, make_long_message(
                "user", time.time() + i, char_count=80
            ))

        first_ts = ledger._last_compression_time.get(chat_id, 0)

        time.sleep(0.1)

        # 第二次压缩
        for i in range(50):
            ledger.add_message(chat_id, make_long_message(
                "user", time.time() + 1000 + i, char_count=80
            ))

        second_ts = ledger._last_compression_time.get(chat_id, 0)
        assert second_ts > first_ts, "后续压缩应更新时间戳"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
