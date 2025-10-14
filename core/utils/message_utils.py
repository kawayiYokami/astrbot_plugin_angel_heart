"""
AngelHeart 插件 - 消息处理相关工具函数
"""


from .content_utils import convert_content_to_string
from .time_utils import format_relative_time

# 条件导入：当缺少astrbot依赖时使用Mock
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


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