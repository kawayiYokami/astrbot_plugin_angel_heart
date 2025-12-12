"""
AngelHeart 插件 - 核心工具模块
提供各种通用工具和辅助功能
"""

# 从各个子模块导入函数
from .time_utils import get_latest_message_time, format_relative_time, get_beijing_time_str
from .content_utils import convert_content_to_string, strip_markdown
from .message_utils import prune_old_messages, format_message_for_llm
from .context_utils import json_serialize_context, partition_dialogue, partition_dialogue_raw, format_final_prompt
from .xml_formatter import format_message_to_text
from .json_parser import JsonParser

# 导出所有函数和类
__all__ = [
    # 时间相关
    'get_latest_message_time',
    'format_relative_time',
    'get_beijing_time_str',

    # 内容处理相关
    'convert_content_to_string',
    'strip_markdown',

    # 消息处理相关
    'prune_old_messages',
    'format_message_for_llm',

    # 上下文处理相关
    'json_serialize_context',
    'partition_dialogue',
    # XML 格式化相关
    'format_message_to_text',
    'partition_dialogue_raw',
    'format_final_prompt',

    # JSON解析相关
    'JsonParser'
]