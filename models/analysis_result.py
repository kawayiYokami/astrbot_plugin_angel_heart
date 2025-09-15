import datetime
from pydantic import BaseModel, Field

class SecretaryDecision(BaseModel):
    """秘书AI的决策结果"""
    should_reply: bool = Field(..., description="是否应该介入回复")
    reply_strategy: str = Field(..., description="建议的回复策略，例如：缓和气氛、技术指导、表示共情等")
    topic: str = Field(..., description="当前对话的核心主题")
    # --- 新增字段以传递人格信息 ---
    persona_name: str = Field(default="", description="当前使用的人格名称")
    alias: str = Field(default="", description="AI的别名/昵称")
    # --- 新增字段以支持回复目标 ---
    reply_target: str = Field(default="", description="回复的目标用户昵称或ID")
    # --- 新增字段以支持超时机制 ---
    created_at: datetime.datetime = Field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc), description="决策创建的时间戳")
