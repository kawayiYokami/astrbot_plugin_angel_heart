"""
AngelHeart 插件 - 消息处理相关工具函数
"""

from typing import Any

from .xml_formatter import format_message_to_text

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


def format_message_for_llm(msg: dict, alias: str) -> str:
    """
    按照轻量模型能看到的格式格式化消息。
    生成文本格式，供上层调用者决定是否添加 XML 包裹。

    Args:
        msg (dict): 消息字典，包含 role, content, sender_name, sender_id, timestamp 等字段。
        alias (str): AI 的昵称，用于格式化助理消息。

    Returns:
        str: 格式化后的消息字符串。
    """
    return format_message_to_text(msg, alias)


def serialize_message_chain(message_chain) -> list:
    """
    将 AstrBot 的 MessageChain 序列化为符合多模态标准的字典列表。

    Args:
        message_chain: AstrBot 的 MessageChain 对象

    Returns:
        list: 序列化后的内容列表，例如 [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "..."}}]
    """
    if not message_chain:
        return []

    serialized_content = []

    # 条件导入 AstrBot 的消息组件
    try:
        from astrbot.core.message.components import Plain, Image
    except ImportError:
        logger.warning("无法导入 AstrBot 消息组件，serialize_message_chain 将无法处理图片")
        Plain = None
        Image = None

    for component in message_chain:
        # 处理纯文本组件
        if Plain and isinstance(component, Plain):
            if component.text:
                serialized_content.append({
                    "type": "text",
                    "text": component.text
                })
        # 处理图片组件
        elif Image and isinstance(component, Image):
            # 优先使用 file 字段，如果为空则尝试 path 字段
            image_url = component.file or getattr(component, 'path', '')
            if image_url:
                serialized_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": image_url
                    }
                })
        # 处理其他未知组件，尝试转换为文本
        else:
            try:
                # 尝试获取组件的文本表示
                component_text = str(component)
                if component_text:
                    serialized_content.append({
                        "type": "text",
                        "text": component_text
                    })
            except Exception as e:
                logger.warning(f"无法序列化未知组件类型: {type(component).__name__}, 错误: {e}")

    return serialized_content


def serialize_content_parts(content: Any) -> Any:
    """将 AstrBot 的 content parts 统一序列化为可入账的基础结构。"""
    if isinstance(content, list):
        serialized = []
        for item in content:
            if isinstance(item, dict):
                serialized.append(item.copy())
            elif hasattr(item, "model_dump_for_context"):
                serialized.append(item.model_dump_for_context())
            elif hasattr(item, "model_dump"):
                serialized.append(item.model_dump())
            else:
                serialized.append({"type": "text", "text": str(item)})
        return serialized
    return content


def _is_checkpoint_context_item(item: Any) -> bool:
    """与上游 bind_checkpoint_messages 对齐：checkpoint 不进入 run_context.messages。"""
    if isinstance(item, dict):
        return item.get("role") == "_checkpoint"
    return getattr(item, "role", None) == "_checkpoint"


def estimate_provider_request_baseline_count(provider_request: Any) -> int:
    """估算 run_context.messages 中属于请求基线的前缀长度。"""
    if provider_request is None:
        return 0

    contexts = getattr(provider_request, "contexts", None) or []
    # 上游会把 _checkpoint 绑定到前一条消息，不单独 append 到 messages。
    baseline_count = sum(
        1 for item in contexts if not _is_checkpoint_context_item(item)
    )

    has_current_user_message = bool(
        getattr(provider_request, "prompt", None) is not None
        or getattr(provider_request, "image_urls", None)
        or getattr(provider_request, "audio_urls", None)
        or getattr(provider_request, "extra_user_content_parts", None)
    )
    if has_current_user_message:
        baseline_count += 1

    if getattr(provider_request, "system_prompt", ""):
        baseline_count += 1

    return baseline_count


def extract_completed_agent_messages(
    messages: list[Any] | None,
    provider_request: Any,
) -> list[Any]:
    """从 run_context.messages 中切出本次事件新增的 assistant/tool 闭合链。"""
    all_messages = list(messages or [])
    if not all_messages:
        return []

    baseline_count = estimate_provider_request_baseline_count(provider_request)
    baseline_count = max(0, min(baseline_count, len(all_messages)))

    completed_messages = []
    for message in all_messages[baseline_count:]:
        role = getattr(message, "role", None)
        if role in ("assistant", "tool"):
            completed_messages.append(message)
    return completed_messages


def serialize_agent_run_message(
    message: Any,
    *,
    timestamp: float,
    assistant_sender_id: str,
) -> dict | None:
    """将上游 run_context.messages 中的单条 assistant/tool 消息转为 Ledger 结构。

    timestamp 由完成点统一给定：整条闭合链共用完结瞬间为基准，
    链内仅做微小递增以保序。不要改成工具中途真实时间，否则并发用户
    消息可能插进工具链中间。
    """
    role = getattr(message, "role", None)
    if role not in ("assistant", "tool"):
        return None

    payload = {
        "role": role,
        "content": serialize_content_parts(getattr(message, "content", None)),
        "timestamp": timestamp,
    }

    if role == "assistant":
        payload["sender_id"] = assistant_sender_id or "assistant"
        payload["sender_name"] = "assistant"
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls is not None:
            payload["tool_calls"] = serialize_content_parts(tool_calls)
            payload["is_structured_toolcall"] = True
    else:
        payload["sender_id"] = "tool"
        payload["sender_name"] = "tool_result"
        payload["tool_call_id"] = getattr(message, "tool_call_id", None)
        payload["is_structured_toolcall"] = True

    if payload.get("content") is None and not payload.get("tool_calls"):
        return None

    return payload