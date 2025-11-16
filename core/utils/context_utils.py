"""
AngelHeart 插件 - 上下文处理相关工具函数
"""

import json
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


def json_serialize_context(chat_records: List[Dict], decision: Union["SecretaryDecision", Dict], needs_search: bool = False) -> str:
    """
    将聊天记录、秘书决策和搜索标志序列化为 JSON 字符串，用于注入到 AstrMessageEvent。

    Args:
        chat_records (List[Dict]): 聊天记录列表，每条记录为消息 Dict。
        decision (Union[SecretaryDecision, Dict]): 秘书决策对象或字典。
        needs_search (bool): 是否需要搜索，默认 False。

    Returns:
        str: JSON 字符串，包含 angelheart_context 数据。
    """
    # 输入验证
    if not isinstance(chat_records, list):
        logger.warning("chat_records 必须是列表类型，使用空列表代替")
        chat_records = []

    # 确保所有聊天记录都是字典类型
    validated_records = []
    for record in chat_records:
        if isinstance(record, dict):
            validated_records.append(record)
        else:
            logger.warning(f"跳过非字典类型的聊天记录: {type(record)}")

    try:
        # 从决策对象中获取 needs_search 信息
        if hasattr(decision, 'needs_search'):
            needs_search = decision.needs_search
        elif isinstance(decision, dict) and 'needs_search' in decision:
            needs_search = decision['needs_search']

        # 使用 model_dump() 替代过时的 dict() 方法
        if hasattr(decision, 'model_dump'):
            decision_dict = decision.model_dump()
        elif hasattr(decision, 'dict'):
            decision_dict = decision.dict()
        else:
            decision_dict = decision

        context_data = {
            "chat_records": validated_records,
            "secretary_decision": decision_dict,
            "needs_search": needs_search
        }
        return json.dumps(context_data, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as e:
        logger.error(f"序列化上下文失败: {e}")
        # 返回一个最小化的安全上下文
        fallback_context = {
            "chat_records": [],
            "secretary_decision": {"should_reply": False, "error": "序列化失败"},
            "needs_search": needs_search,
            "error": "序列化失败"
        }
        return json.dumps(fallback_context, ensure_ascii=False)


def partition_dialogue(
    ledger: 'ConversationLedger',
    chat_id: str
) -> Tuple[List[Dict], List[Dict], float]:
    """
    根据指定会话的最后处理时间戳，将对话记录分割为历史和新对话。
    这是从 ConversationLedger.get_context_snapshot 提取的核心逻辑。

    同时对工具调用进行压缩处理，便于秘书分析。

    Args:
        ledger: ConversationLedger 的实例。
        chat_id: 会话 ID。

    Returns:
        一个元组 (historical_context, recent_dialogue, boundary_timestamp)。
    """
    # _get_or_create_ledger 是 protected, 但在这里为了重构暂时使用
    # 使用公共方法获取消息
    all_messages = ledger.get_all_messages(chat_id)
    
    # 对所有消息进行工具调用压缩处理（在锁外）
    processed_messages = []
    for msg in all_messages:
        processed_msg = _compress_tool_message(msg)
        processed_messages.append(processed_msg)
    
    # 根据 is_processed 标志进行分割
    historical_context = [m for m in processed_messages if m.get("is_processed", False)]
    recent_dialogue = [m for m in processed_messages if not m.get("is_processed", False)]
    
    # 边界时间戳是新对话中最后一条消息的时间戳
    boundary_ts = 0.0
    if recent_dialogue:
        # 为确保准确，最好在取最后一个元素前按时间戳排序
        recent_dialogue.sort(key=lambda m: m.get("timestamp", 0))
        boundary_ts = recent_dialogue[-1].get("timestamp", 0.0)
    
    return historical_context, recent_dialogue, boundary_ts


def _compress_tool_message(msg: Dict) -> Dict:
    """
    压缩工具调用相关的消息，便于秘书分析。

    Args:
        msg: 原始消息

    Returns:
        压缩后的消息
    """
    # 创建消息副本，避免修改原始数据
    compressed_msg = msg.copy()

    # 处理工具结果消息 (role: "user" 且 sender_name: "tool_result")
    if msg.get("role") == "user" and msg.get("sender_name") == "tool_result":
        content = msg.get("content", "")

        # 处理 content 可能是列表的情况（多模态内容）
        if isinstance(content, list):
            # 提取所有文本内容
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            content = "".join(text_parts)

        # 移除"工具调用结果："前缀后压缩
        if isinstance(content, str):
            if content.startswith("工具调用结果："):
                result_content = content[len("工具调用结果："):]
                if len(result_content) > 20:
                    compressed_msg["content"] = "工具调用结果：" + result_content[:20] + "..."
                # 如果结果内容<=20字符，保持原样（不需要压缩）
            elif len(content) > 20:
                # 不以"工具调用结果："开头的普通内容，直接截断
                compressed_msg["content"] = content[:20] + "..."

    # 处理工具调用消息 (role: "assistant" 且 sender_name: "assistant" 且内容包含"调用")
    # 新格式下，工具调用已经是纯文本，格式如："调用 function_name({args})"
    elif msg.get("role") == "assistant" and msg.get("sender_name") == "assistant":
        content = msg.get("content", "")

        # 处理 content 可能是列表的情况（多模态内容）
        if isinstance(content, list):
            # 提取所有文本内容
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
            content = "".join(text_parts)

        # 如果内容以"调用"开头，说明这是工具调用消息
        if content and isinstance(content, str) and content.startswith("调用 "):
            # 提取函数名（不包含参数）来压缩
            try:
                # 格式: "调用 function_name({args})" 或 "调用 func1({args}); 调用 func2({args})"
                tool_names = []
                for call in content.split("; "):
                    if call.startswith("调用 "):
                        func_part = call[3:]  # 移除"调用 "
                        func_name = func_part.split("(")[0] if "(" in func_part else func_part
                        tool_names.append(func_name)

                if tool_names:
                    compressed_msg["content"] = f"[使用工具: {', '.join(tool_names)}]"
            except Exception as e:
                logger.warning(f"压缩工具调用消息失败: {e}, 保持原内容")

    return compressed_msg


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
    chat_id: str
) -> Tuple[List[Dict], List[Dict], float]:
    """
    根据指定会话的最后处理时间戳，将对话记录分割为历史和新对话。
    与 partition_dialogue 的区别是：此函数保留原始的工具调用结构，不进行压缩。
    专门用于给老板（前台LLM）构建完整的上下文。

    Args:
        ledger: ConversationLedger 的实例。
        chat_id: 会话 ID。

    Returns:
        一个元组 (historical_context, recent_dialogue, boundary_timestamp)。
    """
    # 使用公共方法获取消息
    all_messages = ledger.get_all_messages(chat_id)
    
    # 不进行任何压缩处理，保留原始消息结构
    # 直接根据 is_processed 标志进行分割
    historical_context = [m for m in all_messages if m.get("is_processed", False)]
    recent_dialogue = [m for m in all_messages if not m.get("is_processed", False)]
    
    # 边界时间戳是新对话中最后一条消息的时间戳
    boundary_ts = 0.0
    if recent_dialogue:
        # 为确保准确，最好在取最后一个元素前按时间戳排序
        recent_dialogue.sort(key=lambda m: m.get("timestamp", 0))
        boundary_ts = recent_dialogue[-1].get("timestamp", 0.0)
    
    return historical_context, recent_dialogue, boundary_ts


def format_final_prompt(recent_dialogue: List[Dict], decision: 'SecretaryDecision') -> str:
    """
    为大模型生成最终的、自包含的用户指令字符串 (Prompt)。
    """
    from .content_utils import convert_content_to_string

    # 1. 将需要回应的新对话格式化为字符串
    dialogue_str = "\n".join([
        f"{msg.get('sender_name', '未知用户')}：{convert_content_to_string(msg.get('content', ''))}"
        for msg in recent_dialogue
    ])

    # 2. 从决策中获取核心信息
    topic = decision.topic
    target = decision.reply_target
    strategy = decision.reply_strategy

    # 3. 组装最终的 Prompt 字符串
    prompt = f"""需要你分析的最新对话（这是你唯一需要回应的对话，过去的对话已经过去了，仅供参考）
---
{dialogue_str}
---
任务指令：请根据以上对话历史，围绕核心话题 '{topic}'，向 '{target}' 执行以下策略：'{strategy}'。"""

    return prompt