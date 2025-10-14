"""
AngelHeart 插件 - 时间相关工具函数
"""

import time
# 条件导入：当缺少astrbot依赖时使用Mock
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

# 定义默认时间戳回退时间（1小时），用于当消息没有时间戳时提供一个基准时间
DEFAULT_TIMESTAMP_FALLBACK_SECONDS = 3600


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