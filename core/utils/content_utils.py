"""
AngelHeart 插件 - 内容处理相关工具函数
"""

import re
from markdown_it import MarkdownIt
from mdit_plain.renderer import RendererPlain

# 创建一个全局的 MarkdownIt 实例用于 strip_markdown 函数，以提高性能
_md_strip_instance = MarkdownIt(renderer_cls=RendererPlain)


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


def strip_reasoning_chain(text: str) -> str:
    """
    清洗文本中的思维链内容，移除推理模型的推理过程标记。

    支持的格式：
    - 思维链内容</think> XML 标签格式

    Args:
        text (str): 可能包含思维链的原始文本。

    Returns:
        str: 清洗后的文本，已移除思维链内容。
    """
    # 匹配 ...</think> XML 标签及其内容
    # 使用 re.DOTALL 使 . 匹配换行符，支持多行思维链
    reasoning_pattern = re.compile(
        r'[\s\S]*?</think>',
        re.IGNORECASE | re.DOTALL
    )

    # 移除所有匹配的思维链内容
    cleaned_text = reasoning_pattern.sub('', text)

    # 清理可能留下的多余空行
    cleaned_text = re.sub(r'\n\s*\n', '\n\n', cleaned_text).strip()

    return cleaned_text


def strip_markdown(text: str) -> str:
    """
    使用 markdown-it-py 和 mdit_plain 库将 Markdown 文本转换为纯文本。
    同时会先清洗文本中的思维链内容。

    Args:
        text (str): 包含 Markdown 格式和可能包含思维链的原始文本。

    Returns:
        str: 清洗后的纯文本（已移除思维链和 Markdown 格式）。
    """
    # 先清洗思维链内容
    text = strip_reasoning_chain(text)

    # 使用全局的 MarkdownIt 实例以提高性能
    global _md_strip_instance
    # 渲染并返回纯文本
    return _md_strip_instance.render(text)