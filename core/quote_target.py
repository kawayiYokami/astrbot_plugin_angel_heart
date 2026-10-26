"""引用目标协议的纯函数实现。

把「本轮可见群友消息编号」「模型前缀标记解析与清理」「消息间隔计算」
集中在此，只依赖标准库与消息字典，不导入 AstrBot 对象。

协议：请求阶段给本轮群友消息编号并登记 ``{编号: source_message_id}``，
模型用 ``[引用:n]`` 指定目标；发送阶段解析前缀、清理标记，再由调用方
按间隔阈值决定是否插入 ``Reply``。
"""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# 前缀标记：位于文本开头，允许半角/全角括号与冒号、括号内合理空白。
_PREFIX_MARKER_RE = re.compile(r"^\s*[\[【]\s*引用\s*[:：]\s*([^\]】]*?)\s*[\]】]\s*")

# 兜底清理：正文任意位置的「引用 + 冒号」保留括号格式。
_QUOTE_MARKER_RE = re.compile(r"[\[【]\s*引用\s*[:：][^\]】]*?[\]】]")

# 摘要类消息不参与编号与计数。
_SUMMARY_KINDS = ("context_summary", "summary_context", "context_compaction")

# 旧式工具结果伪装的用户消息不参与编号与计数。
_TOOL_SENDER_NAMES = ("tool_result",)


def _text_of(value: Any) -> str:
    return value if isinstance(value, str) else ""


def is_group_member_message(msg: Any) -> bool:
    """判断消息是否为真实群友消息（可参与计数）。

    排除 assistant、原生 tool、旧式 ``sender_name == "tool_result"`` 与摘要。
    编号与间隔计数共用同一判定，避免两处口径不一致。
    """
    if not isinstance(msg, dict):
        return False
    if msg.get("role") != "user":
        return False
    if msg.get("sender_name") in _TOOL_SENDER_NAMES:
        return False
    if msg.get("kind") in _SUMMARY_KINDS:
        return False
    return True


def is_quotable_message(msg: Any) -> bool:
    """判断消息是否可以成为引用目标（必须能定位到原始消息 ID）。"""
    if not is_group_member_message(msg):
        return False
    return bool(str(msg.get("source_message_id", "") or "").strip())


def register_quote_target(quote_map: Optional[Dict[int, str]], msg: Any) -> Optional[int]:
    """渲染消息时为可引用消息登记编号，返回该编号（不可编号时返回 None）。"""
    if not isinstance(quote_map, dict):
        return None
    if not is_quotable_message(msg):
        return None
    number = len(quote_map) + 1
    quote_map[number] = str(msg.get("source_message_id", "") or "")
    return number


def _parse_positive_int(raw: str) -> Optional[int]:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_prefix_marker(text: Any) -> Tuple[Optional[int], str]:
    """解析文本开头的前缀标记。

    Returns:
        (编号或 None, 去掉前缀后的文本)。没有前缀时编号为 None 且文本原样返回；
        前缀编号非法（零、负数、非数字）时仍去掉前缀，但编号为 None。
    """
    source = _text_of(text)
    if not source:
        return None, source
    match = _PREFIX_MARKER_RE.match(source)
    if not match:
        return None, source
    return _parse_positive_int(match.group(1)), source[match.end():]


def strip_quote_markers(text: Any) -> str:
    """删除正文中所有可识别的引用标记（含非法编号），不触碰普通文字。"""
    source = _text_of(text)
    if not source:
        return source
    return _QUOTE_MARKER_RE.sub("", source)


def strip_markers_from_content(content: Any) -> Any:
    """清理序列化消息内容中的引用标记，保留结构与其它字段。"""
    if isinstance(content, str):
        return strip_quote_markers(content)
    if isinstance(content, list):
        cleaned: List[Any] = []
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            ):
                copied = dict(item)
                copied["text"] = strip_quote_markers(copied["text"])
                cleaned.append(copied)
            else:
                cleaned.append(item)
        return cleaned
    return content


def _timestamp_of(msg: Dict[str, Any]) -> Optional[float]:
    """读取有效时间戳；缺失、零或非法时返回 None（按快照顺序兜底）。"""
    raw = msg.get("timestamp")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def count_gap_messages(
    messages: Any, target_message_id: Any, frozen_at: float
) -> Optional[int]:
    """统计目标之后、冻结时刻之前到达的真实群友消息条数。

    目标不在快照中时返回 None：调用方不得把未知间隔当成较大值或 0。
    """
    target_id = str(target_message_id or "")
    if not target_id or not isinstance(messages, list):
        return None

    target_index = None
    for index, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if str(msg.get("source_message_id", "") or "") == target_id:
            target_index = index
            break
    if target_index is None:
        return None

    gap = 0
    for msg in messages[target_index + 1:]:
        if not is_group_member_message(msg):
            continue
        timestamp = _timestamp_of(msg)
        if timestamp is not None and timestamp > frozen_at:
            continue
        gap += 1
    return gap


def collect_known_message_ids(messages: Any) -> Set[str]:
    """收集快照中可用于定位的消息 ID（``source_message_id`` 非空者）。"""
    known: Set[str] = set()
    if not isinstance(messages, list):
        return known
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        raw = str(msg.get("source_message_id", "") or "").strip()
        if raw:
            known.add(raw)
    return known


def classify_missing_target(known_ids: Any, target_message_id: Any) -> str:
    """引用快照缺失目标时，按请求阶段索引是否持有它分类。

    ``known_ids`` 是请求阶段引用索引里存在的消息 ID 集合：

    - 目标当时在索引中 -> ``quote_index_evicted``；
    - 目标当时不在索引中 -> ``quote_index_missing``，不能据此断言从未入账。

    正常整理不会删除独立的引用索引；索引超出容量或缺少记录时才走此兜底。
    """
    target_id = str(target_message_id or "").strip()
    if not target_id:
        return "quote_index_missing"
    if isinstance(known_ids, (set, frozenset, list, tuple)):
        for item in known_ids:
            if str(item or "").strip() == target_id:
                return "quote_index_evicted"
    return "quote_index_missing"
