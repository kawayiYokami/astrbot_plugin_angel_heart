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

            # 0. 固化决策上下文：与秘书路径保持一致，主脑 rewrite 必须用同一份切片。
            #    缺失时执行链 _get_decision_context_for_rewrite 群聊分支会返回 None 并抛异常。
            #
            # 原子性说明（为何不做跨管理器联合 API）：
            # - 边界 ID 来自事件 extra（防抖调度时已固化），不是现场读取共享状态；
            # - 账本快照读取 get_all_messages 自带 _lock，且两行之间无 await，无竞态窗口；
            # - 快照按边界 ID 包含式截断（_slice_messages_through_id），新入账消息也会被切掉，
            #   不会出现"边界说 5、内容含 6"。
            #
            # 边界消息为何不会被整理收掉：
            # - 整理（_rule_organize）只从最新消息往前保留预算内消息，旧消息才收进摘要；
            # - 离场应答的边界消息 = 触发本轮防抖的最新消息，必在保留区内，不会被收掉。
            try:
                boundary_message_id = self.angel_context.debounce_manager.get_end_message_id(
                    event
                )
                if not boundary_message_id:
                    boundary_message_id = str(
                        getattr(getattr(event, "message_obj", None), "message_id", "")
                        or ""
                    )
                historical_context, recent_dialogue, boundary_ts = (
                    self.angel_context.conversation_ledger.get_context_snapshot(
                        chat_id, boundary_message_id
                    )
                )
                if hasattr(event, "set_extra"):
                    event.set_extra(
                        "angelheart_decision_context",
                        {
                            "historical_context": historical_context,
                            "recent_dialogue": recent_dialogue,
                            "boundary_ts": boundary_ts,
                            "boundary_message_id": boundary_message_id,
                        },
                    )
            except Exception as e:
                logger.warning(f"AngelHeart[{chat_id}]: 固化离场应答决策上下文失败: {e}")

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

            # 固化决策上下文：与秘书路径对齐，
            # 否则群聊执行决策时必抛“秘书决策上下文缺失”
            try:
                if decision.should_reply and hasattr(event, "set_extra"):
                    boundary_message_id = (
                        self.angel_context.debounce_manager.get_end_message_id(event)
                    )
                    if not boundary_message_id:
                        boundary_message_id = str(
                            getattr(getattr(event, "message_obj", None), "message_id", "") or ""
                        )
                    historical_context, recent_dialogue, boundary_ts = (
                        self.angel_context.conversation_ledger.get_context_snapshot(
                            chat_id, boundary_message_id
                        )
                    )
                    event.set_extra(
                        "angelheart_decision_context",
                        {
                            "historical_context": historical_context,
                            "recent_dialogue": recent_dialogue,
                            "boundary_ts": boundary_ts,
                            "boundary_message_id": boundary_message_id,
                        },
                    )
            except Exception as e:
                logger.warning(f"AngelHeart[{chat_id}]: 离场应答固化决策上下文失败: {e}")

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
