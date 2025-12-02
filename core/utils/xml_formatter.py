"""
AngelHeart 插件 - 消息格式化工具
负责将各种角色的消息转换为文本格式，并支持可选的 XML 包裹。
"""

from .time_utils import format_relative_time
from .content_utils import convert_content_to_string

def format_message_to_text(msg: dict, alias: str = "AngelHeart", wrapper_tag: str = None) -> str:
    """
    将消息转换为文本格式，并可选地使用 XML 标签包裹。

    Args:
        msg (dict): 消息字典。
        alias (str): AI 的昵称。
        wrapper_tag (str): 可选的 XML 包裹标签（如 "已回应消息"）。

    Returns:
        str: 格式化后的字符串。
    """
    role = msg.get("role")
    content = msg.get("content", "")
    text_content = convert_content_to_string(content)

    formatted_body = ""

    # 1. User (群友) 消息处理
    if role == "user":
        if msg.get("sender_name") == "tool_result":
            # 工具结果处理
            clean_content = text_content
            if text_content.startswith("工具调用结果："):
                clean_content = text_content.replace("工具调用结果：", "", 1).strip()
            formatted_body = f"[系统工具]: {clean_content}"

        elif "sender_name" in msg:
            # 标准用户消息
            sender_id = msg.get("sender_id", "Unknown")
            sender_name = msg.get("sender_name", "成员")
            timestamp = msg.get("timestamp")
            relative_time = format_relative_time(timestamp)

            # 恢复旧格式：
            # [群友: 昵称 (ID: ...)] (相对时间)
            # [内容: 类型]
            # 实际内容

            # 判断内容类型 (简单判断)
            content_type = "文本"
            if "[图片]" in text_content:
                content_type = "图片"

            header = f"[群友: {sender_name} (ID: {sender_id})]{relative_time}"
            formatted_body = f"{header}\n[内容: {content_type}]\n{text_content}"
        else:
            # 历史记录回退
            formatted_body = f"[群友(历史记录)]\n[内容: 文本]\n{text_content}"

    # 2. Assistant (助理) 消息处理
    elif role == "assistant":
        if msg.get("sender_name") == "assistant" and text_content.startswith("调用 "):
             formatted_body = f"[助理: {alias}]\n[动作: 调用工具]\n{text_content}"
        else:
             formatted_body = f"[助理: {alias}]\n[内容: 文本]\n{text_content}"

    # 3. System (系统) 消息处理
    elif role == "system":
        formatted_body = f"[系统通知]\n{text_content}"

    # 4. 其他默认处理
    else:
        formatted_body = f"[{role}]\n{text_content}"

    # 应用 XML 包裹
    if wrapper_tag:
        return f"<{wrapper_tag}>\n{formatted_body}\n</{wrapper_tag}>"
    else:
        return formatted_body

# 保持兼容性别名，但在新逻辑中应尽量使用 format_message_to_text
format_message_to_xml = format_message_to_text