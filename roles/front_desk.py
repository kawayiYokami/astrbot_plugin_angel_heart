"""
AngelHeart 插件 - 前台角色 (FrontDesk)
负责接收并缓存所有合规消息。
"""

import asyncio
import base64
import copy
import json
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from astrbot.api import logger
except ImportError:
    import logging

    logger = logging.getLogger(__name__)
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import At, File, Image, Plain, Reply
from typing import Any, List, Dict  # 导入类型提示

# 导入公共工具函数和 ConversationLedger
from ..core.utils import partition_dialogue_raw, format_final_prompt
from ..core.image_processor import ImageProcessor

from ..core.fishing_direct_reply import FishingDirectReply
from ..core.message_processor import MessageProcessor
from ..core.utils.content_utils import convert_content_to_string
from ..core.utils.message_hits import (
    build_message_metadata,
    extract_plain_body_from_components,
    metadata_has_hit,
    parse_pipe_phrases,
    parse_space_phrases,
)
from ..core.utils.message_utils import serialize_content_parts

# 导入状态枚举
from ..core.angel_heart_status import AngelHeartStatus, StatusChecker
from ..core.debounce_manager import PROCESS, KILL



class FrontDesk:
    """
    前台角色 - 专注的消息接收与缓存员
    """

    ASTRBOT_HISTORY_MESSAGE_LIMIT = 7
    ASTRBOT_HISTORY_TEXT_TOKEN_LIMIT = 10000
    SUPPORTED_TEXT_FILE_EXTENSIONS = {".txt", ".md"}
    MAX_TEXT_FILE_BYTES = 100 * 1024
    MAX_IMAGE_SOURCE_BYTES = 20 * 1024 * 1024
    BLANK_SENDER_NAME = "空白"
    INVALID_SENDER_IDS = {
        "",
        "0",
        "unknown",
        "none",
        "null",
        "user",
        "history_user",
        "assistant",
        "tool",
    }

    def __init__(self, config_manager, angel_context):
        """
        初始化前台角色。

        Args:
            config_manager: 配置管理器实例。
            angel_context: AngelHeart全局上下文实例。
        """
        self._config_manager = config_manager
        self.context = angel_context
        self.astr_context = angel_context.astr_context  # AstrBot 主上下文

        # 移除本地缓存：存储每个会话的未处理用户消息
        # self.unprocessed_messages: Dict[str, List[Dict]] = {}

        # 闭嘴状态已迁移到 angel_context.silenced_until

        # 初始化图片处理器
        self.image_processor = ImageProcessor()

        # 初始化混脸熟直接回复处理器（兼容保留，进场不再依赖）
        self.fishing_reply = FishingDirectReply(config_manager, angel_context)

        # 唤醒判定复用 StatusChecker
        self.status_checker = StatusChecker(config_manager, angel_context)

        # 来源登记（见过的人群/私聊，供 WebUI 认群）；由 main.py 注入
        self.chat_sources = None

        # 最近决策存储（每群最近一条，供 WebUI 状态栏）；由 main.py 注入
        self.last_decisions = None

        # secretary 引用将由 main.py 设置
        self.secretary = None

        # 私聊摘要是插件自建后台任务，必须登记，供去重与 terminate 完整取消。
        self._private_compression_tasks: Dict[str, asyncio.Task] = {}

    def _get_event_message_id(self, event: AstrMessageEvent) -> str:
        """读取 AstrBot 当前入站消息 ID。"""
        try:
            return str(getattr(event.message_obj, "message_id", "") or "")
        except Exception:
            return ""

    def _ensure_message_id(self, event: AstrMessageEvent) -> str:
        """确保 AstrBotMessage 带有消息 ID；仅在上游缺失时生成兜底 UUID。"""
        message_id = self._get_event_message_id(event)
        if message_id:
            return message_id
        try:
            message_id = uuid.uuid4().hex
            event.message_obj.message_id = message_id
            return message_id
        except Exception:
            return ""

    def _normalize_sender_name(self, sender_id: Any, *name_candidates: Any) -> str:
        """规范化发送者显示名。"""
        for candidate in name_candidates:
            if candidate is None:
                continue
            name = str(candidate).strip()
            if name:
                return name

        normalized_sender_id = str(sender_id or "").strip()
        if normalized_sender_id.lower() not in self.INVALID_SENDER_IDS:
            return self.BLANK_SENDER_NAME
        return ""

    def _file_name_from_url(self, url: str) -> str:
        try:
            parsed = urlparse(str(url or ""))
            return Path(unquote(parsed.path)).name
        except Exception:
            return ""

    def _normalize_local_file_path(self, path: str) -> str:
        path = str(path or "")
        if path.startswith("file://"):
            path = path[7:]
            if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
                path = path[1:]
        return path

    def _get_file_component_name(self, component: File) -> str:
        file_path = self._normalize_local_file_path(getattr(component, "file_", None) or "")
        file_url = getattr(component, "url", None) or ""
        return (
            getattr(component, "name", None)
            or Path(file_path).name
            or self._file_name_from_url(file_url)
            or "未知文件"
        )

    async def _read_image_component_bytes(self, component: Image) -> tuple[bytes, str]:
        source_ref = getattr(component, "url", None) or getattr(component, "file", None) or ""
        try:
            local_path = await component.convert_to_file_path()
            local_path = self._normalize_local_file_path(local_path)
            if local_path and os.path.exists(local_path):
                size = os.path.getsize(local_path)
                if size <= self.MAX_IMAGE_SOURCE_BYTES:
                    return Path(local_path).read_bytes(), source_ref or local_path
                logger.warning(f"AngelHeart: 图片文件过大，跳过缓存: {local_path}")
        except Exception as e:
            logger.debug(f"AngelHeart: convert_to_file_path 读取图片失败: {e}")

        try:
            base64_data = await component.convert_to_base64()
            if base64_data:
                encoded = base64_data.removeprefix("base64://")
                return base64.b64decode(encoded), source_ref
        except Exception as e:
            logger.debug(f"AngelHeart: convert_to_base64 读取图片失败: {e}")

        return b"", source_ref

    async def _build_cached_image_item(self, chat_id: str, component: Image) -> dict | None:
        raw_bytes, source_ref = await self._read_image_component_bytes(component)
        if not raw_bytes:
            return None

        image_cache = self.context.conversation_ledger.image_cache
        dhash = image_cache.put(chat_id, raw_bytes)
        if dhash:
            cache_path = str(image_cache.get_cached_path(chat_id, dhash))
            return {
                "type": "image_url",
                "image_url": {"url": cache_path},
                "cache_path": cache_path,
                "cache_dhash": dhash,
                "local_file_path": cache_path,
                "original_url": cache_path,
                "original_file_url": cache_path,
                "source_url": source_ref,
            }

        encoded = base64.b64encode(raw_bytes).decode("utf-8")
        data_url = f"data:image/jpeg;base64,{encoded}"
        return {
            "type": "image_url",
            "image_url": {"url": data_url},
            "original_url": data_url,
            "original_file_url": data_url,
            "source_url": source_ref,
        }

    async def _resolve_file_component_path(self, component: File) -> str:
        file_path = self._normalize_local_file_path(getattr(component, "file_", None) or "")
        if file_path and os.path.exists(file_path):
            return os.path.abspath(file_path)

        try:
            resolved = await component.get_file()
            resolved = self._normalize_local_file_path(resolved)
            if resolved and os.path.exists(resolved):
                return os.path.abspath(resolved)
        except Exception as e:
            logger.debug(f"AngelHeart: get_file() 获取文件失败: {e}")

        return ""

    async def _build_cached_file_text_item(self, chat_id: str, component: File) -> dict:
        name = self._get_file_component_name(component)
        ext = Path(name).suffix.lower()
        if ext not in self.SUPPORTED_TEXT_FILE_EXTENSIONS:
            logger.debug(f"AngelHeart[{chat_id}]: 不支持的文件类型 {ext}，已跳过: {name}")
            return {"type": "text", "text": f"[不支持的文件类型: {name}]"}

        local_path = await self._resolve_file_component_path(component)
        if not local_path:
            return {"type": "text", "text": f"[文件已失效: {name}]"}

        try:
            size = os.path.getsize(local_path)
        except OSError:
            return {"type": "text", "text": f"[文件已失效: {name}]"}

        if size > self.MAX_TEXT_FILE_BYTES:
            logger.debug(f"AngelHeart[{chat_id}]: 文本文件过大({size}bytes)，已跳过: {name}")
            return {"type": "text", "text": f"[文件过大，不支持: {name}]"}

        cached_path = self.context.conversation_ledger.image_cache.put_text_file(
            chat_id,
            local_path,
            name,
            max_bytes=self.MAX_TEXT_FILE_BYTES,
        )
        if not cached_path:
            return {"type": "text", "text": f"[文件缓存失败: {name}]"}

        try:
            content = cached_path.read_text(encoding="utf-8", errors="replace")
            return {
                "type": "text",
                "text": f"[文件: {name}]\n{content}",
                "cache_path": str(cached_path),
                "file_name": name,
            }
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 读取缓存文件内容失败: {e}")
            return {
                "type": "text",
                "text": f"[文件读取失败: {name}]",
                "cache_path": str(cached_path),
                "file_name": name,
            }

    async def cache_message(self, chat_id: str, event: AstrMessageEvent):
        """
        前台职责：缓存消息，并在入库时对正文做一次命中判定。

        Args:
            chat_id (str): 会话ID。
            event (AstrMessageEvent): 消息事件对象。
        """
        # 1. 展示正文：Plain + @显示名 + 文件名；匹配正文：只取 Plain
        outline = event.get_message_outline()
        message_chain = event.get_messages() or []
        body_text = extract_plain_body_from_components(message_chain)
        text_parts = []
        for component in message_chain:
            if isinstance(component, Plain) and component.text:
                text_parts.append(component.text)
            elif isinstance(component, At):
                # @消息的显示名也是消息内容的一部分，人类看到的就是 "@昵称"
                name = getattr(component, "name", None) or getattr(component, "display", None) or ""
                if name:
                    text_parts.append(f"@{name}")
            elif isinstance(component, File):
                file_name = self._get_file_component_name(component)
                if file_name:
                    text_parts.append(f"[文件: {file_name}]")
        text_content = "".join(text_parts).strip()
        if not text_content:
            # 展示兜底仍可用 outline；关键词匹配绝不回退 outline
            text_content = outline if outline and outline.strip() else ""

        logger.debug(
            f"AngelHeart[{chat_id}]: 缓存消息，展示正文: '{text_content}', 匹配正文: '{body_text}'"
        )

        # 2. 构建标准多模态 content 列表
        content_list = []
        if text_content:
            content_list.append({"type": "text", "text": text_content})

        # 3. 处理图片与文件组件
        for component in message_chain:
            if isinstance(component, Image):
                try:
                    item = await self._build_cached_image_item(chat_id, component)
                    if item:
                        content_list.append(item)
                    else:
                        content_list.append({"type": "text", "text": "[图片处理失败]"})
                except Exception as e:
                    original_url = component.url or component.file or "未知URL"
                    logger.debug(f"AngelHeart[{chat_id}]: 图片处理跳过，URL: {original_url}, 原因: {str(e)[:100]}")

            elif isinstance(component, File):
                try:
                    content_list.append(await self._build_cached_file_text_item(chat_id, component))
                except Exception as e:
                    logger.debug(f"AngelHeart[{chat_id}]: File 组件处理异常: {e}")
                    content_list.append({"type": "text", "text": f"[文件处理异常: {getattr(component, 'name', '')}]"})

        # 4. 如果没有内容，创建一个空文本
        if not content_list:
            content_list.append({"type": "text", "text": ""})

        # 5. 构建完整的消息字典
        source_message_id = self._get_event_message_id(event)

        # 检测是否为@自己的消息
        is_at_self = False
        try:
            self_id = str(event.get_self_id())
            for component in message_chain:
                if isinstance(component, At) and str(component.qq) == self_id:
                    is_at_self = True
                    break
                # 兼容非 strict At 类型
                qq = getattr(component, "qq", None)
                cls_name = component.__class__.__name__
                if qq is not None and str(qq) == self_id and ("At" in cls_name or "at" in cls_name.lower()):
                    is_at_self = True
                    break
        except Exception:
            pass

        metadata = build_message_metadata(
            body_text=body_text,
            alias_phrases=parse_pipe_phrases(
                getattr(self.config_manager.for_chat(chat_id), "alias", "")
            ),
            focus_phrases=parse_space_phrases(
                getattr(self.config_manager.for_chat(chat_id), "focus_instructions", "")
            ),
            is_at_self=is_at_self,
        )
        # 给当前事件挂一份，供调度点名立刻读取，避免再扫 outline
        try:
            if hasattr(event, "set_extra"):
                event.set_extra("angelheart_message_metadata", metadata)
                event.set_extra("angelheart_body_text", body_text)
        except Exception:
            pass

        new_message = {
            "role": "user",
            "content": content_list,  # 标准多模态列表
            "sender_id": event.get_sender_id(),
            "sender_name": self._normalize_sender_name(
                event.get_sender_id(),
                event.get_sender_name(),
            ),
            # 当前消息 ID：用于精确定位 ledger 边界并排除请求中的重复当前消息
            "source_message_id": source_message_id,
            "is_at_self": is_at_self,
            "metadata": metadata,
            "timestamp": (
                event.get_timestamp()
                if hasattr(event, "get_timestamp") and event.get_timestamp()
                else time.time()
            ),
        }
        # 6. 将消息添加到 Ledger。上下文清理由压缩策略统一控制，不再因离场状态触发。
        self.context.conversation_ledger.add_message(chat_id, new_message)

        # 7. 登记来源（供 WebUI 认群）：群聊取群名，私聊取发送者昵称，均为上游同步字段。
        self._record_chat_source(chat_id, event)

    def _record_chat_source(self, chat_id: str, event) -> None:
        """登记来源显示名，供绑定页认群。

        显示名全部取上游同步字段，不走异步 API：
        - 群聊：event.message_obj.group.group_name（aiocqhttp 已从 OneBot 事件填入）
        - 私聊：event.message_obj.sender.nickname
        """
        store = self.chat_sources
        if store is None:
            return
        try:
            message_obj = getattr(event, "message_obj", None)
            display_name = ""
            kind = "unknown"
            group = getattr(message_obj, "group", None) if message_obj else None
            if group is not None and getattr(group, "group_id", None):
                kind = "group"
                display_name = str(getattr(group, "group_name", "") or "")
            else:
                sender = getattr(message_obj, "sender", None) if message_obj else None
                if sender is not None and getattr(sender, "user_id", None):
                    kind = "private"
                    display_name = str(getattr(sender, "nickname", "") or "")
            store.record(chat_id, display_name, kind)
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 来源登记失败: {e}")

    async def handle_event(self, event: AstrMessageEvent):
        """
        处理新消息事件 - 集成4状态机制重构版
        根据状态系统智能分流：不在场→缓存，混脸熟→直接回复，被呼唤/观测期→秘书分析
        """
        chat_id = event.unified_msg_origin
        current_time = time.time()
        message_content = event.get_message_outline()

        try:
            self._ensure_message_id(event)

            # 优先进行超时检查
            await self._check_and_handle_timeout(chat_id, current_time)

            # 1. 基本合法性检查 (最高优先级)
            if not message_content.strip():
                logger.debug(f"AngelHeart[{chat_id}]: 空消息，跳过处理")
                return

            # 2. 闭嘴状态检查
            muted = chat_id in self.context.silenced_until and current_time < self.context.silenced_until[chat_id]
            unmuted_now = False
            cm = self.config_manager.for_chat(chat_id)
            if muted:
                speak_words_str = cm.speak_words
                if speak_words_str:
                    speak_words = [
                        word.strip() for word in speak_words_str.split("|") if word.strip()
                    ]
                    for word in speak_words:
                        if word in message_content:
                            self.context.silenced_until.pop(chat_id, None)
                            logger.info(
                                f"AngelHeart[{chat_id}]: 检测到张嘴词 '{word}'，解除闭嘴模式。"
                            )
                            unmuted_now = True
                            muted = False
                            break
                if muted:
                    remaining = self.context.silenced_until[chat_id] - current_time
                    logger.info(
                        f"AngelHeart[{chat_id}]: 处于闭嘴状态 (剩余 {remaining:.1f} 秒)，事件已终止。"
                    )
                    await self.context.debounce_manager.clear_chat(
                        chat_id, reason="silenced"
                    )
                    event.stop_event()
                    return

            # 3. 掌嘴词检测
            if not unmuted_now:
                slap_words_str = cm.slap_words
                if slap_words_str:
                    slap_words = [
                        word.strip() for word in slap_words_str.split("|") if word.strip()
                    ]
                    for word in slap_words:
                        if word in message_content:
                            silence_duration = cm.silence_duration
                            self.context.silenced_until[chat_id] = (
                                current_time + silence_duration
                            )
                            logger.info(
                                f"AngelHeart[{chat_id}]: 检测到掌嘴词 '{word}'，启动闭嘴模式 {silence_duration} 秒，事件已终止。"
                            )
                            await self.context.debounce_manager.clear_chat(
                                chat_id, reason="slap_words"
                            )
                            event.stop_event()
                            return

            # 4. 【核心】缓存消息
            await self.cache_message(chat_id, event)

            if event.get_extra("angelheart_blocked_by_provider_wake_prefix", False):
                logger.debug(
                    f"AngelHeart[{chat_id}]: 事件未命中额外聊天唤醒前缀，已缓存但跳过秘书分析。"
                )
                if cm.block_unapproved_wake_non_command:
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 已启用未批准非命令消息阻断，停止后续主 LLM 处理。"
                    )
                    event.stop_event()
                return

            # 私聊由主框架直接响应，这里只负责缓存，不走秘书/双防抖链路
            # 根因：AstrBot 无法在子代理中注入消息，私聊忙碌时只能队列
            if self._is_private_chat(chat_id):
                logger.debug(
                    f"AngelHeart[{chat_id}]: 私聊消息已缓存，跳过秘书与双防抖，等待主框架队列/直接响应。"
                )
                # 私聊摘要后台跑，不阻塞前台返回；同会话只保留一个已登记任务。
                try:
                    if self.context.conversation_ledger._should_compress(chat_id):
                        self._schedule_private_compression(chat_id)
                except Exception as e:
                    logger.warning(f"AngelHeart[{chat_id}]: 调度私聊摘要失败: {e}")
                return

            # 5. 群聊：双防抖（目的）+ 扣押事件（实现）
            await self._schedule_group_debounce(event)

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 前台事件处理异常: {e}", exc_info=True)
            # 发生异常时，终止事件传播
            event.stop_event()

    async def _schedule_group_debounce(self, event: AstrMessageEvent):
        """群聊双防抖调度：账本自管，等待挂在事件上。"""
        chat_id = event.unified_msg_origin
        sender_id = str(event.get_sender_id() or "")
        message_id = self._ensure_message_id(event)

        # 在场超时检查（离场）
        await self._check_and_handle_timeout(chat_id, time.time())

        is_wake = self.status_checker.is_event_wake(event)
        is_present = self.context.is_present(chat_id)
        cm = self.config_manager.for_chat(chat_id)
        # 入场门槛：开启「仅点名入场」时只有点名消息能进场；关闭时任何消息都进场处理
        can_enter = is_wake or not cm.enter_on_mention_only
        leave_reply_trigger = ""
        if not can_enter and not is_present:
            leave_reply_trigger = self.status_checker.get_leave_reply_trigger(chat_id)

        # 离场进场：先标记进场，再进入助理防抖
        if can_enter and not is_present:
            # 入场整理：收口离场历史（规则整理，不主动 LLM 摘要）
            # 与补种二选一：做过入场整理后，激活路径禁止再补种
            try:
                keep_ts = None
                all_msgs = self.context.conversation_ledger.get_all_messages(chat_id)
                if all_msgs:
                    keep_ts = all_msgs[-1].get("timestamp")
                self.context.conversation_ledger.organize_on_group_enter(
                    chat_id, keep_from_timestamp=keep_ts
                )
                if hasattr(event, "set_extra"):
                    event.set_extra("angelheart_group_enter_organized", True)
            except Exception as e:
                logger.warning(f"AngelHeart[{chat_id}]: 入场整理失败: {e}")

            await self.context.transition_to_status(
                chat_id,
                AngelHeartStatus.OBSERVATION,
                "离场唤醒，进入在场",
            )
            is_present = True

        ticket = await self.context.debounce_manager.schedule(
            chat_id=chat_id,
            event=event,
            sender_id=sender_id,
            message_id=message_id,
            is_wake=is_wake,
            is_present=is_present,
            leave_reply_trigger=leave_reply_trigger,
        )

        if ticket is None:
            # 只入库，不激活
            logger.debug(
                f"AngelHeart[{chat_id}]: 消息仅入库，不激活事件 "
                f"(wake={is_wake}, present={is_present}, sender={sender_id})"
            )
            event.stop_event()
            return

        result = await ticket
        if result == KILL:
            logger.debug(f"AngelHeart[{chat_id}]: 防抖旧事件被替换，停止当前事件")
            result_obj = event.get_result()
            if result_obj:
                result_obj.chain = []
            event.stop_event()
            return

        if result != PROCESS:
            logger.warning(f"AngelHeart[{chat_id}]: 未知防抖结果 '{result}'，停止事件")
            event.stop_event()
            return

        # 激活：重建上下文后进入秘书/主脑
        try:
            await self._activate_group_event(event)
        except Exception:
            await self._finish_secretary_dispatch(
                event,
                chat_id,
                cooldown_seconds=0.0,
                reason="activation_error",
            )
            raise

    async def _activate_group_event(self, event: AstrMessageEvent):
        """防抖到期后激活事件：重建上下文，再请求。"""
        chat_id = event.unified_msg_origin
        logger.debug(
            f"AngelHeart[{chat_id}]: 巡检放行后激活 "
            f"(kind={self.context.debounce_manager.get_debounce_kind(event)}, "
            f"must_reply={self.context.debounce_manager.get_must_reply(event)})"
        )

        # 激活时重建上下文
        await self._ensure_minimum_context(chat_id, event)

        # 登记工作账本：本事件对应一套活
        try:
            work_id = self._ensure_message_id(event)
            trigger_message_id = ""
            trigger_summary = ""
            try:
                if hasattr(event, "get_extra"):
                    trigger_message_id = self.context.debounce_manager.get_end_message_id(event)
            except Exception:
                trigger_message_id = ""
            if not trigger_message_id:
                trigger_message_id = work_id
            try:
                trigger_summary = (event.get_message_outline() or "").strip()
            except Exception:
                trigger_summary = ""
            if len(trigger_summary) > 80:
                trigger_summary = trigger_summary[:80] + "…"
            kind = self.context.debounce_manager.get_debounce_kind(event) or "assistant"
            self.context.work_ledger.start_work(
                chat_id=chat_id,
                work_id=work_id,
                trigger_message_id=trigger_message_id,
                trigger_summary=trigger_summary or "群聊应答",
                kind=kind,
            )
            if hasattr(event, "set_extra"):
                event.set_extra("angelheart_work_id", work_id)
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 登记工作账本失败: {e}")

        # 一事件一子代理：激活后直接进入秘书决策。
        # 不再用旧单槽扣押队列收集消息；并发由多个被放行事件自然形成。
        await self._call_secretary_and_execute(event, chat_id)

    def _schedule_private_compression(self, chat_id: str) -> None:
        """登记私聊摘要后台任务；同会话已有任务时不重复创建。"""
        current = self._private_compression_tasks.get(chat_id)
        if current and not current.done():
            return

        task = asyncio.create_task(self._maybe_private_llm_compress(chat_id))
        self._private_compression_tasks[chat_id] = task

        def _done(done_task: asyncio.Task) -> None:
            if self._private_compression_tasks.get(chat_id) is done_task:
                self._private_compression_tasks.pop(chat_id, None)
            try:
                done_task.result()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(
                    f"AngelHeart[{chat_id}]: 私聊摘要后台任务异常: {e}",
                    exc_info=True,
                )

        task.add_done_callback(_done)

    async def cleanup_background_tasks(self) -> None:
        """取消并等待前台创建的全部后台任务退出。"""
        tasks = list(self._private_compression_tasks.values())
        self._private_compression_tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _maybe_private_llm_compress(self, chat_id: str):
        """私聊主动 LLM 摘要压缩。"""
        ledger = self.context.conversation_ledger
        if not ledger._should_compress(chat_id):
            return

        analyzer_model = self.config_manager.analyzer_model
        if not analyzer_model:
            # 无分析模型时安全规则回退
            ledger.organize_context(chat_id, mode="private_fallback")
            return

        provider = None
        try:
            provider = self.astr_context.get_provider_by_id(analyzer_model)
        except Exception:
            provider = None
        if not provider:
            ledger.organize_context(chat_id, mode="private_fallback")
            return

        async def _text_chat(prompt: str) -> str:
            # 超时/取消遵循上游 Provider 配置；异常向外抛给 ledger，触发规则摘要回退。
            token = await provider.text_chat(prompt=prompt)
            return (token.completion_text or "").strip()

        await ledger.maybe_llm_compress_private(chat_id, _text_chat)

    async def _check_and_handle_timeout(self, chat_id: str, current_time: float):
        """检查并处理在场超时 → 离场"""
        try:
            current_status = self.context.get_chat_status(chat_id)
            if current_status not in (
                AngelHeartStatus.OBSERVATION,
                AngelHeartStatus.SUMMONED,
                AngelHeartStatus.GETTING_FAMILIAR,
            ):
                return

            status_start_time = (
                self.context.status_transition_manager.get_status_start_time(chat_id)
            )
            if status_start_time == 0:
                logger.warning(
                    f"AngelHeart[{chat_id}]: 状态缺少开始时间，跳过超时检查"
                )
                return

            timeout = self.config_manager.for_chat(chat_id).observation_timeout
            if current_time - status_start_time >= timeout:
                logger.info(
                    f"AngelHeart[{chat_id}]: 在场超时({timeout}秒)，转为离场"
                )
                await self.context.transition_to_status(
                    chat_id,
                    AngelHeartStatus.NOT_PRESENT,
                    f"在场超时({timeout}秒)自动离场",
                )
                await self.context.debounce_manager.clear_chat(
                    chat_id, reason="present_timeout"
                )

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 超时检查异常: {e}", exc_info=True)

    async def _finish_secretary_dispatch(
        self,
        event: AstrMessageEvent,
        chat_id: str,
        *,
        cooldown_seconds: float,
        reason: str,
    ) -> bool:
        """按本事件的调度归属收口同会话秘书单飞。

        秘书单飞只覆盖判断阶段；放行助理后应立即调用并令 cooldown_seconds=0。
        助理休息由 DebounceManager.start_assistant_rest 在主脑调用点单独启动。
        """
        dispatch_id = ""
        if hasattr(event, "get_extra"):
            dispatch_id = str(
                event.get_extra("angelheart_secretary_dispatch_id", "") or ""
            )
        if not dispatch_id:
            return False
        return await self.context.debounce_manager.finish_secretary_dispatch(
            chat_id,
            dispatch_id,
            cooldown_seconds=cooldown_seconds,
            reason=reason,
        )

    async def _call_secretary_and_execute(self, event: AstrMessageEvent, chat_id: str):
        """
        调用秘书并执行决策。

        群聊现行模型：
        - 防抖放行后的事件进入秘书决策
        - 同会话秘书单飞只保护“判断是否接话”
        - 秘书放行后立即释放单飞；助理生成/发送不再占用
        - 不能把后到消息注入已运行子代理
        """
        try:
            leave_reply_trigger = self.context.debounce_manager.get_leave_reply_trigger(event)
            if leave_reply_trigger:
                decision = await self.fishing_reply.generate_reply_strategy(
                    chat_id, event, leave_reply_trigger
                )
            else:
                decision = await self.secretary.handle_message_by_state(event)

            if decision and decision.should_reply:
                logger.info(
                    f"AngelHeart[{chat_id}]: 秘书决策 action=reply "
                    f"reason={decision.reply_strategy or '未说明'}"
                )
                self._record_last_decision(chat_id, True, decision.reply_strategy)
                await self._execute_secretary_decision(decision, event, chat_id)
                return

            if decision:
                logger.info(
                    f"AngelHeart[{chat_id}]: 秘书决策 action=no_reply "
                    f"reason={decision.reply_strategy or '未说明'}"
                )
                self._record_last_decision(chat_id, False, decision.reply_strategy)
            else:
                logger.warning(
                    f"AngelHeart[{chat_id}]: 秘书决策 action=no_decision reason=analysis_failed"
                )
                self._record_last_decision(chat_id, False, "分析失败")

            if (
                event.is_at_or_wake_command
                and self.context.config_manager.for_chat(chat_id).block_unapproved_wake_non_command
            ):
                logger.debug(
                    f"AngelHeart[{chat_id}]: 上游唤醒聊天事件未获批准，已停止后续主 LLM 处理。"
                )
                event.stop_event()

            # 不回复：关闭本轮工作，避免账本一直 running
            try:
                work_id = ""
                if hasattr(event, "get_extra"):
                    work_id = str(event.get_extra("angelheart_work_id", "") or "")
                if not work_id:
                    work_id = self._get_event_message_id(event)
                reason = decision.reply_strategy if decision else "分析失败"
                self.context.work_ledger.complete_work(
                    chat_id,
                    work_id,
                    status="done",
                    result_summary=f"不回复：{reason}",
                )
            except Exception:
                pass

            await self._finish_secretary_dispatch(
                event,
                chat_id,
                cooldown_seconds=0.0,
                reason="no_reply" if decision else "no_decision",
            )

            # 不回复时停止事件，避免继续进入主脑
            event.stop_event()
        except Exception as e:
            message_id = self._get_event_message_id(event)
            logger.error(
                f"AngelHeart[{chat_id}]: 调用秘书异常 (message_id={message_id}): {e}",
                exc_info=True,
            )
            try:
                self.context.work_ledger.complete_work(
                    chat_id,
                    message_id,
                    status="failed",
                    result_summary="秘书处理异常",
                )
            except Exception:
                pass
            await self._finish_secretary_dispatch(
                event,
                chat_id,
                cooldown_seconds=0.0,
                reason="secretary_error",
            )
            # 秘书兜底：秘书链路异常时放行，让主脑无脑处理，而不是 stop 导致不回复。
            # 秘书是"拦"的角色；秘书死了，助理应该照常干活，不能陪葬。
            if not self.config_manager.for_chat(chat_id).debug_mode:
                event.is_at_or_wake_command = True
                logger.debug(
                    f"AngelHeart[{chat_id}]: 秘书异常，放行主脑无脑处理"
                )

    def _record_last_decision(self, chat_id: str, should_reply: bool, summary: str) -> None:
        """记录该群最近一次秘书决策（供 WebUI 状态栏）；store 未注入或失败只 debug 不打断。"""
        if self.last_decisions is None:
            return
        try:
            self.last_decisions.record(chat_id, should_reply, summary)
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 记录最近决策失败: {e}")

    async def _execute_secretary_decision(
        self, decision, event: AstrMessageEvent, chat_id: str
    ):
        """
        执行秘书的决策

        Args:
            decision: 秘书的决策对象
            event: 消息事件
            chat_id: 会话ID
        """
        try:
            frozen_context = self._get_decision_context_for_rewrite(chat_id, event)
            if not frozen_context:
                raise RuntimeError("秘书决策上下文缺失")
            recent_dialogue, historical_context, boundary_ts = frozen_context

            await self._process_decision_result(
                decision,
                recent_dialogue,
                historical_context,
                boundary_ts,
                event,
                chat_id,
            )
        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 执行秘书决策异常: {e}", exc_info=True)
            raise

    async def _process_decision_result(
        self, decision, recent_dialogue, historical_context, boundary_ts, event, chat_id
    ):
        """处理决策结果 - 复用秘书的逻辑"""
        if decision and decision.should_reply:
            logger.debug(
                f"AngelHeart[{chat_id}]: 执行秘书决策 action=reply "
                f"reason={decision.reply_strategy or '未说明'}"
            )

            # 旁路上下文：聊天记录 + 决策 挂到本事件，供日志/下游钩子读
            # 不写会话共享缓存；主脑 req 临时注入仍只留工作账本
            from ..core.utils import json_serialize_context

            full_snapshot = historical_context + recent_dialogue
            try:
                event.angelheart_context = json_serialize_context(
                    full_snapshot, decision
                )
                logger.debug(
                    f"AngelHeart[{chat_id}]: 上下文已注入 event.angelheart_context"
                )
            except Exception as e:
                logger.error(f"AngelHeart[{chat_id}]: 注入上下文失败: {e}")
                event.angelheart_context = json.dumps(
                    {
                        "chat_records": [],
                        "secretary_decision": {
                            "should_reply": False,
                            "error": "注入失败",
                        },
                        "error": "注入失败",
                    },
                    ensure_ascii=False,
                )

            # 决策门闩：要回就唤醒主脑
            try:
                if hasattr(event, "set_extra"):
                    event.set_extra("angelheart_assistant_invoked", True)
                await self.context.debounce_manager.start_assistant_rest(
                    chat_id,
                    self.config_manager.for_chat(chat_id).waiting_time,
                    reason="assistant_invoked",
                )
            except Exception as e:
                logger.warning(f"AngelHeart[{chat_id}]: 启动助理休息失败: {e}")

            if not self.config_manager.for_chat(chat_id).debug_mode:
                event.is_at_or_wake_command = True
                logger.debug(f"AngelHeart[{chat_id}]: 已设置唤醒主脑标志")
                # 秘书判断结束即释放单飞；助理生成/发送不再占用。
                await self._finish_secretary_dispatch(
                    event,
                    chat_id,
                    cooldown_seconds=0.0,
                    reason="reply_handoff",
                )
            else:
                logger.debug(f"AngelHeart[{chat_id}]: 调试模式已启用，阻止了实际唤醒。")
                try:
                    work_id = ""
                    if hasattr(event, "get_extra"):
                        work_id = str(event.get_extra("angelheart_work_id", "") or "")
                    if not work_id:
                        work_id = self._get_event_message_id(event)
                    self.context.work_ledger.complete_work(
                        chat_id,
                        work_id,
                        status="done",
                        result_summary="debug跳过发送",
                    )
                except Exception:
                    pass
                await self._finish_secretary_dispatch(
                    event,
                    chat_id,
                    cooldown_seconds=0.0,
                    reason="debug_skip_send",
                )
            # 需要回复时，由主框架继续处理该事件（一事件一子代理）

    async def _ensure_minimum_context(self, chat_id: str, event: AstrMessageEvent):
        """
        冷启动补种：连续块过短时从历史库补充。

        与入场整理二选一：
        - 已有 current_summary / 本事件做过入场整理 → 禁止补种（补种=整理的一种，互斥）
        - 仅真空/冷启动场景才补种
        """
        try:
            ledger = self.context.conversation_ledger

            # 入场整理与补种互斥
            try:
                if hasattr(event, "get_extra") and event.get_extra(
                    "angelheart_group_enter_organized", False
                ):
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 本事件已入场整理，跳过补种"
                    )
                    return
            except Exception:
                pass
            try:
                if str(ledger.get_current_summary(chat_id) or "").strip():
                    logger.debug(
                        f"AngelHeart[{chat_id}]: 已有当前摘要，跳过补种（与整理互斥）"
                    )
                    return
            except Exception:
                pass

            current_messages = ledger.get_all_messages(chat_id)

            # 统计总消息数（包括图片等无文本消息）
            total_messages = len(current_messages)
            text_messages = [
                msg for msg in current_messages if self._has_text_content(msg)
            ]

            # 基于总消息数判断是否需要补充（不只是文本消息）
            if total_messages >= 7:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 消息数量充足({total_messages} >= 7)，无需补充"
                )
                return

            # 固定获取19条历史消息（除了最新那条）
            logger.debug(
                f"AngelHeart[{chat_id}]: 当前有 {len(text_messages)} 条消息，开始获取历史消息"
            )
            supplement_messages = await self._fetch_database_history(chat_id, 19, event)

            if supplement_messages:
                # 合并历史消息和当前内存消息；按时间戳去重，避免补历史时清空已有上下文。
                # is_processed 已退役：冷启动补种直接进入当前连续块。
                messages_by_timestamp = {}
                for msg in supplement_messages + current_messages:
                    msg.pop("is_processed", None)
                    messages_by_timestamp[msg.get("timestamp", 0)] = msg

                all_messages = sorted(
                    messages_by_timestamp.values(), key=lambda m: m.get("timestamp", 0)
                )

                # 使用公共方法更新消息列表
                ledger.set_messages(chat_id, all_messages)

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 补充历史消息失败: {e}")

    def _has_text_content(self, message: Dict) -> bool:
        """检查消息是否包含文本内容"""
        content = message.get("content", "")
        if isinstance(content, str):
            return bool(content.strip())
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if text.strip():
                        return True
        return False

    async def _fetch_database_history(
        self, chat_id: str, needed_count: int, event: AstrMessageEvent
    ) -> List[Dict]:
        """
        优先从 QQ API 获取历史消息；若失败或为空，则回退到 AstrBot 官方会话历史。
        """
        try:
            if not self._is_group_chat(chat_id):
                logger.debug(
                    f"AngelHeart[{chat_id}]: 非群聊会话，直接从 AstrBot 官方会话历史补充"
                )
                return await self._fetch_astrbot_conversation_history(chat_id, needed_count)

            converted_messages = await self._fetch_qq_history(chat_id, needed_count, event)
            if converted_messages:
                return converted_messages

            logger.debug(
                f"AngelHeart[{chat_id}]: QQ 历史获取为空或失败，回退到 AstrBot 官方会话历史"
            )
            return await self._fetch_astrbot_conversation_history(chat_id, needed_count)

        except Exception as e:
            logger.error(
                f"AngelHeart[{chat_id}]: 获取QQ API历史失败: {e}", exc_info=True
            )
            return []

    async def _fetch_qq_history(
        self, chat_id: str, needed_count: int, event: AstrMessageEvent
    ) -> List[Dict]:
        """从 QQ API 获取历史消息并转换为 AngelHeart 格式。"""
        # 解析群号
        group_id = self._extract_group_id(chat_id)

        # 获取bot实例
        bot = self._get_bot_instance(event)
        if not bot:
            logger.error(f"AngelHeart[{chat_id}]: 无法获取bot实例")
            return []

        raw_messages = await self._get_qq_history_direct(bot, group_id, needed_count)

        converted_messages = []
        for raw_msg in raw_messages:
            msg = self._convert_raw_qq_message_to_angelheart_format(raw_msg)
            if msg:
                converted_messages.append(msg)
        return converted_messages

    async def _fetch_astrbot_conversation_history(
        self, chat_id: str, needed_count: int
    ) -> List[Dict]:
        """从 AstrBot 官方 conversation history 回退补充历史消息。"""
        try:
            conv_mgr = getattr(self.astr_context, "conversation_manager", None)
            if not conv_mgr:
                logger.warning(f"AngelHeart[{chat_id}]: AstrBot conversation_manager 不可用")
                return []

            conversation_id = await conv_mgr.get_curr_conversation_id(chat_id)
            if not conversation_id:
                logger.debug(f"AngelHeart[{chat_id}]: 当前会话没有官方 conversation id")
                return []

            conversation = await conv_mgr.get_conversation(chat_id, conversation_id)
            if not conversation or not getattr(conversation, "history", None):
                logger.debug(f"AngelHeart[{chat_id}]: 官方 conversation history 为空")
                return []

            try:
                history_records = json.loads(conversation.history)
            except Exception as e:
                logger.warning(f"AngelHeart[{chat_id}]: 解析官方 conversation history 失败: {e}")
                return []

            if not isinstance(history_records, list) or not history_records:
                return []

            fallback_messages = self._convert_astrbot_history_to_angelheart_format(
                history_records,
                needed_count,
            )
            logger.debug(
                f"AngelHeart[{chat_id}]: 已从 AstrBot 官方会话历史回退补充 {len(fallback_messages)} 条消息"
            )
            return fallback_messages
        except Exception as e:
            logger.error(
                f"AngelHeart[{chat_id}]: 回退 AstrBot 官方会话历史失败: {e}",
                exc_info=True,
            )
            return []

    def _convert_astrbot_history_to_angelheart_format(
        self, history_records: List[Dict], needed_count: int
    ) -> List[Dict]:
        """将 AstrBot 官方 conversation history 转为 AngelHeart 内部消息格式。"""
        selected_records = []
        used_tokens = 0
        message_limit = min(needed_count, self.ASTRBOT_HISTORY_MESSAGE_LIMIT)

        for record in reversed(history_records):
            if not isinstance(record, dict):
                continue

            role = record.get("role")
            if role not in ("user", "assistant", "tool"):
                continue

            content = self._extract_text_from_astrbot_history_record(record)
            content_tokens = self._estimate_text_tokens(content)
            if used_tokens + content_tokens > self.ASTRBOT_HISTORY_TEXT_TOKEN_LIMIT:
                break

            normalized_record = self._convert_astrbot_history_record(record)
            if normalized_record is None:
                continue

            selected_records.append(normalized_record)
            used_tokens += content_tokens

            if len(selected_records) >= message_limit:
                break

        selected_records.reverse()

        base_timestamp = time.time() - max(len(selected_records), 1)
        for index, message in enumerate(selected_records):
            message["timestamp"] = base_timestamp + index

        return selected_records

    def _convert_astrbot_history_record(self, record: Dict) -> Dict | None:
        role = record.get("role")
        if role == "user":
            content = self._extract_text_from_astrbot_history_record(record)
            if not content:
                return None
            return {
                "role": "user",
                "content": content,
                "sender_id": "history_user",
                "sender_name": "user",
                "source": "astrbot_conversation",
            }

        if role == "assistant":
            content = record.get("content", "")
            tool_calls = record.get("tool_calls")
            if content is None and not tool_calls:
                return None

            normalized_content = serialize_content_parts(content)
            if isinstance(normalized_content, list) and not normalized_content:
                normalized_content = []
            elif normalized_content is None:
                normalized_content = [] if tool_calls else ""

            converted = {
                "role": "assistant",
                "content": normalized_content,
                "sender_id": "assistant",
                "sender_name": "assistant",
                "source": "astrbot_conversation",
            }
            if tool_calls:
                converted["tool_calls"] = serialize_content_parts(tool_calls)
                converted["is_structured_toolcall"] = True
            return converted

        if role == "tool":
            content = record.get("content", "")
            normalized_content = serialize_content_parts(content)
            if normalized_content is None:
                normalized_content = ""
            return {
                "role": "tool",
                "content": normalized_content,
                "tool_call_id": record.get("tool_call_id"),
                "sender_id": "tool",
                "sender_name": "tool_result",
                "source": "astrbot_conversation",
                "is_structured_toolcall": True,
            }

        return None

    def _estimate_text_tokens(self, text: str) -> int:
        """粗略估算文本 token 数，与总账压缩估算保持同一量级。"""
        if not isinstance(text, str) or not text:
            return 0
        chinese_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        other_chars = len(text) - chinese_chars
        return int(chinese_chars * 0.6 + other_chars * 0.3)

    def _extract_text_from_astrbot_history_record(self, record: Dict) -> str:
        """从 AstrBot 官方 history record 中提取可用文本。"""
        content = record.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text = item.get("text") or item.get("content") or ""
                        if isinstance(text, str) and text.strip():
                            text_parts.append(text.strip())
                    elif isinstance(item.get("text"), str) and item.get("text").strip():
                        text_parts.append(item.get("text").strip())
                elif isinstance(item, str) and item.strip():
                    text_parts.append(item.strip())
            return " ".join(text_parts).strip()
        return ""

    async def _get_qq_history_direct(
        self, bot, group_id: str, count: int
    ) -> List[Dict]:
        """
        直接调用QQ API获取历史消息
        参考天使之眼的实现
        """
        try:
            # 调用get_group_msg_history API
            payloads = {
                "group_id": int(group_id),
                "message_seq": 0,  # 从最新开始
                "reverseOrder": True,  # 倒序获取（但实际返回仍是正序）
            }
            result = await bot.api.call_action("get_group_msg_history", **payloads)

            if not result or "messages" not in result:
                logger.warning(f"AngelHeart: API返回无效结果: {result}")
                return []

            messages = result.get("messages", [])
            logger.debug(f"AngelHeart: QQ API返回 {len(messages)} 条消息")

            # 返回所有消息，但去掉最新的一条（避免与当前消息重复）
            if len(messages) > 1:
                return messages[:-1]  # 去掉最新的一条
            else:
                return []

        except Exception as e:
            logger.debug(f"AngelHeart: 首次补历史调用QQ API失败（不影响主流程）: {e}")
            return []

    def _extract_group_id(self, chat_id: str) -> str:
        """从chat_id中提取群号"""
        # chat_id格式通常是 "default:GroupMessage:群号"
        parts = chat_id.split(":")
        return parts[-1] if len(parts) >= 3 else chat_id

    def _get_bot_instance(self, event: AstrMessageEvent):
        """从事件对象获取bot实例"""
        try:
            # 参考天使之眼的方式：从event.bot获取
            if hasattr(event, "bot"):
                return event.bot
            else:
                logger.error("AngelHeart: event对象中没有bot实例")
                return None
        except Exception as e:
            logger.error(f"AngelHeart: 获取bot实例失败: {e}")
            return None

    def _convert_raw_qq_message_to_angelheart_format(self, raw_msg: Dict) -> Dict:
        """
        将QQ API返回的原始消息转换为天使之心格式
        完全参考天使之眼的format_unified_message逻辑
        """
        try:
            # 1. 获取发送者信息（天使之眼的方式）
            sender = raw_msg.get("sender", {})
            sender_id = str(sender.get("user_id", ""))

            # 2. 判断是否为机器人自己发送的消息
            # 直接对比 sender.user_id 和 self_id
            self_id = str(raw_msg.get("self_id", ""))
            is_self = str(sender_id) == self_id
            role = "assistant" if is_self else "user"
            sender_name = self._normalize_sender_name(
                sender_id,
                sender.get("card"),
                sender.get("nickname"),
            )
            if role == "assistant" and sender_name == self.BLANK_SENDER_NAME:
                sender_name = "assistant"

            # 3. 提取消息内容（只取文本，忽略图片等）
            content = self._extract_text_from_qq_message(raw_msg)

            if not content.strip():
                return None

            # 4. 获取时间戳
            timestamp = raw_msg.get("time", time.time())

            return {
                "role": role,
                "content": content,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "source_message_id": str(raw_msg.get("message_id", "") or ""),
                "timestamp": timestamp,
                "source": "qq_api",
            }

        except Exception as e:
            logger.warning(f"转换QQ消息格式失败: {e}")
            return None

    def _extract_text_from_qq_message(self, raw_msg: Dict) -> str:
        """
        从QQ API返回的原始消息中提取纯文本
        message字段是一个数组，每个元素有type和data
        """
        try:
            message_chain = raw_msg.get("message", [])
            if not isinstance(message_chain, list):
                return str(message_chain)

            text_parts = []
            for component in message_chain:
                if isinstance(component, dict):
                    comp_type = component.get("type", "")
                    data = component.get("data", {})

                    # 只处理文本组件
                    if comp_type == "text":
                        text_content = data.get("text", "")
                        if text_content:
                            text_parts.append(text_content)

            return "".join(text_parts).strip()

        except Exception as e:
            logger.warning(f"提取QQ消息文本失败: {e}")
            return ""

    @property
    def config_manager(self):
        return self._config_manager

    def filter_images_for_provider(
        self, chat_id: str, contexts: List[Dict]
    ) -> List[Dict]:
        """
        根据 Provider 的 modalities 配置过滤图片内容

        Args:
            chat_id: 聊天ID，用于获取当前使用的 provider
            contexts: 消息上下文列表

        Returns:
            过滤后的消息上下文列表
        """
        try:
            # 获取当前使用的 provider
            provider = self.context.astr_context.get_using_provider(chat_id)
            if not provider:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 无法获取当前 provider，跳过图片过滤"
                )
                return contexts

            # 检查 provider 的 modalities 配置
            provider_config = provider.provider_config
            modalities = provider_config.get("modalities", None)

            if not modalities or not isinstance(modalities, list):
                logger.debug(
                    f"AngelHeart[{chat_id}]: Provider {provider_config.get('id', 'unknown')} 未声明 modalities，按兼容策略保留图片"
                )
                return contexts

            # 如果支持图片，直接返回
            if "image" in modalities:
                logger.debug(
                    f"AngelHeart[{chat_id}]: Provider {provider_config.get('id', 'unknown')} 支持图片，无需过滤"
                )
                return contexts

            # 不支持图片，需要过滤
            logger.debug(
                f"AngelHeart[{chat_id}]: Provider {provider_config.get('id', 'unknown')} 不支持图片，开始过滤图片内容"
            )

            filtered_contexts = []
            images_filtered_count = 0

            for msg in contexts:
                filtered_msg = copy.deepcopy(msg)  # 深拷贝避免修改原始数据

                if msg.get("role") == "user" and isinstance(
                    filtered_msg.get("content"), list
                ):
                    original_content = filtered_msg["content"]
                    filtered_content = []
                    has_image = False

                    for item in original_content:
                        # 只处理字典类型的组件，保留 Pydantic 模型对象（如 ThinkPart）
                        if isinstance(item, dict) and item.get("type") == "image_url":
                            has_image = True
                            images_filtered_count += 1
                            # 静默移除图片，不添加任何提示
                        else:
                            # 保留非图片的所有组件（文本、ThinkPart、文件等）
                            filtered_content.append(item)

                    filtered_msg["content"] = filtered_content

                    if has_image:
                        logger.debug(
                            f"AngelHeart[{chat_id}]: 已过滤用户消息中的图片内容"
                        )

                elif msg.get("role") == "assistant":
                    # assistant 历史必须保留 think / text / tool_calls 等结构，只移除图片。
                    content = filtered_msg.get("content", [])
                    if isinstance(content, list):
                        filtered_content = []
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "image_url":
                                images_filtered_count += 1
                                continue
                            filtered_content.append(item)
                        filtered_msg["content"] = filtered_content

                filtered_contexts.append(filtered_msg)

            if images_filtered_count > 0:
                logger.debug(
                    f"AngelHeart[{chat_id}]: 总共过滤了 {images_filtered_count} 个图片组件"
                )

            return filtered_contexts

        except Exception as e:
            logger.error(
                f"AngelHeart[{chat_id}]: 图片过滤时发生错误: {e}", exc_info=True
            )
            # 出错时返回原始上下文，避免破坏流程
            return contexts

    def _is_group_chat(self, chat_id: str) -> bool:
        """根据 unified_msg_origin 判断是否为群聊。"""
        parts = chat_id.split(":")
        return len(parts) >= 3 and parts[1] == "GroupMessage"

    def _is_private_chat(self, chat_id: str) -> bool:
        """根据 unified_msg_origin 判断是否为私聊。"""
        parts = chat_id.split(":")
        return len(parts) >= 3 and parts[1] == "FriendMessage"

    def _get_conversation_data_from_ledger(
        self, chat_id: str, boundary_message_id: str = ""
    ):
        """从 ConversationLedger 取得截至边界消息的历史重写数据。"""
        historical_context, recent_dialogue, boundary_ts = partition_dialogue_raw(
            self.context.conversation_ledger, chat_id, boundary_message_id
        )
        return recent_dialogue, historical_context, boundary_ts

    def _get_decision_context_for_rewrite(
        self, chat_id: str, event: AstrMessageEvent
    ):
        """
        主脑重写用的上下文：群聊必须等于秘书判断点，不得二次全量扩窗。

        Returns:
            (recent_dialogue, historical_context, boundary_ts) 或 None（群聊无固化切片时）
        """
        if self._is_group_chat(chat_id):
            frozen = None
            try:
                if hasattr(event, "get_extra"):
                    frozen = event.get_extra("angelheart_decision_context", None)
            except Exception:
                frozen = None
            if not isinstance(frozen, dict):
                logger.debug(
                    f"AngelHeart[{chat_id}]: 群聊无固化决策上下文，跳过重写以免扩窗"
                )
                return None
            recent = frozen.get("recent_dialogue") or []
            historical = frozen.get("historical_context") or []
            boundary_ts = frozen.get("boundary_ts", 0.0)
            if not recent and not historical:
                return None
            return recent, historical, boundary_ts

        # 私聊：无秘书切片，按当前入站消息 ID 从账本截断。
        return self._get_conversation_data_from_ledger(
            chat_id, self._get_event_message_id(event)
        )

    def _split_recent_dialogue_at_current_message(
        self, recent_dialogue: List[Dict], current_message_id: str
    ) -> tuple[List[Dict], List[Dict]]:
        """按当前上游消息 ID 拆分上下文与本轮 prompt，当前消息绝不进入 contexts。"""
        if not current_message_id:
            return list(recent_dialogue), []

        context_messages = []
        current_messages = []
        for message in recent_dialogue:
            source_message_id = str(message.get("source_message_id", "") or "")
            if source_message_id == current_message_id:
                current_messages.append(message)
            else:
                context_messages.append(message)
        return context_messages, current_messages

    def _generate_final_prompt(
        self, recent_dialogue: List[Dict], decision: Any, alias: str
    ) -> str:
        """生成聚焦指令"""
        return format_final_prompt(recent_dialogue, decision, alias, use_absolute_time=True)

    def _is_valid_final_prompt(self, prompt: str) -> bool:
        """检查重建后的当前提示词是否有有效内容。"""
        return isinstance(prompt, str) and bool(prompt.strip())

    def _parse_space_separated_phrases(self, raw: Any) -> List[str]:
        """解析空格分隔短语，去掉空项并去重保序。"""
        return parse_space_phrases(raw)

    def _extract_message_plain_text(self, message: Dict[str, Any] | None) -> str:
        """兼容旧调用：优先 metadata.body_text，否则退回 content 纯文本。"""
        if not isinstance(message, dict):
            return ""
        metadata = message.get("metadata")
        if isinstance(metadata, dict):
            body_text = metadata.get("body_text")
            if isinstance(body_text, str):
                return body_text
        return convert_content_to_string(message.get("content", ""))

    def _message_has_focus_hit(self, message: Dict[str, Any] | None, chat_id: str = "") -> bool:
        """读取入库时写好的焦点命中。"""
        if not isinstance(message, dict):
            return False
        metadata = message.get("metadata")
        if isinstance(metadata, dict) and "hits" in metadata:
            return metadata_has_hit(metadata, "focus")
        # 兼容旧消息：只回退到 body_text / 纯 content，不再扫昵称字段
        text = self._extract_message_plain_text(message)
        phrases = self._parse_space_separated_phrases(
            getattr(self.config_manager.for_chat(chat_id), "focus_instructions", "")
        )
        if not text or not phrases:
            return False
        normalized = text.casefold()
        return any(phrase.casefold() in normalized for phrase in phrases)

    def _matches_focus_instructions(self, texts: List[str]) -> bool:
        """兼容旧接口：对给定文本列表做焦点匹配。"""
        phrases = self._parse_space_separated_phrases(
            getattr(self.config_manager, "focus_instructions", "")
        )
        if not phrases:
            return False
        normalized_phrases = [phrase.casefold() for phrase in phrases]
        for text in texts:
            normalized = str(text or "").casefold()
            if not normalized:
                continue
            if any(phrase in normalized for phrase in normalized_phrases):
                return True
        return False

    def _build_reply_length_reminder(self, focus: bool, chat_id: str = "") -> str:
        """构建常规/焦点字数系统提醒。"""
        cm = self.config_manager.for_chat(chat_id)
        normal_limit = int(getattr(cm, "normal_reply_max_chars", 20) or 20)
        focus_limit = int(getattr(cm, "focus_reply_max_chars", 200) or 200)
        normal_limit = max(1, normal_limit)
        focus_limit = max(normal_limit, focus_limit)
        if focus:
            return (
                f"请认真回答：先给结论，再给必要依据，长度以 {focus_limit} 字左右为宜。"
                "确实需要时可以超出，重点是讲清楚，不要注水、不要重复。"
                "如果是分析的，不要正反面讲解，直接给出你认为的最佳结论，只给出必要的关键推理。"
            )
        return (
            f"回复尽量简短，通常一两句话、{normal_limit} 字左右即可说清。"
            "但如果问题复杂度高、三言两语说不清，或者用户明确要求多说一些，"
            "可以适度超出，以讲清楚为准。"
            "不要正反面讲解，直接给出你认为的最佳结论，不需要推理过程。"
        )

    def _build_temporary_reply_length_context(
        self,
        chat_id: str,
        recent_dialogue: List[Dict],
        prompt_recent_dialogue: List[Dict],
    ) -> Dict[str, Any] | None:
        """群聊回复时注入字数提醒；私聊不注入。"""
        if not self._is_group_chat(chat_id):
            return None

        candidate_messages = list(recent_dialogue or []) + list(prompt_recent_dialogue or [])
        focus = False
        for message in candidate_messages:
            if not isinstance(message, dict):
                continue
            if str(message.get("role", "") or "").lower() == "assistant":
                continue
            if self._message_has_focus_hit(message, chat_id):
                focus = True
                break

        reminder = self._build_reply_length_reminder(focus, chat_id)
        return {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"<system_reminder>\n{reminder}\n</system_reminder>",
                }
            ],
            "sender_id": "angelheart-reply-length",
            "sender_name": "回复长度",
            "timestamp": time.time(),
            "_no_save": True,
            "is_temporary_context": True,
            "chat_id": chat_id,
            "angelheart_focus": focus,
        }

    def _build_temporary_work_ledger_context(
        self, chat_id: str, event: AstrMessageEvent | None = None
    ) -> Dict[str, Any] | None:
        """构建不保存的工作账本临时提醒（第二人称）。"""
        try:
            work_id = ""
            if event is not None and hasattr(event, "get_extra"):
                work_id = str(event.get_extra("angelheart_work_id", "") or "")
            if not work_id and event is not None:
                work_id = self._get_event_message_id(event)
            text = self.context.work_ledger.format_for_assistant(chat_id, current_work_id=work_id)
        except Exception as e:
            logger.warning(f"AngelHeart[{chat_id}]: 构建工作账本提醒失败: {e}")
            return None

        if not text or not text.strip():
            return None

        return {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"<system_reminder>\n{text.strip()}\n</system_reminder>",
                }
            ],
            "sender_id": "angelheart-work-ledger",
            "sender_name": "工作账本",
            "timestamp": time.time(),
            "_no_save": True,
            "is_temporary_context": True,
            "chat_id": chat_id,
        }

    def _mark_processed_if_needed(
        self,
        chat_id: str,
        recent_dialogue: List[Dict],
        should_mark_processed: bool,
    ):
        """兼容旧接口：is_processed 已退役，空操作。"""
        return

    def _provider_supports_images(self, chat_id: str) -> bool:
        """判断当前会话使用的主模型是否支持图片输入。"""
        try:
            provider = self.astr_context.get_using_provider(chat_id)
            if not provider:
                return False
            provider_config = getattr(provider, "provider_config", {}) or {}
            modalities = provider_config.get("modalities", None)
            if not modalities or not isinstance(modalities, list):
                return True
            return "image" in modalities
        except Exception as e:
            logger.debug(f"AngelHeart[{chat_id}]: 判断 Provider 图片能力失败: {e}")
            return False

    def _should_preserve_current_image_urls(self, chat_id: str) -> bool:
        """主模型支持图片时，当前事件图片保持 AstrBot 原生传递。"""
        return self._provider_supports_images(chat_id)

    def _build_contexts_with_processor(
        self,
        processor: 'MessageProcessor',
        historical_context: List[Dict],
        recent_dialogue: List[Dict],
        chat_id: str,
        current_message_id: str,
        scene_hint: str | None = None,
    ) -> List[Dict]:
        """使用 MessageProcessor 构建上下文列表"""
        new_contexts = []
        if scene_hint:
            # 在最顶部添加场景说明消息，避免某些模型不允许第一条消息是助理
            new_contexts.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": scene_hint}]
                }
            )

        # 1) 历史消息
        for msg in historical_context:
            processed_msg = processor.process_message(msg)
            new_contexts.append(processed_msg)

        # 2) 最新消息（按当前消息 ID 过滤，避免与 req.prompt 对应的新消息重复）
        for msg in recent_dialogue:
            if current_message_id and str(msg.get("source_message_id", "") or "") == current_message_id:
                continue
            processed_msg = processor.process_message(msg)
            new_contexts.append(processed_msg)

        return new_contexts

    def _collect_non_current_image_urls(
        self, recent_dialogue: List[Dict], current_message_id: str
    ) -> List[str]:
        """收集阻塞聚合中非当前消息的图片。"""
        return self._collect_image_urls_by_message(
            recent_dialogue,
            current_message_id,
            include_current=False,
        )

    def _collect_current_image_urls(
        self, recent_dialogue: List[Dict], current_message_id: str
    ) -> List[str]:
        """收集当前事件中已落入插件缓存的图片路径，用于替换 req.image_urls。"""
        return self._collect_image_urls_by_message(
            recent_dialogue,
            current_message_id,
            include_current=True,
        )

    def _image_item_request_url(self, item: Dict) -> str:
        for key in ("cache_path", "local_file_path", "original_file_url", "original_url"):
            url = item.get(key)
            if isinstance(url, str) and url:
                return url

        image_url = item.get("image_url", {})
        if isinstance(image_url, dict):
            url = image_url.get("url", "")
            if isinstance(url, str):
                return url
        return ""

    def _collect_image_urls_by_message(
        self,
        recent_dialogue: List[Dict],
        current_message_id: str,
        include_current: bool,
    ) -> List[str]:
        if not current_message_id:
            return []

        image_urls = []
        seen = set()
        for msg in recent_dialogue:
            is_current = str(msg.get("source_message_id", "") or "") == current_message_id
            if include_current != is_current:
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "image_url":
                    continue
                url = self._image_item_request_url(item)
                if isinstance(url, str) and url and url not in seen:
                    image_urls.append(url)
                    seen.add(url)

        return image_urls

    def _append_extra_image_urls_to_request(self, req: Any, image_urls: List[str]):
        """把非当前消息的 ledger 图片追加为当前请求的额外多模态块。"""
        if not image_urls:
            return

        try:
            from astrbot.core.agent.message import ImageURLPart
        except Exception as e:
            logger.warning(f"AngelHeart: 无法导入 ImageURLPart，跳过聚合图片追加: {e}")
            return

        if not hasattr(req, "extra_user_content_parts") or req.extra_user_content_parts is None:
            req.extra_user_content_parts = []

        for url in image_urls:
            try:
                req.extra_user_content_parts.append(
                    ImageURLPart(image_url={"url": url})
                )
            except Exception as e:
                logger.warning(f"AngelHeart: 追加聚合图片失败: {e}")

    def _replace_current_image_attachment_paths(
        self, req: Any, current_image_urls: List[str]
    ):
        """把 AstrBot 当前消息图片附件文本中的临时 path 替换为账本缓存 path。"""
        if not current_image_urls:
            return
        parts = getattr(req, "extra_user_content_parts", None)
        if not isinstance(parts, list):
            return

        ref_index = 0
        pattern = re.compile(r"^\[Image Attachment: path .+\]$")
        for part in parts:
            if ref_index >= len(current_image_urls):
                break

            text = ""
            if isinstance(part, dict):
                text = part.get("text", "") if part.get("type") == "text" else ""
            else:
                text = getattr(part, "text", "")

            if not isinstance(text, str) or not pattern.match(text):
                continue

            replacement = f"[Image Attachment: path {current_image_urls[ref_index]}]"
            if isinstance(part, dict):
                part["text"] = replacement
            else:
                try:
                    part.text = replacement
                except Exception as e:
                    logger.warning(f"AngelHeart: 替换当前图片附件路径失败: {e}")
                    continue
            ref_index += 1

    def _update_request(
        self,
        req: Any,
        contexts: List[Dict],
        final_prompt: str,
        alias: str,
        scene_prompt: str | None = None,
        preserve_current_image_urls: bool = False,
        current_image_urls: List[str] | None = None,
        extra_image_urls: List[str] | None = None,
    ):
        """更新请求对象"""
        # 完全覆盖原有的 contexts
        req.contexts = contexts

        # 只在当前提示词有效时覆盖 req.prompt；否则保留原始当前轮输入。
        if self._is_valid_final_prompt(final_prompt):
            req.prompt = final_prompt
            if preserve_current_image_urls:
                if current_image_urls:
                    req.image_urls = current_image_urls
                self._append_extra_image_urls_to_request(req, extra_image_urls or [])
            else:
                req.image_urls = []
            self._replace_current_image_attachment_paths(req, current_image_urls or [])

        # 注入系统提示词
        original_system_prompt = getattr(req, "system_prompt", "")
        if scene_prompt:
            req.system_prompt = (
                f"{original_system_prompt}\n\n{scene_prompt.format(alias=alias)}"
            )
        else:
            req.system_prompt = original_system_prompt

    async def rewrite_prompt_for_llm(self, chat_id: str, event: AstrMessageEvent, req: Any):
        """
        重构请求体，实现完整的对话历史格式化和指令注入。
        使用辅助方法和 MessageProcessor 类使逻辑更清晰。
        """
        logger.debug(f"AngelHeart[{chat_id}]: 开始重构LLM请求体...")

        alias = self.config_manager.for_chat(chat_id).alias
        current_message_id = self._get_event_message_id(event)
        should_mark_processed = False
        scene_hint = None
        scene_prompt = None
        preserve_current_image_urls = self._should_preserve_current_image_urls(chat_id)

        # 历史重写：
        # - 群聊：必须用秘书判断点固化的切片（秘书点=主脑点）
        # - 私聊：无秘书，从 ledger 读；可冷启动补种
        # 助理临时注入只留工作账本。
        if self._is_private_chat(chat_id):
            await self._ensure_minimum_context(chat_id, event)

        conv = self._get_decision_context_for_rewrite(chat_id, event)
        if not conv:
            logger.debug(f"AngelHeart[{chat_id}]: 暂无可用上下文，跳过重构。")
            return
        recent_dialogue, historical_context, _ = conv
        if not recent_dialogue and not historical_context:
            logger.debug(f"AngelHeart[{chat_id}]: 暂无可用上下文，跳过重构。")
            return

        context_recent_dialogue, prompt_recent_dialogue = self._split_recent_dialogue_at_current_message(
            recent_dialogue, current_message_id
        )
        final_prompt_str = self._generate_final_prompt(prompt_recent_dialogue, None, alias)
        should_mark_processed = True
        if self._is_group_chat(chat_id):
            scene_hint = "这是一个群聊场景。"
        elif self._is_private_chat(chat_id):
            scene_prompt = "你正在一个私聊中扮演角色，你的昵称是 '{alias}'。"

        # 2. 标记已处理消息（如果需要）
        self._mark_processed_if_needed(chat_id, recent_dialogue, should_mark_processed)

        # 3. 使用 MessageProcessor 构建上下文
        processor = MessageProcessor(alias)
        new_contexts = self._build_contexts_with_processor(
            processor,
            historical_context,
            context_recent_dialogue,
            chat_id,
            current_message_id,
            scene_hint,
        )
        extra_image_urls = (
            self._collect_non_current_image_urls(recent_dialogue, current_message_id)
            if preserve_current_image_urls
            else []
        )
        current_image_urls = self._collect_current_image_urls(
            recent_dialogue, current_message_id
        )

        # 4. 注入工作账本临时提醒（不保存），不再注入秘书决策建议
        work_context = self._build_temporary_work_ledger_context(chat_id, event)
        if work_context:
            new_contexts.append(work_context)

        # 4.1 群聊注入回复字数提醒（常规/焦点）
        length_context = self._build_temporary_reply_length_context(
            chat_id,
            context_recent_dialogue,
            prompt_recent_dialogue,
        )
        if length_context:
            new_contexts.append(length_context)

        # 5. 根据 Provider 的 modalities 配置过滤图片内容
        new_contexts = self.filter_images_for_provider(chat_id, new_contexts)

        # 6. 更新请求对象
        self._update_request(
            req,
            new_contexts,
            final_prompt_str,
            alias,
            scene_prompt,
            preserve_current_image_urls=preserve_current_image_urls,
            current_image_urls=current_image_urls,
            extra_image_urls=extra_image_urls,
        )

        if not self._is_valid_final_prompt(final_prompt_str):
            logger.warning(
                f"AngelHeart[{chat_id}]: 重建后的当前提示词为空，本次仅重建上文，保留原始 req.prompt。"
            )

        logger.debug(
            f"AngelHeart[{chat_id}]: LLM请求体已重构，采用'完整上下文+聚焦指令'模式。"
        )

    @config_manager.setter
    def config_manager(self, value):
        self._config_manager = value
