"""
AngelHeart 插件 - 核心工具函数
"""

import time
import json
from typing import List, Dict, TYPE_CHECKING, Union, Tuple

if TYPE_CHECKING:
    from ..models.analysis_result import SecretaryDecision

# 条件导入：当缺少astrbot依赖时使用Mock
try:
    from astrbot.api import logger
except ImportError:
    # 创建Mock logger用于测试
    class MockLogger:
        def debug(self, msg): pass
        def info(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass
    logger = MockLogger()

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
    将消息内容转换为用于分析器的纯文本字符串。
    支持标准多模态 content 列表，只提取文本部分。
    """
    if isinstance(content, str):
        # 如果 content 是字符串，直接返回其 strip 后的结果
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
        # 如果 content 是其他类型，尝试转换为字符串
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
        last_ts = ledger_data["last_processed_timestamp"]
        all_messages = ledger_data["messages"]

        historical_context = [m for m in all_messages if m.get("timestamp", 0) <= last_ts]
        recent_dialogue = [m for m in all_messages if m.get("timestamp", 0) > last_ts]

        boundary_ts = 0.0
        if recent_dialogue:
            boundary_ts = recent_dialogue[-1].get("timestamp", 0.0)

        return historical_context, recent_dialogue, boundary_ts


def format_final_prompt(recent_dialogue: List[Dict], decision: 'SecretaryDecision') -> str:
    """
    为大模型生成最终的、自包含的用户指令字符串 (Prompt)。
    """
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
