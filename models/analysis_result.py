import datetime
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

class AngelEyeRequest(BaseModel):
    """天使之眼查询请求"""
    required_docs: Dict[str, Dict[str, List[str]]] = Field(
        default_factory=dict,
        description="需要查询的文档，键是实体名称，值是包含keywords的对象"
    )
    required_facts: List[str] = Field(
        default_factory=list,
        description="需要查询的结构化事实，格式为'实体名.属性名'"
    )
    chat_history: Dict[str, Any] = Field(
        default_factory=dict,
        description="聊天记录查询参数，包含time_range_hours、filter_user_ids、keywords等"
    )

class SecretaryDecision(BaseModel):
    """
    秘书决策数据模型
    """
    should_reply: bool = Field(description="是否需要回复")
    is_questioned: bool = Field(default=False, description="是否被追问")
    is_interesting: bool = Field(default=False, description="话题是否有趣")
    reply_strategy: str = Field(description="回复策略")
    topic: str = Field(description="话题")
    reply_target: str = Field(default="", description="回复目标")
    needs_search: bool = Field(default=False, description="是否需要搜索")
    angel_eye_request: Optional[Dict[str, Any]] = Field(default=None, description="天使之眼请求")
    reasoning: Optional[str] = Field(default=None, description="推理说明")
    trigger_type: Optional[str] = Field(default=None, description="触发类型")
    confidence: Optional[float] = Field(default=0.5, description="置信度")
    alias: Optional[str] = Field(default=None, description="昵称")
    boundary_timestamp: Optional[float] = Field(default=None, description="边界时间戳")
    recent_dialogue: Optional[List[Dict]] = Field(default=None, description="最近对话")
    created_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
