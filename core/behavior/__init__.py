"""
行为画像模块 — 纯统计，零 LLM 依赖

提供:
- MessageCleaner: 过滤代码块、转发、引用
- LiwcAnalyzer: LIWC 词频统计（情绪/代词/认知）
- BehaviorBaseline: 长期行为基线计算
- BehaviorProfileManager: 全局管理器，接入消息流
"""
from .analyzer import MessageCleaner, LiwcAnalyzer, LiwcResult
from .baseline import BehaviorBaseline
from .profile_manager import BehaviorProfileManager
 
