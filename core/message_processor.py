"""
MessageProcessor - 前台消息处理器
负责将 ConversationLedger 中的内部消息格式转换为上游 LLM 请求的 context 消息格式。

主要转换逻辑：
1. 识别工具调用/结果消息：保持原始结构，确保兼容性
2. 图片转述处理：使用 ConversationLedger 中已生成的 image_caption，移除原始图片组件
3. 文本格式化：为消息添加结构化标签，增强 LLM 对上下文的理解
4. 内容标准化：统一 content 字段格式，兼容纯文本和多模态模型

输入：conversation_ledger.py 中的原始消息字典
输出：上游 Provider 可用的标准化 context 消息字典
"""

import copy
from typing import Any, List, Dict, Optional

from .utils import format_message_to_text


class MessageProcessor:
    """
    前台消息处理器 - 负责将 ConversationLedger 的内部消息转换为上游 context 消息
    
    转换职责：
    - 工具调用识别与保留：保持 assistant 角色的 tool_calls 结构
    - 工具结果透传：保持 tool 角色的结果消息原样传递
    - 图片转述处理：使用 ConversationLedger 中已生成的 image_caption，移除原始图片组件，适配纯文本模型
    - 文本格式化：为消息添加结构化包装标签，区分历史/最新消息
    - 多模态内容构建：整合文本描述与原始图片组件，支持图片模型
    
    输入消息来自 conversation_ledger.py 的 get_context_snapshot() 方法
    输出用于 rewrite_prompt_for_llm() 方法构建完整的 LLM 请求体
    """
    
    def __init__(self, alias: str):
        self.alias = alias
    
    def process_message(self, msg: Dict[str, Any], wrapper_tag: Optional[str] = None) -> Dict[str, Any]:
        """
        处理单条消息，将 ConversationLedger 内部格式转换为上游 context 消息
        
        Args:
            msg: ConversationLedger 中的原始消息字典，包含 role, content, timestamp, image_caption 等字段
            wrapper_tag: XML 包装标签名称（如 "消息"），None 表示不加包装
                        - 历史消息：wrapper_tag=None，完全纯文本
                        - 最新消息：wrapper_tag="消息"，添加 <消息> 标签作为 Prompt 强调
                        
        Returns:
            处理后的消息字典，标准化为上游 Provider 可接受的格式：
            - 工具调用消息：保持 tool_calls 结构，转换为字典格式
            - 工具结果消息：保持原样传递
            - 普通消息：使用已有的图片转述，文本格式化，构建多模态/纯文本 content
        """
        # 检查是否为原生工具调用或结果，如果是，则直接保留
        if self._is_tool_call(msg):
            return self._handle_tool_call(msg)
        if self._is_tool_result(msg):
            return self._handle_tool_result(msg)
        
        # 处理普通消息
        return self._handle_regular_message(msg, wrapper_tag)
    
    def _is_tool_call(self, msg: Dict[str, Any]) -> bool:
        """判断是否为工具调用消息"""
        return msg.get("role") == "assistant" and msg.get("tool_calls")
    
    def _is_tool_result(self, msg: Dict[str, Any]) -> bool:
        """判断是否为工具结果消息"""
        return msg.get("role") == "tool"
    
    def _handle_tool_call(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """处理工具调用消息"""
        dict_msg = msg.copy()
        # 使用 .model_dump() 将 Pydantic 对象转换为字典
        tool_calls = msg.get("tool_calls", [])
        if tool_calls and hasattr(tool_calls[0], 'model_dump'):
            dict_msg["tool_calls"] = [tc.model_dump() for tc in tool_calls]
        return dict_msg
    
    def _handle_tool_result(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """处理工具结果消息（通常已经是字典，直接返回）"""
        return msg.copy()
    
    def _handle_regular_message(self, msg: Dict[str, Any], wrapper_tag: Optional[str]) -> Dict[str, Any]:
        """处理普通消息（使用已有的图片转述、文本格式化等）"""
        # 预处理消息内容：使用已有的图片转述和多模态内容
        processed_msg = copy.deepcopy(msg)
        original_content = processed_msg.get("content", [])
        
        # 标准化 content 为列表格式
        content_list = self._normalize_content(original_content)
        
        # 使用 ConversationLedger 中已生成的图片转述
        image_caption = processed_msg.get("image_caption")
        if image_caption:
            content_list = self._apply_image_caption(content_list, image_caption)
        
        # 更新消息内容为处理后的列表
        processed_msg["content"] = content_list
        
        # 调用文本格式化工具生成结构化文本
        xml_content = format_message_to_text(processed_msg, self.alias, wrapper_tag=wrapper_tag)
        
        # 提取原始的图片组件
        image_components = self._extract_image_components(original_content)
        
        # 构建最终内容
        role = msg.get("role", "user")
        # 强制规则：
        # 1. 助理消息：无条件字符串
        # 2. 用户消息：无图片 -> 字符串，有图片 -> 多模态列表
        if role == "assistant" or not image_components:
            final_content = xml_content  # 纯文本字符串
        else:
            # 只有用户消息且包含图片时，返回多模态列表
            final_content = [{"type": "text", "text": xml_content}]
            final_content.extend(image_components)
        
        return {
            "role": role,
            "content": final_content
        }
    
    def _normalize_content(self, content: Any) -> List[Dict[str, Any]]:
        """标准化 content 为列表格式"""
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        elif isinstance(content, list):
            return content.copy()
        else:
            return [{"type": "text", "text": str(content)}]
    
    def _apply_image_caption(self, content_list: List[Dict[str, Any]], image_caption: str) -> List[Dict[str, Any]]:
        """使用 ConversationLedger 中已生成的 image_caption，移除图片组件，添加转述文本"""
        caption_text = f"[图片描述: {image_caption}]"
        # 移除所有图片组件
        filtered_list = [
            item for item in content_list if item.get("type") != "image_url"
        ]
        # 添加转述文本
        filtered_list.append({"type": "text", "text": caption_text})
        return filtered_list
    
    def _extract_image_components(self, original_content: Any) -> List[Dict[str, Any]]:
        """从原始内容中提取图片组件"""
        if not isinstance(original_content, list):
            return []
        
        return [
            item for item in original_content 
            if item.get("type") == "image_url"
        ]