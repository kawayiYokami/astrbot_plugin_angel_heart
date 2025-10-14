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

    Args:
        ledger: ConversationLedger 的实例。
        chat_id: 会话 ID。

    Returns:
        一个元组 (historical_context, recent_dialogue, boundary_timestamp)。
    """
    # _get_or_create_ledger 是 protected, 但在这里为了重构暂时使用
    # 理想情况下 ledger 应该提供一个公共的获取消息的方法
    ledger_data = ledger._get_or_create_ledger(chat_id)

    with ledger._lock:
        all_messages = ledger_data["messages"]

        # 根据 is_processed 标志进行分割
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