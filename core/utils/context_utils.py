"""
AngelHeart 插件 - 上下文处理相关工具函数
"""

import copy
import json
import re
from typing import List, Dict, TYPE_CHECKING, Union, Tuple

if TYPE_CHECKING:
    from ..models.analysis_result import SecretaryDecision
    from ..conversation_ledger import ConversationLedger

# 条件导入：当缺少astrbot依赖时使用Mock
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


_GENERIC_IMAGE_PLACEHOLDER_RE = re.compile(r"(?:\s*\[图片\]\s*)+")


def json_serialize_context(
    chat_records: List[Dict],
    decision: Union["SecretaryDecision", Dict],
) -> str:
    """
    将聊天记录与秘书决策序列化为 JSON 字符串，注入到 AstrMessageEvent。

    Args:
        chat_records: 聊天记录列表
        decision: 秘书决策对象或字典

    Returns:
        angelheart_context JSON 字符串（chat_records + secretary_decision）
    """
    if not isinstance(chat_records, list):
        logger.warning("chat_records 必须是列表类型，使用空列表代替")
        chat_records = []

    validated_records = []
    for record in chat_records:
        if isinstance(record, dict):
            validated_records.append(record)
        else:
            logger.warning(f"跳过非字典类型的聊天记录: {type(record)}")

    try:
        if hasattr(decision, "model_dump"):
            decision_dict = decision.model_dump()
        elif hasattr(decision, "dict"):
            decision_dict = decision.dict()
        else:
            decision_dict = decision

        # 过时字段：不再注入 needs_search
        if isinstance(decision_dict, dict):
            decision_dict = dict(decision_dict)
            decision_dict.pop("needs_search", None)

        context_data = {
            "chat_records": validated_records,
            "secretary_decision": decision_dict,
        }
        return json.dumps(context_data, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as e:
        logger.error(f"序列化上下文失败: {e}")
        fallback_context = {
            "chat_records": [],
            "secretary_decision": {"should_reply": False, "error": "序列化失败"},
            "error": "序列化失败",
        }
        return json.dumps(fallback_context, ensure_ascii=False)


def _slice_messages_through_id(
    messages: List[Dict], boundary_message_id: str
) -> List[Dict]:
    """按消息 ID 包含式截断；找不到明确边界时拒绝扩窗。"""
    boundary_message_id = str(boundary_message_id or "")
    if not boundary_message_id:
        return messages
    for index, message in enumerate(messages):
        if str(message.get("source_message_id", "") or "") == boundary_message_id:
            return messages[: index + 1]
    logger.warning(f"上下文边界消息不存在: {boundary_message_id}")
    return []


def partition_dialogue(
    ledger: 'ConversationLedger',
    chat_id: str,
    boundary_message_id: str = "",
) -> Tuple[List[Dict], List[Dict], float]:
    """
    正式上下文切分（秘书轻量分析用）：
    - 当前摘要作为历史前缀
    - 当前连续消息块整体作为 recent（不再用 is_processed）
    - boundary 为块尾时间戳
    """
    all_messages = _slice_messages_through_id(
        ledger.get_all_messages(chat_id), boundary_message_id
    )
    summary = ""
    try:
        summary = ledger.get_current_summary(chat_id)
    except Exception:
        summary = ""

    # 秘书路径：压缩/丢弃工具消息
    recent_dialogue = []
    for msg in all_messages:
        processed_msg = _compress_tool_message(msg)
        if processed_msg:
            recent_dialogue.append(processed_msg)

    recent_dialogue.sort(key=lambda m: m.get("timestamp", 0))
    boundary_ts = recent_dialogue[-1].get("timestamp", 0.0) if recent_dialogue else 0.0

    historical_context = []
    if summary:
        has_summary_msg = any(
            m.get("kind") in ("context_summary", "summary_context", "context_compaction")
            for m in recent_dialogue[:1]
        )
        if not has_summary_msg:
            ts = recent_dialogue[0].get("timestamp", 0) if recent_dialogue else 0
            historical_context = [
                {
                    "role": "system",
                    "content": f"[当前摘要]\n{summary}",
                    "sender_id": "system",
                    "sender_name": "context_summary",
                    "kind": "context_summary",
                    "timestamp": max(0.0, float(ts) - 0.001) if ts else 0.0,
                }
            ]
        else:
            # 块内已有摘要消息：把它视作历史前缀，其余当 recent
            historical_context = [recent_dialogue[0]]
            recent_dialogue = recent_dialogue[1:]

    return historical_context, recent_dialogue, boundary_ts


def _compress_tool_message(msg: Dict) -> Union[Dict, None]:
    """
    压缩或丢弃工具相关的消息，以便于秘书分析。
    - 丢弃工具调用消息。
    - 丢弃工具结果消息，以节省Token。

    Args:
        msg: 原始消息

    Returns:
        消息字典，或 None (如果消息被丢弃)。
    """
    role = msg.get("role")

    # 1. 丢弃工具结果消息 (role: "tool")
    if role == "tool":
        return None

    # 2. 丢弃旧的、被伪装的工具结果消息
    if role == "user" and msg.get("sender_name") == "tool_result":
        return None

    # 3. 丢弃工具调用消息 (assistant role with tool_calls)
    if role == "assistant" and msg.get("tool_calls"):
        return None

    # 对于其他所有消息，保持原样
    return msg


def _generate_tool_description(tool_name: str, tool_args: Dict) -> str:
    """
    生成工具调用的压缩描述。
    直接使用工具名，不进行任何智能处理。

    Args:
        tool_name: 工具名称
        tool_args: 工具参数（不使用）

    Returns:
        工具描述字符串
    """
    # 直接返回工具名
    return tool_name


def partition_dialogue_raw(
    ledger: 'ConversationLedger',
    chat_id: str,
    boundary_message_id: str = "",
) -> Tuple[List[Dict], List[Dict], float]:
    """
    正式上下文切分（主脑完整上下文）：
    - 当前摘要作为历史前缀
    - 当前连续消息块作为 recent
    - 保留工具结构
    - 不再使用 is_processed
    """
    all_messages = _slice_messages_through_id(
        ledger.get_all_messages(chat_id), boundary_message_id
    )
    summary = ""
    try:
        summary = ledger.get_current_summary(chat_id)
    except Exception:
        summary = ""

    recent_dialogue = sorted(all_messages, key=lambda m: m.get("timestamp", 0))
    boundary_ts = recent_dialogue[-1].get("timestamp", 0.0) if recent_dialogue else 0.0

    historical_context = []
    if summary:
        has_summary_msg = any(
            m.get("kind") in ("context_summary", "summary_context", "context_compaction")
            for m in recent_dialogue[:1]
        )
        if not has_summary_msg:
            ts = recent_dialogue[0].get("timestamp", 0) if recent_dialogue else 0
            historical_context = [
                {
                    "role": "system",
                    "content": f"[当前摘要]\n{summary}",
                    "sender_id": "system",
                    "sender_name": "context_summary",
                    "kind": "context_summary",
                    "timestamp": max(0.0, float(ts) - 0.001) if ts else 0.0,
                }
            ]
        else:
            historical_context = [recent_dialogue[0]]
            recent_dialogue = recent_dialogue[1:]

    return historical_context, recent_dialogue, boundary_ts


def format_decision_xml(decision: 'SecretaryDecision') -> str:
    """
    生成系统决策 XML 字符串。

    Args:
        decision: 秘书决策对象

    Returns:
        str: 系统决策 XML 字符串
    """
    topic = decision.topic
    target = decision.reply_target
    strategy = decision.reply_strategy

    decision_xml = f"""<系统决策>
<系统提醒>该决策是系统简单分析之后的建议方向，你可以参考，但是仍以用户对话为优先</系统提醒>
<参考核心话题>{topic}</参考核心话题>
<建议交互对象>{target}</建议交互对象>
<推荐执行策略>{strategy}</推荐执行策略>
</系统决策>"""

    return decision_xml


def format_final_prompt(
    recent_dialogue: List[Dict],
    decision: 'SecretaryDecision',
    alias: str = "AngelHeart",
    use_absolute_time: bool = True,
) -> str:
    """
    为大模型生成最终的用户对话文本（不包含系统决策和 XML 包裹）。
    """
    from .xml_formatter import format_message_to_text

    marked_dialogue = _with_current_round_image_markers(recent_dialogue)

    # 将需要回应的新对话格式化为文本字符串
    dialogue_str = "\n".join(
        [
            format_message_to_text(
                msg, alias, use_relative_time=not use_absolute_time
            )
            for msg in marked_dialogue
        ]
    )

    return dialogue_str


def _with_current_round_image_markers(messages: List[Dict]) -> List[Dict]:
    """为当前轮 prompt 注入跨消息递增的图片锚点。"""
    marked_messages = []
    image_index = 1

    for msg in messages:
        image_count = _count_prompt_images(msg)
        if image_count <= 0:
            marked_messages.append(msg)
            continue

        markers = [f"[图片{i}]" for i in range(image_index, image_index + image_count)]
        image_index += image_count
        marked_messages.append(_append_image_markers(msg, markers))

    return marked_messages


def _count_prompt_images(msg: Dict) -> int:
    """统计当前 prompt 中需要编号的图片数量。"""
    content = msg.get("content")
    if isinstance(content, list):
        count = sum(
            1
            for item in content
            if isinstance(item, dict) and item.get("type") == "image_url"
        )
        if count:
            return count

    image_refs = msg.get("image_refs")
    if isinstance(image_refs, list):
        return sum(1 for ref in image_refs if isinstance(ref, str) and ref.strip())

    return 0


def _append_image_markers(msg: Dict, markers: List[str]) -> Dict:
    """返回一条带图片编号文本锚点的消息副本。"""
    marked_msg = copy.deepcopy(msg)
    marker_text = " ".join(markers)
    content = marked_msg.get("content")

    if isinstance(content, list):
        text_items = [
            item
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        for item in text_items:
            item["text"] = _strip_generic_image_placeholders(item.get("text", ""))

        non_empty_text_items = [
            item for item in text_items if str(item.get("text", "")).strip()
        ]
        if non_empty_text_items:
            last_text_item = non_empty_text_items[-1]
            text = str(last_text_item.get("text", "")).rstrip()
            last_text_item["text"] = f"{text} {marker_text}".strip()
        else:
            insert_at = 0
            for idx, item in enumerate(content):
                if isinstance(item, dict) and item.get("type") == "image_url":
                    insert_at = idx
                    break
            else:
                insert_at = len(content)
            content.insert(insert_at, {"type": "text", "text": marker_text})
        return marked_msg

    text = _strip_generic_image_placeholders(str(content or ""))
    marked_msg["content"] = f"{text} {marker_text}".strip() if text else marker_text
    return marked_msg


def _strip_generic_image_placeholders(text: str) -> str:
    """移除平台 outline 中不带编号的 [图片] 占位。"""
    if not text:
        return ""
    return _GENERIC_IMAGE_PLACEHOLDER_RE.sub(" ", str(text)).strip()
