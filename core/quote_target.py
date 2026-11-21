"""引用目标协议：消息编号与 ``[#n]`` 前缀解析。

编号在消息入库时由账本分配、压缩后重置，因此同一个上下文窗口内保持稳定：
模型上一轮看到「#3 是小白说的」，下一轮 #3 还是小白。

渲染时编号写进消息头（``[#3 群友: 小白 (ID: ...)]21:05: 内容``），模型要
引用某条时在正文最前面写 ``[#3]``。发送阶段只解析这个位置，正文中段出现的
``[#n]`` 一律当普通文字。
"""

from __future__ import annotations

import re
from typing import Any, Optional, Tuple

# 只认正文开头的前缀标记。
PREFIX_MARKER_RE = re.compile(r"^\s*[\[【]\s*#\s*(\d+)\s*[\]】]\s*")

# 摘要类消息不参与编号。
_SUMMARY_KINDS = ("context_summary", "summary_context", "context_compaction")

# 旧式工具结果伪装的用户消息不参与编号。
_TOOL_SENDER_NAMES = ("tool_result",)


def _text_of(value: Any) -> str:
    return value if isinstance(value, str) else ""


def is_group_member_message(msg: Any) -> bool:
    """判断消息是否为真实群友消息。

    排除 assistant、原生 tool、旧式 ``sender_name == "tool_result"`` 与摘要。
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
    """能否成为引用目标：群友消息，且能定位到原始消息 ID。"""
    if not is_group_member_message(msg):
        return False
    return bool(str(msg.get("source_message_id", "") or "").strip())


def parse_prefix_marker(text: Any) -> Tuple[Optional[int], str]:
    """拆出正文开头的 ``[#n]``，返回 (编号, 剩余正文)。

    没有标记、编号不是正整数、或标记不在开头时返回 (None, 原文)。
    """
    source = _text_of(text)
    if not source:
        return None, source
    match = PREFIX_MARKER_RE.match(source)
    if not match:
        return None, source
    try:
        number = int(match.group(1))
    except (TypeError, ValueError):
        return None, source
    if number <= 0:
        return None, source
    return number, source[match.end() :]
