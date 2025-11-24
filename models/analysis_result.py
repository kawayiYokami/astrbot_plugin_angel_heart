from typing import List
from pydantic import BaseModel, Field

class SecretaryDecision(BaseModel):
    """
    秘书决策数据模型
    """
    should_reply: bool = Field(description="是否需要介入")
    is_questioned: bool = Field(default=False, description="是否被追问（用户在继续之前的话题或要求回应之前的回答）")
    is_interesting: bool = Field(default=False, description="话题是否有趣（符合AI身份、能提供价值、介入合适）")
    reply_strategy: str = Field(description="概述你计划采用的策略。如果 should_reply 为 false，此项应为 '继续观察'")
    topic: str = Field(description="对当前唯一核心话题的简要概括")
    reply_target: str = Field(default="", description="回复目标用户的昵称或ID。如果不需要回复，此项应为空字符串")
    entities: List[str] = Field(default=[], description="实体列表，优先级最高的发言人ID，其次是其他对话中的实体（包含但不限于人物、话题、物品、时间、地点、活动等）")
    facts: List[str] = Field(default=[], description="极简日志模式。只保留'谁 做了 什么'或'谁 提议 什么'。单句禁止超过15个字，禁止形容词")
    keywords: List[str] = Field(default=[], description="1-3个核心搜索词")
