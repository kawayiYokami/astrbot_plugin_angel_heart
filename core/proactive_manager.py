"""
AngelHeart 插件 - 主动应答管理器

提供主动应答功能的统一接口，支持插件化扩展自定义主动应答逻辑。
"""

import time
import asyncio
from typing import Dict, Optional, Callable
from enum import Enum

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .angel_heart_status import AngelHeartStatus


class ProactiveTriggerType(Enum):
    """主动触发类型枚举"""
    IMMEDIATE = "immediate"  # 立即触发
    DELAYED = "delayed"     # 延迟触发
    SCHEDULED = "scheduled"  # 定时触发


class ProactiveRequest:
    """主动应答请求"""

    def __init__(
        self,
        chat_id: str,
        trigger_type: ProactiveTriggerType,
        strategy: str,
        topic: str,
        delay_seconds: float = 0,
        scheduled_time: Optional[float] = None,
        context_data: Optional[Dict] = None,
        callback: Optional[Callable] = None
    ):
        """
        初始化主动应答请求

        Args:
            chat_id: 聊天会话ID
            trigger_type: 触发类型
            strategy: 回复策略
            topic: 话题
            delay_seconds: 延迟秒数（仅用于 DELAYED 类型）
            scheduled_time: 定时时间戳（仅用于 SCHEDULED 类型）
            context_data: 上下文数据
            callback: 完成回调函数
        """
        self.chat_id = chat_id
        self.trigger_type = trigger_type
        self.strategy = strategy
        self.topic = topic
        self.delay_seconds = delay_seconds
        self.scheduled_time = scheduled_time
        self.context_data = context_data or {}
        self.callback = callback
        self.created_at = time.time()
        self.task: Optional[asyncio.Task] = None


class ProactiveManager:
    """主动应答管理器"""

    def __init__(self, angel_context):
        """
        初始化主动应答管理器

        Args:
            angel_context: AngelHeart全局上下文
        """
        self.angel_context = angel_context

        # 活跃的主动应答任务
        self.active_tasks: Dict[str, ProactiveRequest] = {}

        # 自定义触发器注册表
        self.custom_triggers: Dict[str, Callable] = {}

        # 锁保护
        self._lock = asyncio.Lock()

    async def trigger_immediate(
        self,
        chat_id: str,
        strategy: str,
        topic: str,
        context_data: Optional[Dict] = None,
        callback: Optional[Callable] = None
    ) -> bool:
        """
        立即触发主动应答

        Args:
            chat_id: 聊天会话ID
            strategy: 回复策略
            topic: 话题
            context_data: 上下文数据
            callback: 完成回调函数

        Returns:
            bool: 是否成功触发
        """
        try:
            # 检查当前状态
            current_status = self.angel_context.get_chat_status(chat_id)
            if current_status != AngelHeartStatus.NOT_PRESENT:
                logger.debug(f"AngelHeart[{chat_id}]: 当前状态为 {current_status.value}，跳过主动应答")
                return False

            # 创建请求
            request = ProactiveRequest(
                chat_id=chat_id,
                trigger_type=ProactiveTriggerType.IMMEDIATE,
                strategy=strategy,
                topic=topic,
                context_data=context_data,
                callback=callback
            )

            # 立即执行
            await self._execute_proactive_request(request)
            return True

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 立即主动应答失败: {e}", exc_info=True)
            return False

    async def trigger_delayed(
        self,
        chat_id: str,
        strategy: str,
        topic: str,
        delay_seconds: float,
        context_data: Optional[Dict] = None,
        callback: Optional[Callable] = None
    ) -> bool:
        """
        延迟触发主动应答

        Args:
            chat_id: 聊天会话ID
            strategy: 回复策略
            topic: 话题
            delay_seconds: 延迟秒数
            context_data: 上下文数据
            callback: 完成回调函数

        Returns:
            bool: 是否成功安排
        """
        try:
            async with self._lock:
                # 取消该会话的现有任务
                await self._cancel_chat_task(chat_id)

                # 创建延迟请求
                request = ProactiveRequest(
                    chat_id=chat_id,
                    trigger_type=ProactiveTriggerType.DELAYED,
                    strategy=strategy,
                    topic=topic,
                    delay_seconds=delay_seconds,
                    context_data=context_data,
                    callback=callback
                )

                # 创建异步任务
                request.task = asyncio.create_task(
                    self._delayed_handler(request)
                )

                # 注册任务
                self.active_tasks[chat_id] = request

                logger.info(
                    f"AngelHeart[{chat_id}]: 已安排延迟主动应答，{delay_seconds}秒后执行"
                )
                return True

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 安排延迟主动应答失败: {e}", exc_info=True)
            return False

    async def trigger_scheduled(
        self,
        chat_id: str,
        strategy: str,
        topic: str,
        scheduled_time: float,
        context_data: Optional[Dict] = None,
        callback: Optional[Callable] = None
    ) -> bool:
        """
        定时触发主动应答

        Args:
            chat_id: 聊天会话ID
            strategy: 回复策略
            topic: 话题
            scheduled_time: 定时时间戳
            context_data: 上下文数据
            callback: 完成回调函数

        Returns:
            bool: 是否成功安排
        """
        try:
            async with self._lock:
                # 取消该会话的现有任务
                await self._cancel_chat_task(chat_id)

                # 计算延迟时间
                delay = max(0, scheduled_time - time.time())

                # 创建定时请求
                request = ProactiveRequest(
                    chat_id=chat_id,
                    trigger_type=ProactiveTriggerType.SCHEDULED,
                    strategy=strategy,
                    topic=topic,
                    scheduled_time=scheduled_time,
                    context_data=context_data,
                    callback=callback
                )

                # 创建异步任务
                request.task = asyncio.create_task(
                    self._scheduled_handler(request, delay)
                )

                # 注册任务
                self.active_tasks[chat_id] = request

                logger.info(
                    f"AngelHeart[{chat_id}]: 已安排定时主动应答，将在 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(scheduled_time))} 执行"
                )
                return True

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 安排定时主动应答失败: {e}", exc_info=True)
            return False

    def register_custom_trigger(self, name: str, trigger_func: Callable):
        """
        注册自定义触发器

        Args:
            name: 触发器名称
            trigger_func: 触发函数，签名为 async func(chat_id: str, context_data: Dict) -> bool
        """
        self.custom_triggers[name] = trigger_func
        logger.info(f"AngelHeart: 已注册自定义触发器 '{name}'")

    def unregister_custom_trigger(self, name: str):
        """
        注销自定义触发器

        Args:
            name: 触发器名称
        """
        if name in self.custom_triggers:
            del self.custom_triggers[name]
            logger.info(f"AngelHeart: 已注销自定义触发器 '{name}'")

    async def call_custom_trigger(self, name: str, chat_id: str, context_data: Optional[Dict] = None) -> bool:
        """
        调用自定义触发器

        Args:
            name: 触发器名称
            chat_id: 聊天会话ID
            context_data: 上下文数据

        Returns:
            bool: 触发是否成功
        """
        if name not in self.custom_triggers:
            logger.warning(f"AngelHeart: 自定义触发器 '{name}' 不存在")
            return False

        try:
            trigger_func = self.custom_triggers[name]
            result = await trigger_func(chat_id, context_data or {})
            return result
        except Exception as e:
            logger.error(f"AngelHeart: 调用自定义触发器 '{name}' 失败: {e}", exc_info=True)
            return False

    async def cancel_chat_task(self, chat_id: str) -> bool:
        """
        取消指定会话的主动应答任务

        Args:
            chat_id: 聊天会话ID

        Returns:
            bool: 是否成功取消
        """
        async with self._lock:
            return await self._cancel_chat_task(chat_id)

    async def _cancel_chat_task(self, chat_id: str) -> bool:
        """内部方法：取消任务（需要在锁内调用）"""
        if chat_id in self.active_tasks:
            request = self.active_tasks.pop(chat_id)
            if request.task and not request.task.done():
                request.task.cancel()
                logger.debug(f"AngelHeart[{chat_id}]: 已取消主动应答任务")
                return True
        return False

    async def _execute_proactive_request(self, request: ProactiveRequest):
        """
        执行主动应答请求

        Args:
            request: 主动应答请求
        """
        try:
            chat_id = request.chat_id

            # 检查状态
            current_status = self.angel_context.get_chat_status(chat_id)
            if current_status != AngelHeartStatus.NOT_PRESENT:
                logger.debug(f"AngelHeart[{chat_id}]: 状态已变更为 {current_status.value}，取消主动应答")
                return

            # 转换到被呼唤状态
            await self.angel_context.transition_to_status(
                chat_id,
                AngelHeartStatus.SUMMONED,
                f"主动应答: {request.topic}"
            )

            # 创建决策对象 - 按照RAG规范添加字段
            from ..models.analysis_result import SecretaryDecision
            decision = SecretaryDecision(
                should_reply=True,
                reply_strategy=request.strategy,
                topic=request.topic,
                reply_target="",
                is_questioned=False,
                is_interesting=True,
                entities=[],  # 实体应由LLM从实际内容中提取，主动应答场景暂不提供
                facts=[f"系统主动发起{request.topic}"],  # 极简日志模式，不超过15字
                keywords=[request.topic]  # 核心搜索词
            )

            # 存储决策
            await self.angel_context.update_analysis_cache(
                chat_id,
                decision,
                reason="主动应答"
            )

            # 更新分析时间
            await self.angel_context.update_last_analysis_time(chat_id)

            logger.info(
                f"AngelHeart[{chat_id}]: 主动应答已触发 - 话题: {request.topic}, 策略: {request.strategy}"
            )

            # 调用回调
            if request.callback:
                try:
                    await request.callback(chat_id, decision, request.context_data)
                except Exception as e:
                    logger.error(f"AngelHeart[{chat_id}]: 主动应答回调失败: {e}", exc_info=True)

        except Exception as e:
            logger.error(f"AngelHeart[{request.chat_id}]: 执行主动应答失败: {e}", exc_info=True)

    async def _delayed_handler(self, request: ProactiveRequest):
        """延迟处理器"""
        try:
            await asyncio.sleep(request.delay_seconds)
            await self._execute_proactive_request(request)
        except asyncio.CancelledError:
            logger.debug(f"AngelHeart[{request.chat_id}]: 延迟主动应答被取消")
        except Exception as e:
            logger.error(f"AngelHeart[{request.chat_id}]: 延迟主动应答处理失败: {e}", exc_info=True)
        finally:
            # 清理任务
            async with self._lock:
                self.active_tasks.pop(request.chat_id, None)

    async def _scheduled_handler(self, request: ProactiveRequest, delay: float):
        """定时处理器"""
        try:
            await asyncio.sleep(delay)
            await self._execute_proactive_request(request)
        except asyncio.CancelledError:
            logger.debug(f"AngelHeart[{request.chat_id}]: 定时主动应答被取消")
        except Exception as e:
            logger.error(f"AngelHeart[{request.chat_id}]: 定时主动应答处理失败: {e}", exc_info=True)
        finally:
            # 清理任务
            async with self._lock:
                self.active_tasks.pop(request.chat_id, None)

    def get_active_tasks(self) -> Dict[str, Dict]:
        """
        获取活跃任务列表

        Returns:
            Dict: 活跃任务信息
        """
        result = {}
        for chat_id, request in self.active_tasks.items():
            result[chat_id] = {
                "trigger_type": request.trigger_type.value,
                "strategy": request.strategy,
                "topic": request.topic,
                "created_at": request.created_at,
                "delay_seconds": request.delay_seconds,
                "scheduled_time": request.scheduled_time
            }
        return result

    async def cleanup(self):
        """清理所有任务"""
        async with self._lock:
            for chat_id in list(self.active_tasks.keys()):
                await self._cancel_chat_task(chat_id)
            logger.info("AngelHeart: 主动应答管理器已清理所有任务")