"""
AngelHeart 插件 - 内容处理相关工具函数
"""

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