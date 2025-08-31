from pydantic import BaseModel, Field
from typing import Optional

class SecretaryDecision(BaseModel):
    """秘书AI的决策结果"""
    should_reply: bool = Field(..., description="是否应该介入回复")
    reply_strategy: str = Field(..., description="建议的回复策略，例如：缓和气氛、技术指导、表示共情等")
    topic: str = Field(..., description="当前对话的核心主题")
