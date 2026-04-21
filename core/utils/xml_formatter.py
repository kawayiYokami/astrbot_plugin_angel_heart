"""
AngelHeart 插件 - 消息格式化工具
负责将各种角色的消息转换为文本格式，并支持可选的 XML 包裹。
"""

from .time_utils import format_relative_time, format_absolute_time
from .content_utils import convert_content_to_string


def build_image_attachment_text(msg: dict) -> str:
    """按统一格式构建图片附件文本块。"""
    image_caption = msg.get("image_caption", "")
    if not isinstance(image_caption, str) or not image_caption.strip():
        return ""

    refs = []
    stored_refs = msg.get("image_refs", [])
    if isinstance(stored_refs, list):
        refs.extend(
            ref for ref in stored_refs if isinstance(ref, str) and ref.strip()
        )

    content = msg.get("content", [])
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "image_url":
                continue
            ref = (
                item.get("local_file_path")
                or item.get("original_file_url")
                or item.get("original_url")
            )
            if not ref:
                image_url = item.get("image_url", {})
                if isinstance(image_url, dict):
                    url = image_url.get("url", "")
                    if isinstance(url, str) and url and not url.startswith("data:"):
                        ref = url
            if isinstance(ref, str) and ref.strip():
                refs.append(ref)

    deduped_refs = []
    seen = set()
    for ref in refs:
        if ref not in seen:
            deduped_refs.append(ref)
            seen.add(ref)

    path_text = "\n".join(deduped_refs) if deduped_refs else "未知"
    caption_text = image_caption.strip()
    return (
        "<图片附件>\n"
        "#图片路径#：\n"
        f"{path_text}\n"
        "#图片描述#：\n"
        f"{caption_text}\n"
        "</图片附件>"
    )


def format_message_to_text(
    msg: dict,
    alias: str = "AngelHeart",
    wrapper_tag: str = None,
    use_relative_time: bool = True,
) -> str:
    """
    将消息转换为文本格式，并可选地使用 XML 标签包裹。

    Args:
        msg (dict): 消息字典。
        alias (str): AI 的昵称。
        wrapper_tag (str): 可选的 XML 包裹标签（如 "已回应消息"）。
        use_relative_time (bool): 是否使用相对时间，False 则使用绝对时间。

    Returns:
        str: 格式化后的字符串。
    """
    role = msg.get("role")
    content = msg.get("content", "")
    text_content = convert_content_to_string(content)
    image_attachment_text = build_image_attachment_text(msg)
    if image_attachment_text:
        text_content = (
            f"{text_content}\n\n{image_attachment_text}"
            if text_content
            else image_attachment_text
        )

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
            if use_relative_time:
                time_tag = format_relative_time(timestamp)
            else:
                time_tag = format_absolute_time(timestamp)

            # 恢复旧格式：
            # [群友: 昵称 (ID: ...)] (相对时间)
            # [内容: 类型]
            # 实际内容


            header = f"[群友: {sender_name} (ID: {sender_id})]{time_tag}"
            formatted_body = f"{header}: {text_content}"
        else:
            # 历史记录回退
            formatted_body = f"[群友(历史记录)]\n[内容: 文本]\n{text_content}"

    # 2. Assistant (助理) 消息处理
    elif role == "assistant":
        # 优先检查 tool_calls 结构化数据
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            # 格式化所有工具调用
            actions = []
            for tc in tool_calls:
                # 兼容 ToolCall 对象和字典两种格式
                if hasattr(tc, "function"):
                    # 对象格式 (Pydantic model)
                    func_name = getattr(tc.function, "name", "unknown")
                    args = getattr(tc.function, "arguments", "{}")
                elif isinstance(tc, dict):
                    # 字典格式
                    func_name = tc.get("function", {}).get("name", "unknown")
                    args = tc.get("function", {}).get("arguments", "{}")
                else:
                    func_name = "unknown"
                    args = "{}"

                actions.append(f"调用工具 {func_name}({args})")

            action_desc = "; ".join(actions)
            # 如果还有文本内容，拼接到后面
            final_content = f"[动作: {action_desc}]"
            if text_content:
                final_content += f"\n{text_content}"

            formatted_body = f"{final_content}"

        # 兼容旧的文本格式调用
        elif msg.get("sender_name") == "assistant" and text_content.startswith("调用 "):
            formatted_body = f"[动作: 调用工具]\n{text_content}"
        else:
            # 直接返回内容，不带[助理:...]前缀，避免复读历史
            formatted_body = f"{text_content}"

    # 3. System (系统) 消息处理
    elif role == "system":
        formatted_body = f"[系统通知]\n{text_content}"

    # 4. Tool (工具结果) 消息处理 - 此分支已废弃
    # 由于已切换到原生工具调用格式，此处的文本化逻辑不再需要。
    # 在 front_desk.py 中，role == "tool" 的消息会直接保留原始结构。

    # 5. 其他默认处理
    else:
        formatted_body = f"[{role}]\n{text_content}"

    # 应用 XML 包裹
    if wrapper_tag:
        return f"<{wrapper_tag}>\n{formatted_body}\n</{wrapper_tag}>"
    else:
        return formatted_body
