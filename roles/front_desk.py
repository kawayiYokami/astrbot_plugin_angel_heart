"""
AngelHeart 插件 - 前台角色 (FrontDesk)
负责接收并缓存所有合规消息。
"""

import time
import os
import copy

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image  # 导入 Image 和 Plain 组件
from typing import Any, List, Dict  # 导入类型提示

# 导入公共工具函数和 ConversationLedger
from ..core.utils import convert_content_to_string, format_relative_time
from ..core.utils import partition_dialogue, format_final_prompt
from ..core.image_processor import ImageProcessor


class FrontDesk:
    """
    前台角色 - 专注的消息接收与缓存员
    """

    def __init__(self, config_manager, angel_context):
        """
        初始化前台角色。

        Args:
            config_manager: 配置管理器实例。
            angel_context: AngelHeart全局上下文实例。
        """
        self._config_manager = config_manager
        self.context = angel_context

        # 移除本地缓存：存储每个会话的未处理用户消息
        # self.unprocessed_messages: Dict[str, List[Dict]] = {}

        # 闭嘴状态已迁移到 angel_context.silenced_until

        # 初始化图片处理器
        self.image_processor = ImageProcessor()

        # secretary 引用将由 main.py 设置
        self.secretary = None

    async def cache_message(self, chat_id: str, event: AstrMessageEvent):
        """
        前台职责：使用消息概要作为主要正文，处理图片组件并缓存。

        Args:
            chat_id (str): 会话ID。
            event (AstrMessageEvent): 消息事件对象。
        """
        # 1. 获取消息概要作为主要正文
        outline = event.get_message_outline()
        text_content = outline if outline and outline.strip() else ""

        # 2. 获取 MessageChain 用于图片处理
        message_chain = event.get_messages()
        logger.debug(f"AngelHeart[{chat_id}]: 缓存消息，消息概要: '{text_content}'")

        # 3. 构建标准多模态 content 列表
        content_list = []
        if text_content:
            content_list.append({
                "type": "text",
                "text": text_content
            })

        # 4. 处理图片组件
        for component in message_chain:
            if isinstance(component, Image):
                # 尝试使用官方方法处理本地文件或可访问的URL
                try:
                    # 检查是否是本地文件或可访问的URL
                    url = component.url or component.file
                    if url and (url.startswith("file:///") or url.startswith("base64://") or os.path.exists(url or "")):
                        # 对于本地文件，直接使用官方方法
                        base64_data = await component.convert_to_base64()
                        if base64_data:
                            # 转换为 data URL 格式
                            if base64_data.startswith("base64://"):
                                image_data = base64_data.replace("base64://", "")
                            else:
                                image_data = base64_data
                            data_url = f"data:image/jpeg;base64,{image_data}"
                            content_list.append({
                                "type": "image_url",
                                "image_url": {"url": data_url},
                                "original_url": url  # 保存原始URL供转述使用
                            })
                        else:
                            raise Exception("convert_to_base64 返回空值")
                    else:
                        # 对于网络URL，尝试下载，如果失败则跳过
                        base64_data = await component.convert_to_base64()
                        if base64_data:
                            # 转换为 data URL 格式
                            if base64_data.startswith("base64://"):
                                image_data = base64_data.replace("base64://", "")
                            else:
                                image_data = base64_data
                            data_url = f"data:image/jpeg;base64,{image_data}"
                            content_list.append({
                                "type": "image_url",
                                "image_url": {"url": data_url},
                                "original_url": url  # 保存原始URL供转述使用
                            })
                        else:
                            raise Exception("网络图片下载失败")
                except Exception as e:
                    # 图片处理失败时，用文本占位符替换，避免传递空或无效URL
                    original_url = component.url or component.file or "未知URL"
                    logger.debug(f"AngelHeart[{chat_id}]: 图片处理跳过，URL: {original_url}, 原因: {str(e)[:100]}")
                    # 不添加任何内容，完全跳过图片，保持原有文本消息不变

        # 5. 如果没有内容，创建一个空文本
        if not content_list:
            content_list.append({
                "type": "text",
                "text": ""
            })

        # 6. 构建完整的消息字典
        new_message = {
            "role": "user",
            "content": content_list,  # 标准多模态列表
            "sender_id": event.get_sender_id(),
            "sender_name": event.get_sender_name(),
            "timestamp": event.get_timestamp() if hasattr(event, 'get_timestamp') and event.get_timestamp() else time.time(),
        }

        # 7. 将消息添加到 Ledger
        self.context.conversation_ledger.add_message(chat_id, new_message)
        logger.info(f"AngelHeart[{chat_id}]: 消息已缓存，使用概要作为正文，包含 {len(content_list)} 个组件。")



    async def handle_event(self, event: AstrMessageEvent):
        """
        处理新消息事件。这是前台的主要入口点。
        它会根据配置进行闭嘴、掌嘴、唤醒词检测，然后决定是否缓存和通知秘书。
        """
        chat_id = event.unified_msg_origin
        current_time = time.time()
        message_content = event.get_message_outline()

        # 1. 闭嘴状态检查 (最高优先级) - 从全局上下文读取
        if chat_id in self.context.silenced_until and current_time < self.context.silenced_until[chat_id]:
            remaining = self.context.silenced_until[chat_id] - current_time
            logger.info(f"AngelHeart[{chat_id}]: 处于闭嘴状态 (剩余 {remaining:.1f} 秒)，事件已终止。")
            event.stop_event() # 彻底中断
            return

        # 2. 消息合法性检查
        if not message_content.strip():
            # 对于空消息，我们只跳过自己的逻辑，不影响其他插件
            return

        # 3. 掌嘴词检测
        slap_words_str = self.config_manager.slap_words
        if slap_words_str:
            slap_words = [word.strip() for word in slap_words_str.split('|') if word.strip()]
            for word in slap_words:
                if word in message_content:
                    silence_duration = self.config_manager.silence_duration
                    self.context.silenced_until[chat_id] = current_time + silence_duration
                    logger.info(f"AngelHeart[{chat_id}]: 检测到掌嘴词 '{word}'，启动闭嘴模式 {silence_duration} 秒，事件已终止。")
                    event.stop_event() # 彻底中断
                    return

        # 4. 缓存消息
        await self.cache_message(chat_id, event)

        # 4.5. 【新增】检查并补充历史消息，确保至少有7条上下文
        logger.debug(f"AngelHeart[{chat_id}]: 开始检查上下文是否需要补充")
        await self._ensure_minimum_context(chat_id, event)

        # 5. 唤醒词检测
        # 设计意图：此处的缓存模拟了 AI 的"看群时间"或"短期记忆"。
        # 在 'analysis_on_mention_only' 模式下，只要在这段记忆中出现过一次别名，
        # AI 就会认为当前对话值得关注，从而触发后续的分析。
        # 这并非要求每条消息都包含别名，而是模拟一种"被唤醒后持续观察"的行为。
        if self.config_manager.analysis_on_mention_only:
            alias_str = self.config_manager.alias
            if not alias_str:
                return

            aliases = [name.strip() for name in alias_str.split('|') if name.strip()]
            # 从 ConversationLedger 获取所有消息（包括历史消息），因为AI应该记住被呼唤的状态
            historical_context, unprocessed_dialogue, _ = self.context.conversation_ledger.get_context_snapshot(chat_id)
            all_messages = historical_context + unprocessed_dialogue

            found_mention = any(
                alias in convert_content_to_string(msg.get("content", ""))
                for msg in all_messages
                for alias in aliases
            )

            if not found_mention:
                logger.info(f"AngelHeart[{chat_id}]: '仅呼唤'模式开启，但缓存中未检测到别名，不通知秘书。")
                return

        # 6. 【核心逻辑 V2】检查 Secretary 是否忙中
        if await self.context.is_chat_processing(chat_id):
            # Secretary 忙中：阻塞事件，等待观察期结束或被新事件取代
            logger.debug(f"AngelHeart[{chat_id}]: Secretary 忙中，进入事件扣押与阻塞流程")

            # 获取 Future 并阻塞当前事件传播链
            future = await self.context.hold_and_start_observation(chat_id)
            result = await future  # 阻塞在这里，直到收到信号

            # 处理唤醒信号
            if result == "KILL":
                # 被新消息取代，干净地终止此事件
                logger.debug(f"AngelHeart[{chat_id}]: 此事件已被新消息取代，正在终止...")
                # 核心：产生空回复并停止事件传播
                result_obj = event.get_result()
                if result_obj:
                    result_obj.chain = []
                event.stop_event()
                return
            elif result == "PROCESS":
                # 观察期结束，轮到自己处理
                logger.info(f"AngelHeart[{chat_id}]: 观察期结束，开始处理此事件")
                await self.secretary.process_notification(event)
            else:
                # 未知信号，记录警告
                logger.warning(f"AngelHeart[{chat_id}]: 收到未知信号 '{result}'，忽略处理")
                return
        else:
            # Secretary 空闲：直接通知处理
            await self.secretary.process_notification(event)

    async def _ensure_minimum_context(self, chat_id: str, event: AstrMessageEvent):
        """
        确保会话至少有7条消息
        历史消息标记为已处理，新消息保持未处理状态
        """
        try:
            ledger = self.context.conversation_ledger
            current_messages = ledger.get_all_messages(chat_id)

            # 统计总消息数（包括图片等无文本消息）
            total_messages = len(current_messages)
            text_messages = [msg for msg in current_messages
                            if self._has_text_content(msg)]

            logger.debug(f"AngelHeart[{chat_id}]: 当前消息总数: {total_messages}, 有文本的消息数: {len(text_messages)}")

            # 基于总消息数判断是否需要补充（不只是文本消息）
            if total_messages >= 7:
                logger.debug(f"AngelHeart[{chat_id}]: 消息数量充足({total_messages} >= 7)，无需补充")
                return

            # 固定获取19条历史消息（除了最新那条）
            logger.info(f"AngelHeart[{chat_id}]: 当前有 {len(text_messages)} 条消息，开始获取历史消息")
            supplement_messages = await self._fetch_database_history(chat_id, 19, event)

            if supplement_messages:
                # 确保历史消息标记为已处理
                for msg in supplement_messages:
                    msg["is_processed"] = True

                # 保留最新的消息（刚收到的），清空其他记录避免重复
                if current_messages:
                    # 按时间戳排序，找到最新的消息
                    sorted_current = sorted(current_messages, key=lambda m: m.get("timestamp", 0))
                    latest_message = sorted_current[-1]
                    
                    # 合并历史消息和最新消息
                    all_messages = supplement_messages + [latest_message]
                else:
                    # 如果没有当前消息（不太可能），只使用历史消息
                    all_messages = supplement_messages
                
                all_messages.sort(key=lambda m: m.get("timestamp", 0))

                # 使用公共方法更新消息列表
                ledger.set_messages(chat_id, all_messages)

                # 调试：检查消息状态
                processed_count = sum(1 for m in all_messages if m.get("is_processed", False))
                logger.info(f"AngelHeart[{chat_id}]: 补充了 {len(supplement_messages)} 条已处理的历史消息")
                logger.debug(f"AngelHeart[{chat_id}]: 总消息数: {len(all_messages)}, 已处理: {processed_count}, 未处理: {len(all_messages) - processed_count}")



                # 调试：检查时间戳范围
                if all_messages:
                    timestamps = [m.get("timestamp", 0) for m in all_messages]
                    logger.debug(f"AngelHeart[{chat_id}]: 时间戳范围: {min(timestamps)} - {max(timestamps)}")

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

    async def _fetch_database_history(self, chat_id: str, needed_count: int, event: AstrMessageEvent) -> List[Dict]:
        """
        从QQ API获取历史消息并转换为天使之心格式
        """
        try:
            # 解析群号
            group_id = self._extract_group_id(chat_id)

            logger.debug(f"AngelHeart[{chat_id}]: 准备从QQ API获取历史消息 - group_id: {group_id}")

            # 获取bot实例
            bot = self._get_bot_instance(event)
            if not bot:
                logger.error(f"AngelHeart[{chat_id}]: 无法获取bot实例")
                return []

            # 直接调用QQ API获取历史消息
            raw_messages = await self._get_qq_history_direct(bot, group_id, needed_count)

            logger.debug(f"AngelHeart[{chat_id}]: 从QQ API获取到 {len(raw_messages)} 条历史记录")



            # 转换格式
            converted_messages = []
            for raw_msg in raw_messages:
                msg = self._convert_raw_qq_message_to_angelheart_format(raw_msg)
                if msg:
                    converted_messages.append(msg)
                    logger.debug(f"AngelHeart[{chat_id}]: 转换消息成功")
                else:
                    logger.debug(f"AngelHeart[{chat_id}]: 跳过无文本内容的消息")
            return converted_messages

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 获取QQ API历史失败: {e}", exc_info=True)
            return []

    async def _get_qq_history_direct(self, bot, group_id: str, count: int) -> List[Dict]:
        """
        直接调用QQ API获取历史消息
        参考天使之眼的实现
        """
        try:
            # 调用get_group_msg_history API
            payloads = {
                "group_id": int(group_id),
                "message_seq": 0,  #从最新开始
                "reverseOrder": True  # 倒序获取（但实际返回仍是正序）
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
            logger.error(f"AngelHeart: 调用QQ API失败: {e}")
            return []





    def _extract_group_id(self, chat_id: str) -> str:
        """从chat_id中提取群号"""
        # chat_id格式通常是 "default:GroupMessage:群号"
        parts = chat_id.split(':')
        return parts[-1] if len(parts) >= 3 else chat_id

    def _get_bot_instance(self, event: AstrMessageEvent):
        """从事件对象获取bot实例"""
        try:
            # 参考天使之眼的方式：从event.bot获取
            if hasattr(event, 'bot'):
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
            sender_name = sender.get("nickname", "未知用户")

            # 2. 判断是否为机器人自己发送的消息
            # 直接对比 sender.user_id 和 self_id
            self_id = str(raw_msg.get("self_id", ""))
            is_self = (str(sender_id) == self_id)
            role = "assistant" if is_self else "user"

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
                "timestamp": timestamp,
                "is_processed": True,
                "source": "qq_api"
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


    def filter_images_for_provider(self, chat_id: str, contexts: List[Dict]) -> List[Dict]:
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
                logger.debug(f"AngelHeart[{chat_id}]: 无法获取当前 provider，跳过图片过滤")
                return contexts

            # 检查 provider 的 modalities 配置
            provider_config = provider.provider_config
            modalities = provider_config.get("modalities", ["text"])

            # 如果支持图片，直接返回
            if "image" in modalities:
                logger.debug(f"AngelHeart[{chat_id}]: Provider {provider_config.get('id', 'unknown')} 支持图片，无需过滤")
                return contexts

            # 不支持图片，需要过滤
            logger.info(f"AngelHeart[{chat_id}]: Provider {provider_config.get('id', 'unknown')} 不支持图片，开始过滤图片内容")

            filtered_contexts = []
            images_filtered_count = 0

            for msg in contexts:
                filtered_msg = copy.deepcopy(msg)  # 深拷贝避免修改原始数据

                if msg.get("role") == "user" and isinstance(filtered_msg.get("content"), list):
                    original_content = filtered_msg["content"]
                    filtered_content = []
                    has_image = False

                    for item in original_content:
                        if item.get("type") == "image_url":
                            has_image = True
                            images_filtered_count += 1
                            # 静默移除图片，不添加任何提示
                        else:
                            # 保留非图片的所有组件（文本、文件等）
                            filtered_content.append(item)

                    filtered_msg["content"] = filtered_content

                    if has_image:
                        logger.debug(f"AngelHeart[{chat_id}]: 已过滤用户消息中的图片内容")

                filtered_contexts.append(filtered_msg)

            if images_filtered_count > 0:
                logger.info(f"AngelHeart[{chat_id}]: 总共过滤了 {images_filtered_count} 个图片组件")

            return filtered_contexts

        except Exception as e:
            logger.error(f"AngelHeart[{chat_id}]: 图片过滤时发生错误: {e}", exc_info=True)
            # 出错时返回原始上下文，避免破坏流程
            return contexts

    async def rewrite_prompt_for_llm(self, chat_id: str, req: Any):
        """
        重构请求体，实现完整的对话历史格式化和指令注入。
        """
        logger.debug(f"AngelHeart[{chat_id}]: 开始重构LLM请求体...")

        # 1. 获取决策，如果不存在则无法继续
        decision = self.secretary.get_decision(chat_id)
        if not decision:
            logger.debug(f"AngelHeart[{chat_id}]: 私聊不参与重构。")
            return

        # 2. 使用决策中保存的对话快照（而不是重新获取，避免被标记为已处理的消息丢失）
        recent_dialogue = decision.recent_dialogue

        # 获取历史对话用于构建完整上下文
        historical_context, _, _ = partition_dialogue(self.context.conversation_ledger, chat_id)

        # 生成聚焦指令
        final_prompt_str = format_final_prompt(recent_dialogue, decision)

        # 3. 准备完整的对话历史 (Context)
        full_history = historical_context + recent_dialogue

        # 4. 遍历 full_history 并动态注入元数据和图片转述
        new_contexts = []
        for msg in full_history:
            if msg.get("role") == "user":
                # 对于 user 消息，生成 header 并注入到 content 中
                header = f"[群友: {msg.get('sender_name', '成员')}/{msg.get('sender_id', 'Unknown')}]{format_relative_time(msg.get('timestamp'))}: "

                # 使用深拷贝复制 content 列表以进行修改，确保不污染原始数据
                new_content = copy.deepcopy(msg.get("content", []))

                # 【新增】处理图片转述 - 直接检查转述字段
                image_caption = msg.get("image_caption")
                if image_caption:
                    # 有转述就无脑合并
                    caption_text = f"[图片描述: {image_caption}]"

                    # 移除所有图片组件
                    new_content = [item for item in new_content if item.get("type") != "image_url"]

                    # 添加转述文本
                    new_content.append({"type": "text", "text": caption_text})

                    logger.debug(f"AngelHeart[{chat_id}]: 已将图片转述合成为文本: {caption_text[:50]}...")

                if isinstance(new_content, list) and new_content:
                    # 找到第一个 text 组件并注入 header
                    found_text = False
                    for item in new_content:
                        if item.get("type") == "text":
                            item["text"] = header + item.get("text", "")
                            found_text = True
                            break
                    # 如果没有 text 组件（纯图片），则在开头插入一个
                    if not found_text:
                        new_content.insert(0, {"type": "text", "text": header.strip()})

                new_contexts.append({
                    "role": "user",
                    "content": new_content
                })
            else:
                # assistant 消息保持不变
                new_contexts.append(msg)

        # 5. 根据 Provider 的 modalities 配置过滤图片内容
        new_contexts = self.filter_images_for_provider(chat_id, new_contexts)

        # 6. 完全覆盖原有的 contexts
        req.contexts = new_contexts

        # 7. 聚焦指令并赋值给 req.prompt
        req.prompt = final_prompt_str

        # 8. 清空 image_urls 并注入系统提示词
        req.image_urls = []  # 图片已在 contexts 中

        # 注入系统提示词
        persona_name = decision.persona_name if decision else 'AngelHeart'
        alias = self.config_manager.alias
        original_system_prompt = getattr(req, 'system_prompt', '')
        new_system_prompt = f"{original_system_prompt}\n\n你正在一个群聊中扮演 '{persona_name}' 的角色，你的别名是 '{alias}'。"
        req.system_prompt = new_system_prompt

        logger.info(f"AngelHeart[{chat_id}]: LLM请求体已重构，采用'完整上下文+聚焦指令'模式。")

    @config_manager.setter
    def config_manager(self, value):
        self._config_manager = value
