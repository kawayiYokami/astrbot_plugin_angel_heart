"""
AngelHeart 插件 - 离场应答策略生成模块

负责在检测到复读或密集发言时，生成一次性回复策略，
由主脑统一生成回复内容，保持架构一致性。
"""



try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from ..models.analysis_result import SecretaryDecision


class FishingDirectReply:
    """离场应答策略生成器。

    负责为离场时的单次应答生成策略，不直接生成回复内容。
    """

    def __init__(self, config_manager, angel_context):
        """
        初始化离场应答策略生成器

        Args:
            config_manager: 配置管理器
            angel_context: AngelHeart全局上下文
        """
        self.config_manager = config_manager
        self.angel_context = angel_context



    async def generate_reply_strategy(self, chat_id: str, event, trigger_type: str) -> SecretaryDecision:
        """
        生成离场应答策略

        Args:
            chat_id: 聊天会话ID
            event: 消息事件
            trigger_type: 触发类型 (echo/dense_conversation)

        Returns:
            SecretaryDecision: 回复决策对象
        """
        try:
            logger.debug(f"AngelHeart[{chat_id}]: 生成离场应答策略，触发类型: {trigger_type}")

            # 1. 根据触发类型选择策略
            if trigger_type == "echo_chamber":
                strategy = "跟紧复读队形"
                topic = "复读互动"
            else:  # dense_conversation
                strategy = "回应热闹聊天"
                topic = "密集讨论"

            # 2. 创建决策对象 - 按照RAG规范添加字段
            decision = SecretaryDecision(
                should_reply=True,
                reply_strategy=strategy,
                topic=topic,
                reply_target="",
                entities=[],  # 实体应由LLM从实际内容中提取，离场应答不预填
                facts=[f"系统{strategy}"],  # 极简日志模式，不超过15字
                keywords=[topic]  # 核心搜索词
            )

            logger.debug(f"AngelHeart[{chat_id}]: 生成策略: {strategy}")
            return decision

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 生成离场应答策略失败: {e}", exc_info=True)
            # 返回默认策略 - 按照RAG规范添加字段
            return SecretaryDecision(
                should_reply=True,
                reply_strategy="简单回应",
                topic="离场应答",
                reply_target="",
                entities=[],  # 实体应由LLM从实际内容中提取，离场应答不预填
                facts=["系统简单回应"],  # 极简日志模式，不超过15字
                keywords=["离场应答"]  # 核心搜索词
            )
