import datetime
from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

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
    reasoning: Optional[str] = Field(default=None, description="推理说明")
    trigger_type: Optional[str] = Field(default=None, description="触发类型")
    confidence: Optional[float] = Field(default=0.5, description="置信度")
    alias: Optional[str] = Field(default=None, description="昵称")
    boundary_timestamp: Optional[float] = Field(default=None, description="边界时间戳")
    recent_dialogue: Optional[List[Dict]] = Field(default=None, description="最近对话")
    created_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
