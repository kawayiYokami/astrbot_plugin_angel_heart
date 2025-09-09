
"""
AngelHeart插件 - 天使心智能群聊/私聊交互插件

基于AngelHeart轻量级架构设计，实现两级AI协作体系。
采用"前台缓存，秘书定时处理"模式：
- 前台：接收并缓存所有合规消息
- 秘书：定时分析缓存内容，决定是否回复
"""

import asyncio
import time
import json
from collections import OrderedDict
from datetime import datetime
from typing import Dict, List

from astrbot.api.star import Star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.core.star.context import Context
from astrbot.api import logger

from .core.llm_analyzer import LLMAnalyzer
from .models.analysis_result import SecretaryDecision

# 定义缓存的最大尺寸
CACHE_MAX_SIZE = 100

class AngelHeartPlugin(Star):
    """AngelHeart插件 - 专注的智能回复员"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self.context = context

        # -- 状态与统计 --
        self.processed_messages = 0
        self.analyses_performed = 0
        self.replies_sent = 0
        self.expired_messages_cleaned = 0  # 过期消息清理计数
        self.performance_stats = {
            'last_analysis_duration': 0.0,
            'total_analysis_time': 0.0,
            'cache_hit_rate': 0.0
        }
        # 使用OrderedDict实现有大小限制的缓存
        self.analysis_cache: OrderedDict[str, SecretaryDecision] = OrderedDict()
        self.analysis_locks: Dict[str, asyncio.Lock] = {}
        """为每个会话(chat_id)维护一个锁，防止并发分析"""

        # -- 前台缓存与秘书调度 --
        self.unprocessed_messages: Dict[str, List[Dict]] = {}
        """前台缓存：存储每个会话的未处理用户消息"""
        self.last_analysis_time: Dict[str, float] = {}
        """秘书上次分析时间：用于控制分析频率"""
        self.analysis_interval = self.config.get("analysis_interval", 7.0)
        """秘书分析间隔：两次分析之间的最小时间间隔（秒）"""
        self.cache_expiry = self.config.get("cache_expiry", 3600)
        """缓存过期时间：消息缓存的过期时间（秒）"""
        # -- 常量定义 --
        self.DEFAULT_TIMESTAMP_FALLBACK_SECONDS = 3600  # 默认时间戳回退时间（1小时）
        self.DB_HISTORY_MERGE_LIMIT = 5  # 数据库历史记录合并限制



        # -- 核心组件 --
        # 初始化 LLMAnalyzer
        analyzer_model_name = self.config.get("analyzer_model")
        reply_strategy_guide = self.config.get("reply_strategy_guide", "")
        # 传递 context 对象，让 LLMAnalyzer 在需要时动态获取 provider
        self.llm_analyzer = LLMAnalyzer(analyzer_model_name, context, reply_strategy_guide)

        logger.info("💖 AngelHeart智能回复员初始化完成 (同步轻量级架构)")

    # --- 核心事件处理 ---
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE | filter.EventMessageType.PRIVATE_MESSAGE, priority=1)
    async def smart_reply_handler(self, event: AstrMessageEvent, *args, **kwargs):
        """智能回复员 - 前台职责：接收并缓存消息，在适当时机唤醒秘书"""
        chat_id = event.unified_msg_origin
        logger.info(f"AngelHeart[{chat_id}]: 收到消息")

        # 前置检查
        if not self._should_process(event):
            return

        # 前台职责1：无条件缓存所有合规消息
        await self._cache_message_as_front_desk(chat_id, event)

        # 前台职责2：检查是否到达秘书的预定工作时间
        if self._should_awaken_secretary(chat_id):
            # 获取或创建锁
            if chat_id not in self.analysis_locks:
                self.analysis_locks[chat_id] = asyncio.Lock()

            lock = self.analysis_locks[chat_id]

            # 检查锁是否已被占用，避免不必要的等待
            if lock.locked():
                logger.debug(f"AngelHeart[{chat_id}]: 分析已在进行中，跳过本次唤醒。")
                return

            async with lock:
                # 再次检查时间间隔，因为在等待锁的过程中条件可能已改变
                if self._should_awaken_secretary(chat_id):
                    # 唤醒秘书进行分析和决策
                    await self._awaken_secretary_for_analysis(chat_id, event)

    # --- LLM Request Hook ---
    @filter.on_llm_request()
    async def inject_oneshot_persona_on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """在LLM请求时，一次性注入由秘书分析得出的人格上下文"""
        chat_id = event.unified_msg_origin

        # 1. 从缓存中获取决策
        decision = self.analysis_cache.get(chat_id)

        # 2. 检查决策是否存在且有效
        if not decision or not decision.should_reply:
            # 如果没有决策或决策是不回复，则不进行任何操作
            return

        # 3. 严格检查参数合法性
        topic = getattr(decision, 'topic', None)
        strategy = getattr(decision, 'reply_strategy', None)

        if not topic or not strategy:
            # 如果话题或策略为空，则不进行任何操作，防止污染
            logger.debug(f"AngelHeart[{chat_id}]: 决策参数不合法 (topic: {topic}, strategy: {strategy})，跳过人格注入。")
            return

        # 4. 构建补充提示词
        persona_context = f"\n\n---\n[AngelHeart秘书提醒] 请围绕以下要点回复：\n- 核心话题: {topic}\n- 回复策略: {strategy}"

        # 5. 注入到 req.prompt 的末尾
        req.prompt = f"{req.prompt}{persona_context}"
        logger.debug(f"AngelHeart[{chat_id}]: 已注入人格上下文到LLM请求。")

        # 6. 用后即焚：删除缓存中的决策，确保只使用一次
        if self.analysis_cache.pop(chat_id, None) is not None:
            logger.debug(f"AngelHeart[{chat_id}]: 已从缓存中移除一次性决策。")

    # --- 指令实现 ---
    @filter.command("angelheart")
    async def handle_status_command(self, event: AstrMessageEvent):
        status_report = []
        status_report.append("💖 AngelHeart 运行状态 💖")
        status_report.append("--------------------")
        status_report.append("总览:")
        status_report.append(f"- 已处理消息总数: {self.processed_messages}")
        status_report.append(f"- 已执行分析总数: {self.analyses_performed}")
        status_report.append(f"- 已发送主动回复: {self.replies_sent}")
        status_report.append(f"- 前台缓存消息数: {sum(len(msgs) for msgs in self.unprocessed_messages.values())}")
        status_report.append("--------------------")
        status_report.append("分析缓存 (最近5条):")

        if not self.analysis_cache:
            status_report.append("缓存为空，还没有任何分析结果。")
        else:
            # 显示最近的5条分析缓存
            cached_items = list(self.analysis_cache.items())
            for chat_id, result in reversed(cached_items[-5:]):
                if result:
                    topic = result.topic
                    status_report.append(f"- {chat_id}:")
                    status_report.append(f"  - 话题: {topic}")
                else:
                    status_report.append(f"- {chat_id}: (分析数据不完整)")

        await event.reply("\n".join(status_report))

    @filter.command("angelheart_reset")
    async def handle_reset_command(self, event: AstrMessageEvent):
        chat_id = event.unified_msg_origin
        # 重置前台缓存和秘书分析时间
        if chat_id in self.unprocessed_messages:
            self.unprocessed_messages[chat_id].clear()
        if chat_id in self.last_analysis_time:
            self.last_analysis_time[chat_id] = 0
        await event.reply("✅ 本会话的 AngelHeart 状态已重置。")

    @filter.command("angelheart_health")
    async def handle_health_command(self, event: AstrMessageEvent):
        """健康检查命令，显示插件状态信息"""
        chat_id = event.unified_msg_origin

        # 统计信息
        total_sessions = len(self.unprocessed_messages)
        total_cached_messages = sum(len(messages) for messages in self.unprocessed_messages.values())
        last_analysis = self.last_analysis_time.get(chat_id, 0)
        analysis_interval = self.analysis_interval
        cache_expiry = self.cache_expiry

        # 当前会话信息
        current_session_messages = len(self.unprocessed_messages.get(chat_id, []))

        # 格式化时间
        current_time = time.time()
        time_since_last_analysis = current_time - last_analysis if last_analysis > 0 else 0

        health_info = [
            "🏥 AngelHeart 健康检查报告",
            f"📊 总体统计:",
            f" - 活跃会话数: {total_sessions}",
            f"  - 缓存消息总数: {total_cached_messages}",
            f"  - 分析间隔: {analysis_interval}秒",
            f"  - 缓存过期时间: {cache_expiry}秒",
            f"",
            f"💬 当前会话 ({chat_id}):",
            f"  - 缓存消息数: {current_session_messages}",
            f"  - 上次分析时间: {time_since_last_analysis:.1f}秒前" if last_analysis > 0 else "  - 尚未进行分析",
        ]

        await event.reply("\n".join(health_info))

    # --- 内部方法 ---
    def update_analysis_cache(self, chat_id: str, result: SecretaryDecision):
        """更新分析缓存和统计"""
        self.analyses_performed += 1
        if result.should_reply:
            self.replies_sent += 1

        self.analysis_cache[chat_id] = result
        # 如果缓存超过最大尺寸，则移除最旧的条目
        if len(self.analysis_cache) > CACHE_MAX_SIZE:
            self.analysis_cache.popitem(last=False)
        logger.info(f"AngelHeart[{chat_id}]: 分析完成，已更新缓存。决策: {'回复' if result.should_reply else '不回复'} | 策略: {result.reply_strategy} | 话题: {result.topic}")

    def reload_config(self, new_config: dict):
        """重新加载配置"""
        old_config = self.config.copy()
        self.config = new_config or {}

        # 更新配置项
        self.analysis_interval = self.config.get("analysis_interval", 7.0)
        self.cache_expiry = self.config.get("cache_expiry", 3600)

        logger.info(f"AngelHeart: 配置已更新。分析间隔: {self.analysis_interval}秒, 缓存过期时间: {self.cache_expiry}秒")

    def _get_plain_chat_id(self, unified_id: str) -> str:
        """从 unified_msg_origin 中提取纯净的聊天ID (QQ号)"""
        parts = unified_id.split(':')
        return parts[-1] if parts else ""

    def _should_process(self, event: AstrMessageEvent) -> bool:
        """检查是否需要处理此消息"""
        chat_id = event.unified_msg_origin

        # 1. 忽略指令或@自己的消息
        if event.is_at_or_wake_command:
            # logger.info(f"AngelHeart[{chat_id}]: 消息是指令或@, 已忽略")
            return False
        if event.get_sender_id() == event.get_self_id():
            logger.info(f"AngelHeart[{chat_id}]: 消息由自己发出, 已忽略")
            return False

        # 2. 忽略空消息
        if not event.message_str or not event.message_str.strip():
            logger.info(f"AngelHeart[{chat_id}]: 消息内容为空, 已忽略")
            return False

        # 3. (可选) 检查白名单
        if self.config.get("whitelist_enabled", False):
            plain_chat_id = self._get_plain_chat_id(chat_id)
            # 将配置中的ID列表转换为字符串以确保类型匹配
            whitelist = [str(cid) for cid in self.config.get("chat_ids", [])]

            if plain_chat_id not in whitelist:
                logger.info(f"AngelHeart[{chat_id}]: 会话未在白名单中, 已忽略")
                return False

        logger.info(f"AngelHeart[{chat_id}]: 消息通过所有前置检查, 准备处理...")
        return True

    # --- 前台与秘书协作方法 ---
    async def _cache_message_as_front_desk(self, chat_id: str, event: AstrMessageEvent):
        """前台职责：缓存新消息"""
        if chat_id not in self.unprocessed_messages:
            self.unprocessed_messages[chat_id] = []

        new_message = {
            'role': 'user',
            'content': event.message_str,
            'sender_name': event.get_sender_name(),
            'timestamp': time.time()
        }
        self.unprocessed_messages[chat_id].append(new_message)
        logger.debug(f"AngelHeart[{chat_id}]: 前台已缓存消息。当前缓存数: {len(self.unprocessed_messages[chat_id])}")

    def _should_awaken_secretary(self, chat_id: str) -> bool:
        """检查是否应该唤醒秘书进行分析"""
        current_time = self._get_current_time()
        last_time = self.last_analysis_time.get(chat_id, 0)
        return current_time - last_time >= self.analysis_interval

    def _clean_expired_messages(self, chat_id: str):
        """清理过期的缓存消息"""
        if chat_id not in self.unprocessed_messages:
            return

        current_time = self._get_current_time()
        expired_count = 0

        # 从后向前遍历，避免在迭代时修改列表
        messages = self.unprocessed_messages[chat_id]
        i = len(messages) - 1
        while i >= 0:
            msg = messages[i]
            # 检查消息时间戳是否过期
            if 'timestamp' in msg and current_time - msg['timestamp'] > self.cache_expiry:
                messages.pop(i)
                expired_count += 1
            i -= 1

        if expired_count > 0:
            logger.debug(f"AngelHeart[{chat_id}]: 清理了 {expired_count} 条过期消息，剩余 {len(messages)} 条")

        # 如果会话消息列表为空，删除该会话的键
        if not messages:
            self.unprocessed_messages.pop(chat_id, None)

    async def _awaken_secretary_for_analysis(self, chat_id: str, event: AstrMessageEvent):
        """秘书职责：分析缓存内容并做出决策"""
        logger.info(f"AngelHeart[{chat_id}]: 唤醒秘书进行分析...")
        self.last_analysis_time[chat_id] = time.time()

        try:
            # 清理过期消息
            self._clean_expired_messages(chat_id)

            # 秘书职责1：整合数据库历史与前台缓存，形成完整上下文
            db_history = await self._get_conversation_history(chat_id)
            cached_messages = self.unprocessed_messages.get(chat_id, [])

            # 智能合并上下文：基于时间戳去重
            full_context = await self._merge_contexts_intelligently(db_history, cached_messages, chat_id)

            if not full_context:
                logger.debug(f"AngelHeart[{chat_id}]: 上下文为空，无需分析。")
                return

            # 秘书职责2：调用分析器进行决策
            decision = await self.llm_analyzer.analyze_and_decide(conversations=full_context)
            self.update_analysis_cache(chat_id, decision)

            # 秘书职责3：执行决策
            if decision.should_reply:
                # 在唤醒核心前，将待处理历史（数据库历史记录）同步回数据库
                # 不包含当前消息，因为当前消息会在后续被核心系统处理并添加到记录中
                curr_cid = await self.context.conversation_manager.get_curr_conversation_id(chat_id)
                if curr_cid:
                    await self.context.conversation_manager.update_conversation(
                        unified_msg_origin=chat_id,
                        conversation_id=curr_cid,
                        history=db_history  # 只同步数据库历史记录，不包含当前消息
                    )
                logger.info(f"AngelHeart[{chat_id}]: 决策为'参与'，已同步待处理历史并唤醒核心。策略: {decision.reply_strategy}")
                event.is_at_or_wake_command = True
            else:
                logger.info(f"AngelHeart[{chat_id}]: 决策为'不参与'。")

            # 所有操作成功完成后再清空当前缓存，准备接收新一轮消息
            self.unprocessed_messages[chat_id] = []

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 秘书处理过程中出错: {e}", exc_info=True)

    async def _merge_contexts_intelligently(self, db_history: List[Dict], cached_messages: List[Dict], chat_id: str) -> List[Dict]:
        """智能合并数据库历史和缓存消息，基于时间戳和内容去重"""
        if not cached_messages:
            return db_history

        if not db_history:
            return cached_messages

        # 获取数据库中最新的消息时间作为基准
        latest_db_time = self._get_latest_message_time(db_history)

        # 收集数据库中的内容用于去重检查（检查最近N条消息）
        db_contents = set()
        for msg in db_history[-self.DB_HISTORY_MERGE_LIMIT:]:  # 只检查最近N条以避免性能问题
            content = msg.get('content', '').strip()
            if content:  # 只添加非空内容
                db_contents.add(content)

        # 过滤缓存消息：只保留比数据库最新消息更新且内容不重复的消息
        fresh_cached_messages = []
        for msg in cached_messages:
            msg_time = msg.get('timestamp', 0)
            msg_content = msg.get('content', '').strip()

            # 只保留更新的且不重复的消息
            if msg_time > latest_db_time and msg_content not in db_contents:
                fresh_cached_messages.append(msg)

        logger.debug(f"AngelHeart[{chat_id}]: 智能合并 - 数据库消息{len(db_history)}条, "
                    f"缓存消息{len(cached_messages)}条 -> 过滤后{len(fresh_cached_messages)}条新鲜消息")

        # 合并上下文：数据库历史 + 新鲜缓存消息
        return db_history + fresh_cached_messages

    def _get_latest_message_time(self, messages: List[Dict]) -> float:
        """获取消息列表中最新消息的时间戳"""
        if not messages:
            return 0.0

        # 尝试从消息中提取时间戳
        latest_time = 0.0
        for msg in messages:
            # 优先使用消息自带的时间戳
            msg_time = msg.get('timestamp', 0)
            if isinstance(msg_time, (int, float)) and msg_time > latest_time:
                latest_time = msg_time

        # 如果所有消息都没有时间戳，使用当前时间作为基准
        if latest_time == 0.0:
            latest_time = time.time() - self.DEFAULT_TIMESTAMP_FALLBACK_SECONDS  # 默认1小时前
            logger.debug(f"AngelHeart: 消息时间戳回退到默认值 {latest_time} ({self.DEFAULT_TIMESTAMP_FALLBACK_SECONDS}秒前)")

        return latest_time

    async def _get_conversation_history(self, chat_id: str) -> List[Dict]:
        """获取当前会话的完整对话历史"""
        try:
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(chat_id)
            if not curr_cid:
                logger.debug(f"未找到当前会话的对话ID: {chat_id}")
                return []

            conversation = await self.context.conversation_manager.get_conversation(chat_id, curr_cid)
            if not conversation or not conversation.history:
                logger.debug(f"对话对象为空或无历史记录: {curr_cid}")
                return []

            history = json.loads(conversation.history)
            return history

        except json.JSONDecodeError as e:
            logger.error(f"解析对话历史JSON失败: {e}")
            return []
        except Exception as e:
            logger.error(f"获取对话历史时发生未知错误: {e}", exc_info=True)
            return []

    async def on_destroy(self):
        """插件销毁时的清理工作"""
        logger.info("💖 AngelHeart 插件已销毁")
