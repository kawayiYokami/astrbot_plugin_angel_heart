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
    # --- 新增字段以支持上下文状态管理 ---
    boundary_timestamp: float = Field(default=0.0, description="分析快照的边界时间戳，用于状态推进")
    # --- 新增字段以支持搜索判断 ---
    needs_search: bool = Field(default=False, description="是否需要搜索百科知识来确认事实或补充信息")
    # --- 新增字段以保存对话快照 ---
    recent_dialogue: List[Dict] = Field(default_factory=list, description="决策时的最新对话快照，用于后续生成提示词")
    # --- 新增字段以支持天使之眼集成 ---
    angel_eye_request: Optional[AngelEyeRequest] = Field(default=None, description="天使之眼查询请求，当needs_search为true时使用")
