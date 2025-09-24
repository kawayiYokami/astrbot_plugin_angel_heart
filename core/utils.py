"""
AngelHeart 插件 - 核心工具函数
"""

import time
from astrbot.api import logger
from markdown_it import MarkdownIt
from mdit_plain.renderer import RendererPlain

# 定义默认时间戳回退时间（1小时），用于当消息没有时间戳时提供一个基准时间
DEFAULT_TIMESTAMP_FALLBACK_SECONDS = 3600

# 创建一个全局的 MarkdownIt 实例用于 strip_markdown 函数，以提高性能
_md_strip_instance = MarkdownIt(renderer_cls=RendererPlain)


def get_latest_message_time(messages: list[dict]) -> float:
    """
    获取消息列表中最新消息的时间戳。

    Args:
        messages (List[Dict]): 消息列表。

    Returns:
        float: 最新消息的时间戳。如果列表为空或所有消息都无有效时间戳，则返回回退时间。
    """
    if not messages:
        return 0.0

    # 尝试从消息中提取时间戳
    latest_time = 0.0
    for msg in messages:
        # 优先使用消息自带的时间戳
        msg_time = msg.get("timestamp", 0)
        if isinstance(msg_time, (int, float)) and msg_time > latest_time:
            latest_time = msg_time

    # 如果所有消息都没有时间戳，使用当前时间作为基准
    if latest_time == 0.0:
        fallback_time = time.time() - DEFAULT_TIMESTAMP_FALLBACK_SECONDS
        logger.debug(
            f"AngelHeart: 消息时间戳回退到默认值 {fallback_time} ({DEFAULT_TIMESTAMP_FALLBACK_SECONDS}秒前)"
        )
        return fallback_time

    return latest_time


def convert_content_to_string(content) -> str:
    """
    将消息内容（可能是字符串或组件列表）转换为用于去重的字符串表示。
    """
    if isinstance(content, str):
        # 如果 content 是字符串，直接返回其 strip 后的结果
        return content.strip()
    elif isinstance(content, list):
        # 如果 content 是组件列表，将其转换为概要字符串
        # 例如: [{"type": "text", "data": {"text": "Hello"}}, {"type": "image", "data": {}}]
        # 转换为: "Hello [图片]"
        outline_parts = []
        for component in content:
            if isinstance(component, dict):
                comp_type = component.get("type", "")
                comp_data = component.get("data", {})
                if comp_type == "text":
                    text_content = comp_data.get("text", "")
                    if text_content:
                        outline_parts.append(text_content)
                elif comp_type == "image":
                    outline_parts.append("[图片]")
                elif comp_type == "at":
                    qq = comp_data.get("qq", "")
                    outline_parts.append(f"[At:{qq}]")
                else:
                    # 对于其他类型，添加一个通用的占位符
                    outline_parts.append(f"[{comp_type}]")
        return " ".join(outline_parts).strip()
    else:
        # 如果 content 是其他类型（理论上不应该发生），尝试转换为字符串
        return str(content).strip()


def format_relative_time(timestamp: float) -> str:
    """
    将Unix时间戳格式化为相对时间字符串。

    Args:
        timestamp (float): Unix时间戳。

    Returns:
        str: 相对时间字符串，例如 "(5分钟前)"。如果时间戳无效，则返回空字符串。
    """
    if not timestamp:
        return ""

    try:
        # 确保 timestamp 是数字类型
        timestamp = float(timestamp)
    except (ValueError, TypeError):
        return ""

    now = time.time()
    delta = now - timestamp

    if delta < 0:
        # 时间在未来，这通常表示有问题，返回空
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
        # 超过一天，可以考虑返回日期，这里简化处理
        days = int(delta / 86400)
        return f" ({days}天前)"


def strip_markdown(text: str) -> str:
    """
    使用 markdown-it-py 和 mdit_plain 库将 Markdown 文本转换为纯文本。

    Args:
        text (str): 包含 Markdown 格式的原始文本。

    Returns:
        str: 清洗后的纯文本。
    """
    # 使用全局的 MarkdownIt 实例以提高性能
    global _md_strip_instance
    # 渲染并返回纯文本
    return _md_strip_instance.render(text)


def prune_old_messages(
    cached_messages: list[dict], db_history: list[dict]
) -> list[dict]:
    """
    从 cached_messages 中移除已经存在于 db_history 中的消息，实现智能剪枝。
    为了高效去重，我们使用消息的时间戳作为唯一标识符。

    Args:
        cached_messages (List[Dict]): 前台缓存的最新消息列表。
        db_history (List[Dict]): 数据库中的历史消息列表。

    Returns:
        List[Dict]: 经过剪枝后，只包含新消息的列表。
    """
    # 1. 构建历史消息时间戳集合
    history_timestamps = {
        msg.get("timestamp") for msg in db_history if msg.get("timestamp")
    }

    # 2. 过滤缓存消息，只保留时间戳不在历史集合中的消息
    recent_dialogue = [
        msg for msg in cached_messages if msg.get("timestamp") not in history_timestamps
    ]

    return recent_dialogue


def format_message_for_llm(msg: dict, persona_name: str) -> str:
    """
    按照轻量模型能看到的格式格式化消息。

    Args:
        msg (dict): 消息字典，包含 role, content, sender_name, sender_id, timestamp 等字段。
        persona_name (str): AI 的人格名称，用于格式化助理消息。

    Returns:
        str: 格式化后的消息字符串。
    """
    role = msg.get("role")
    content = msg.get("content", "")

    if role == "assistant":
        # 助理消息格式: [助理: {persona_name}]\n[内容: 文本]\n{content}
        formatted_content = convert_content_to_string(content)
        return f"[助理: {persona_name}]\n[内容: 文本]\n{formatted_content}"
    elif role == "user":
        # 用户消息需要区分来源
        if "sender_name" in msg:
            # 来自缓存的新消息
            sender_id = msg.get("sender_id", "Unknown")
            sender_name = msg.get("sender_name", "成员")
            timestamp = msg.get("timestamp")
            relative_time_str = format_relative_time(timestamp)
            formatted_content = convert_content_to_string(content)

            # 判断内容类型
            content_type = "文本"
            if isinstance(content, str) and content.startswith("[图片]"):
                content_type = "图片"
            elif isinstance(content, list):
                temp_str = convert_content_to_string(content)
                if "[图片]" in temp_str:
                    content_type = "图片"

            # 新格式: [群友: 昵称 (ID: ...)] (相对时间)\n[内容: 类型]\n实际内容
            header = f"[群友: {sender_name} (ID: {sender_id})]{relative_time_str}"
            return f"{header}\n[内容: {content_type}]\n{formatted_content}"
        else:
            # 来自数据库的历史消息
            formatted_content = convert_content_to_string(content)
            # 历史消息格式: [群友: (历史记录)]\n[内容: 类型]\n实际内容
            header = "[群友: (历史记录)]"

            # 判断内容类型
            content_type = "文本"
            if isinstance(formatted_content, str) and "[图片]" in formatted_content:
                content_type = "图片"

            return f"{header}\n[内容: {content_type}]\n{formatted_content}"
    else:
        # 对于其他角色
        formatted_content = convert_content_to_string(content)
        return f"[{role}]\n[内容: 文本]\n{formatted_content}"
