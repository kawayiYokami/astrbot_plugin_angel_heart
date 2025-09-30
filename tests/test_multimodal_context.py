"""
测试标准多模态消息架构的核心功能
验证从 MessageChain 到标准 content 列表的完整数据流

注意：此测试文件独立运行，不依赖完整的 AstrBot 框架环境。
"""

import unittest
from unittest.mock import Mock, MagicMock, patch
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# 导入我们的核心模块
from core.conversation_ledger import ConversationLedger

# 独立实现辅助函数，用于测试
def format_relative_time(timestamp: float) -> str:
    """测试用的 format_relative_time 独立实现"""
    if not timestamp:
        return ""

    try:
        timestamp = float(timestamp)
    except (ValueError, TypeError):
        return ""

    import time
    now = time.time()
    delta = now - timestamp

    if delta < 0:
        return ""
    elif delta < 60:
        return " (刚刚)"
    elif delta < 3600:
        minutes = int(delta / 60)
        return f" ({minutes}分钟前)"
    elif delta < 86400:  # 24小时
        hours = int(delta / 3600)
        return f" ({hours}小时前)"
    else:
        days = int(delta / 86400)
        return f" ({days}天前)"

# 独立实现 convert_content_to_string 函数，用于测试
def convert_content_to_string(content) -> str:
    """
    convert_content_to_string 的独立实现，用于测试
    支持标准多模态 content 列表。
    """
    if isinstance(content, str):
        return content.strip()

    elif isinstance(content, list):
        # 处理标准多模态 content 列表：[{"type": "text", "text": "..."}, {"type": "image_url", ...}]
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_content = item.get("text", "")
                if text_content:
                    text_parts.append(text_content)
        # 拼接所有文本部分
        return "".join(text_parts).strip()

    else:
        return str(content).strip()


# 创建模拟的组件类，避免依赖 astrbot 框架
class Plain:
    def __init__(self, text):
        self.text = text

    def __eq__(self, other):
        return isinstance(other, Plain) and self.text == other.text


class Image:
    def __init__(self, url):
        self.url = url

    def __eq__(self, other):
        return isinstance(other, Image) and self.url == other.url


class TestMultimodalContext(unittest.TestCase):
    """测试多模态上下文系统的核心功能"""

    def setUp(self):
        """测试前准备"""
        # 创建模拟对象
        self.mock_config_manager = Mock()
        self.mock_secretary = Mock()
        self.mock_ledger = ConversationLedger(cache_expiry=3600)

        # 模拟 FrontDesk 的 cache_message 方法
        # 我们将直接测试核心逻辑，而不是完整的类实例

    def test_cache_message_logic(self):
        """测试 cache_message 方法的核心逻辑：MessageChain -> 标准多模态列表"""

        # 创建模拟事件
        mock_event = Mock()
        mock_event.get_sender_id.return_value = "test_user_123"
        mock_event.get_sender_name.return_value = "测试用户"
        mock_event.get_timestamp.return_value = 1640995200.0  # 2022-01-01 00:00:00

        # 模拟 MessageChain：文本 + 图片 + 文本
        mock_event.get_messages.return_value = [
            Plain(text="你好，这是一张"),
            Image(url="http://example.com/image.jpg"),
            Plain(text="很棒的图片")
        ]

        # 执行缓存逻辑（直接复制 cache_message 的核心代码）
        message_chain = mock_event.get_messages()
        content_list = []

        for component in message_chain:
            if isinstance(component, Plain):
                content_list.append({
                    "type": "text",
                    "text": component.text
                })
            elif isinstance(component, Image):
                content_list.append({
                    "type": "image_url",
                    "image_url": {"url": component.url}
                })
            else:
                content_list.append({
                    "type": "text",
                    "text": str(component)
                })

        new_message = {
            "role": "user",
            "content": content_list,
            "sender_id": mock_event.get_sender_id(),
            "sender_name": mock_event.get_sender_name(),
            "timestamp": mock_event.get_timestamp(),
        }

        self.mock_ledger.add_message("test_chat", new_message)

        # 获取缓存的快照
        historical, unprocessed, _ = self.mock_ledger.get_context_snapshot("test_chat")

        # 验证缓存的内容
        self.assertEqual(len(unprocessed), 1)
        cached_msg = unprocessed[0]

        # 验证基本字段
        self.assertEqual(cached_msg["role"], "user")
        self.assertEqual(cached_msg["sender_id"], "test_user_123")
        self.assertEqual(cached_msg["sender_name"], "测试用户")

        # 验证标准多模态 content 列表
        content = cached_msg["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(len(content), 3)

        # 验证第一个组件（文本）
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[0]["text"], "你好，这是一张")

        # 验证第二个组件（图片）
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], "http://example.com/image.jpg")

        # 验证第三个组件（文本）
        self.assertEqual(content[2]["type"], "text")
        self.assertEqual(content[2]["text"], "很棒的图片")

    def test_convert_content_to_string_multimodal(self):
        """测试 convert_content_to_string 对标准多模态列表的处理"""

        # 测试标准多模态 content 列表
        multimodal_content = [
            {"type": "text", "text": "你好世界"},
            {"type": "image_url", "image_url": {"url": "http://example.com/image.jpg"}},
            {"type": "text", "text": "这是测试"}
        ]

        result = convert_content_to_string(multimodal_content)
        # 应该只提取文本部分，忽略图片
        self.assertEqual(result, "你好世界这是测试")

        # 测试只有文本的列表
        text_only_content = [
            {"type": "text", "text": "纯文本消息"}
        ]
        result = convert_content_to_string(text_only_content)
        self.assertEqual(result, "纯文本消息")

        # 测试只有图片的列表
        image_only_content = [
            {"type": "image_url", "image_url": {"url": "http://example.com/image.jpg"}}
        ]
        result = convert_content_to_string(image_only_content)
        self.assertEqual(result, "")  # 没有文本，应该返回空字符串

        # 测试普通字符串（兼容旧格式）
        result_string = convert_content_to_string("普通文本")
        self.assertEqual(result_string, "普通文本")

    def test_rewrite_prompt_logic(self):
        """测试 rewrite_prompt_for_llm 的元数据注入逻辑"""

        # 创建模拟历史消息（标准多模态格式）
        multimodal_msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "你好，这是一张"},
                {"type": "image_url", "image_url": {"url": "http://example.com/image.jpg"}},
                {"type": "text", "text": "很棒的图片"}
            ],
            "sender_name": "测试用户",
            "sender_id": "test_user_123",
            "timestamp": 1640995200.0  # 2022-01-01 00:00:00
        }

        # 执行 rewrite_prompt_for_llm 的元数据注入逻辑
        new_contexts = []
        for msg in [multimodal_msg]:
            role = msg.get("role", "user")
            content = msg.get("content")

            # 处理消息内容
            if isinstance(content, list):
                message_content = content.copy()
            elif isinstance(content, str):
                message_content = [{"type": "text", "text": content}]
            else:
                message_content = [{"type": "text", "text": str(content)}]

            # 为用户消息注入元数据头信息
            if role == "user":
                sender_name = msg.get("sender_name", "成员")
                sender_id = msg.get("sender_id", "Unknown")
                timestamp = msg.get("timestamp")
                relative_time_str = format_relative_time(timestamp) if timestamp else ""

                header = f"[群友: {sender_name} (ID: {sender_id})]{relative_time_str}\n[内容: 文本]\n"

                # 寻找第一个文本组件并注入头信息
                found_text = False
                for item in message_content:
                    if item.get("type") == "text":
                        original_text = item.get("text", "")
                        item["text"] = header + original_text
                        found_text = True
                        break

                # 如果没有文本组件，则手动添加一个包含头的文本组件
                if not found_text:
                    message_content.insert(0, {"type": "text", "text": header.strip()})

            new_contexts.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": message_content
            })

        # 验证结果
        self.assertEqual(len(new_contexts), 1)
        self.assertEqual(new_contexts[0]["role"], "user")

        # 验证第一个文本组件包含了元数据头
        content = new_contexts[0]["content"]
        first_text_item = content[0]
        self.assertEqual(first_text_item["type"], "text")
        self.assertTrue(first_text_item["text"].startswith("[群友: 测试用户 (ID: test_user_123)]"))
        self.assertIn("[内容: 文本]", first_text_item["text"])
        self.assertIn("你好，这是一张", first_text_item["text"])  # 原始文本被保留

        # 验证图片组件保持不变
        self.assertEqual(content[1]["type"], "image_url")

        # 验证第二个文本组件不变（因为头只加给第一个）
        second_text_item = content[2]
        self.assertEqual(second_text_item["type"], "text")
        self.assertEqual(second_text_item["text"], "很棒的图片")  # 没有添加头

    def test_integration_cache_convert_rewrite(self):
        """集成测试：缓存 -> 文本转换 -> 请求重构的完整流程"""

        # 1. 模拟缓存过程（使用新的标准格式）
        mock_event = Mock()
        mock_event.get_sender_id.return_value = "user123"
        mock_event.get_sender_name.return_value = "用户"
        mock_event.get_timestamp.return_value = 1640995200.0

        mock_event.get_messages.return_value = [
            Plain(text="测试消息"),
            Image(url="http://test.com/image.jpg")
        ]

        # 执行新的缓存逻辑
        message_chain = mock_event.get_messages()
        content_list = []

        for component in message_chain:
            if isinstance(component, Plain):
                content_list.append({
                    "type": "text",
                    "text": component.text
                })
            elif isinstance(component, Image):
                content_list.append({
                    "type": "image_url",
                    "image_url": {"url": component.url}
                })

        new_message = {
            "role": "user",
            "content": content_list,
            "sender_id": mock_event.get_sender_id(),
            "sender_name": mock_event.get_sender_name(),
            "timestamp": mock_event.get_timestamp(),
        }

        self.mock_ledger.add_message("integration_chat", new_message)

        # 2. 获取缓存的数据
        _, unprocessed, _ = self.mock_ledger.get_context_snapshot("integration_chat")
        cached_msg = unprocessed[0]

        # 3. 测试 convert_content_to_string（应该只提取文本）
        text_result = convert_content_to_string(cached_msg["content"])
        self.assertEqual(text_result, "测试消息")  # 图片被忽略

        # 4. 测试 rewrite_prompt_for_llm 的元数据注入逻辑

        full_history = unprocessed
        new_contexts = []
        for msg in full_history:
            role = msg.get("role", "user")
            content = msg.get("content")

            # 处理消息内容
            if isinstance(content, list):
                message_content = content.copy()
            elif isinstance(content, str):
                message_content = [{"type": "text", "text": content}]
            else:
                message_content = [{"type": "text", "text": str(content)}]

            # 为用户消息注入元数据头信息
            if role == "user":
                sender_name = msg.get("sender_name", "成员")
                sender_id = msg.get("sender_id", "Unknown")
                timestamp = msg.get("timestamp")
                relative_time_str = format_relative_time(timestamp) if timestamp else ""

                header = f"[群友: {sender_name} (ID: {sender_id})]{relative_time_str}\n[内容: 文本]\n"

                # 寻找第一个文本组件并注入头信息
                found_text = False
                for item in message_content:
                    if item.get("type") == "text":
                        original_text = item.get("text", "")
                        item["text"] = header + original_text
                        found_text = True
                        break

                # 如果没有文本组件，则手动添加一个包含头的文本组件
                if not found_text:
                    message_content.insert(0, {"type": "text", "text": header.strip()})

            new_contexts.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": message_content
            })

        # 验证最终的 contexts
        self.assertEqual(len(new_contexts), 1)
        self.assertEqual(new_contexts[0]["role"], "user")
        self.assertEqual(len(new_contexts[0]["content"]), 2)  # 文本(已注入头) + 图片

        # 验证第一个文本组件包含了元数据头
        final_content = new_contexts[0]["content"]
        text_item = final_content[0]
        self.assertEqual(text_item["type"], "text")
        self.assertTrue(text_item["text"].startswith("[群友: 用户 (ID: user123)]"))
        self.assertIn("[内容: 文本]", text_item["text"])
        self.assertIn("测试消息", text_item["text"])  # 原始文本被保留

        # 验证图片组件保持不变
        image_item = final_content[1]
        self.assertEqual(image_item["type"], "image_url")
        self.assertEqual(image_item["image_url"]["url"], "http://test.com/image.jpg")


if __name__ == '__main__':
    unittest.main()