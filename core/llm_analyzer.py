import asyncio
from typing import List, Dict
import json
from pathlib import Path
import string
import datetime
import re
from astrbot.api import logger
from astrbot.core.db.po import Persona
from ..core.utils import convert_content_to_string, format_relative_time
from ..models.analysis_result import SecretaryDecision


class SafeFormatter(string.Formatter):
    """
    安全的字符串格式化器，当占位符不存在时返回空字符串或指定的默认值
    """

    def __init__(self, default_value: str = ""):
        """
        初始化安全格式化器

        Args:
            default_value (str): 当占位符不存在时返回的默认值
        """
        self.default_value = default_value

    def get_value(self, key, args, kwargs):
        """
        获取占位符的值

        Args:
            key: 占位符的键
            args: 位置参数
            kwargs: 关键字参数

        Returns:
            占位符的值，如果不存在则返回默认值
        """
        if isinstance(key, str):
            try:
                return kwargs[key]
            except KeyError:
                return self.default_value
        else:
            return string.Formatter.get_value(key, args, kwargs)


class LLMAnalyzer:
    """
    LLM分析器 - 执行实时分析和标注
    采用两级AI协作体系：
    1. 轻量级AI（分析员）：低成本、快速地判断是否需要回复。
    2. 重量级AI（专家）：在需要时，生成高质量的回复。
    """

    # 类级别的常量
    MAX_CONVERSATION_LENGTH = 50
    DEFAULT_PERSONA_NAME = "默认人格"

    def __init__(self, analyzer_model_name: str, context, strategy_guide: str = None, config_manager=None):
        self.analyzer_model_name = analyzer_model_name
        self.context = context  # 存储 context 对象，用于动态获取 provider
        self.strategy_guide = strategy_guide or ""  # 存储策略指导文本
        self.config_manager = config_manager  # 存储 config_manager 对象，用于访问配置
        self.is_ready = False  # 默认认为分析器未就绪
        self.base_prompt_template = "" # 初始化为空字符串

        # 加载外部 Prompt 模板
        try:
            prompt_path = Path(__file__).parent.parent / "prompts" / "secretary_analyzer.md"
            self.base_prompt_template = prompt_path.read_text(encoding="utf-8")
            self.is_ready = True # 成功加载后，标记为就绪
            logger.info("AngelHeart分析器: Prompt模板加载成功。")
        except FileNotFoundError:
            logger.critical("AngelHeart分析器: 核心Prompt模板文件 'prompts/secretary_analyzer.md' 未找到。分析器将无法工作。")

        if not self.analyzer_model_name:
            logger.warning("AngelHeart的分析模型未配置，功能将受限。")

    def _parse_response(self, response_text: str, persona_name: str, alias: str) -> SecretaryDecision:
        """
        解析AI模型的响应文本并返回SecretaryDecision对象

        Args:
            response_text (str): AI模型的响应文本

        Returns:
            SecretaryDecision: 解析后的决策对象
        """
        return self._parse_and_validate_decision(response_text, persona_name, alias)

    async def _call_ai_model(self, prompt: str, chat_id: str) -> str:
        """
        调用AI模型并返回响应文本

        Args:
            prompt (str): 发送给AI模型的提示词
            chat_id (str): 会话ID

        Returns:
            str: AI模型的响应文本

        Raises:
            Exception: 如果调用AI模型失败
        """
        # 3. 如果启用了提示词日志增强，则记录最终构建的完整提示词
        if self.config_manager and self.config_manager.prompt_logging_enabled:
            logger.info(f"[AngelHeart][{chat_id}]:最终构建的完整提示词 ----------------")
            logger.info(prompt)
            logger.info("----------------------------------------")

        # 动态获取 provider
        provider = self.context.get_provider_by_id(self.analyzer_model_name)
        if not provider:
            logger.warning(
                f"AngelHeart分析器: 未找到名为 '{self.analyzer_model_name}' 的分析模型提供商。"
            )
            raise Exception("未找到分析模型提供商")

        token = await provider.text_chat(prompt=prompt)
        response_text = token.completion_text.strip()

        # 记录AI模型的完整响应内容
        logger.info(f"[AngelHeart][{chat_id}]: 轻量模型的分析推理 ----------------")
        logger.info(response_text)
        logger.info("----------------------------------------")

        return response_text

    def _build_prompt(self, historical_context: List[Dict], recent_dialogue: List[Dict], persona_name: str) -> str:
        """
        使用给定的对话历史和人格名称构建分析提示词

        Args:
            conversations (List[Dict]): 对话历史
            persona_name (str): 人格名称

        Returns:
            str: 构建好的提示词
        """
        # 分别格式化历史上下文和最近对话
        historical_text = self._format_conversation_history(historical_context, persona_name)
        recent_text = self._format_conversation_history(recent_dialogue, persona_name)

        # 增强检查：如果历史文本为空，则记录警告日志
        if not historical_text and not recent_text:
            logger.warning("AngelHeart分析器: 格式化后的对话历史为空，将生成一个空的分析提示词。")

        # 获取配置中的别名
        alias = self.config_manager.alias if self.config_manager else "AngelHeart"

        # 使用安全的格式化器来构建提示词，传递结构化的上下文
        formatter = SafeFormatter()
        base_prompt = formatter.format(
            self.base_prompt_template,
            persona_name=persona_name,
            historical_context=historical_text,
            recent_dialogue=recent_text,
            reply_strategy_guide=self.strategy_guide,
            alias=alias
        )

        return base_prompt

    async def _get_persona(self, chat_id: str) -> Persona:
        """
        获取指定会话的当前人格对象。
        如果当前会话没有指定人格，或指定的人格无效，则返回默认人格。

        Args:
            chat_id (str): 会话ID

        Returns:
            Persona: 最终适用的人格对象。
        """
        # 1. 优先获取当前会话的人格
        try:
            conversation_manager = self.context.conversation_manager
            curr_cid = await conversation_manager.get_curr_conversation_id(chat_id)
            if curr_cid:
                conversation = await conversation_manager.get_conversation(chat_id, curr_cid)
                # 2. 检查是否存在 'persona_id'
                if conversation and conversation.persona_id:
                    try:
                        # 3. 如果存在，则加载并返回这个【当前人格】
                        logger.debug(f"正在为会话 {chat_id} 加载当前人格: {conversation.persona_id}")
                        return await self.context.persona_manager.get_persona(conversation.persona_id)
                    except ValueError:
                        logger.warning(f"会话中指定的当前人格 '{conversation.persona_id}' 不存在，将使用默认人格。")
        except Exception as e:
            logger.warning(f"获取当前人格过程中发生未知错误: {e}，将使用默认人格。")

        # 4. 只有在上述所有步骤都失败时，才返回默认人格作为备用
        logger.debug(f"会话 {chat_id} 未指定有效人格，正在返回默认人格。")
        return self.context.persona_manager.selected_default_persona

    async def analyze_and_decide(self, historical_context: List[Dict], recent_dialogue: List[Dict], chat_id: str) -> SecretaryDecision:
        """
        分析对话历史，做出结构化的决策 (JSON)
        """
        # 异步获取 Persona 对象
        persona = await self._get_persona(chat_id)
        persona_name = persona.persona_id if persona else "默认人格"
        # 获取别名
        alias = self.config_manager.alias if self.config_manager else "AngelHeart"

        if not self.analyzer_model_name:
            logger.debug("AngelHeart分析器: 分析模型未配置, 跳过分析。")
            # 返回一个默认的不参与决策
            return SecretaryDecision(
                should_reply=False, reply_strategy="未配置", topic="未知"
            )

        if not self.is_ready:
            logger.debug("AngelHeart分析器: 由于核心Prompt模板丢失，分析器已禁用。")
            return SecretaryDecision(
                should_reply=False, reply_strategy="分析器未就绪", topic="未知"
            )

        # 1. 调用轻量级AI进行分析
        logger.debug("AngelHeart分析器: 准备调用轻量级AI进行分析...")
        prompt = self._build_prompt(historical_context, recent_dialogue, persona_name)

        # 2. 增强检查：如果生成的提示词为空，则记录警告日志并返回一个明确的决策
        if not prompt:
            logger.warning(f"AngelHeart分析器: 生成的分析提示词为空，将返回'分析内容为空'的决策。")
            return SecretaryDecision(
                should_reply=False, reply_strategy="分析内容为空", topic="未知"
            )

        try:
            response_text = await self._call_ai_model(prompt, chat_id)
            # 调用新方法解析和验证响应，并传递 persona_name 和 alias
            return self._parse_response(response_text, persona_name, alias)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(
                f"AngelHeart分析器: AI返回的JSON格式或内容有误: {e}. 原始响应: {response_text[:200]}..."
            )
        except asyncio.CancelledError:
            # 重新抛出 CancelledError，以确保异步任务可以被正常取消
            raise
        except Exception as e:
            logger.error(
                f"💥 AngelHeart分析器: 轻量级AI分析失败: {e}",
                exc_info=True,
            )

        # 如果发生任何错误，都返回一个默认的不参与决策
        return SecretaryDecision(
            should_reply=False, reply_strategy="分析失败", topic="未知"
        )

    def _parse_and_validate_decision(self, response_text: str, persona_name: str, alias: str) -> SecretaryDecision:
        """解析并验证来自AI的响应文本，构建SecretaryDecision对象"""

        # 使用正则表达式查找所有可能的JSON对象，并取最后一个
        json_matches = re.findall(r"\{.*?\}", response_text, re.DOTALL)
        if json_matches:
            # 取最后一个匹配到的JSON对象
            json_text = json_matches[-1].strip()
        else:
            # 如果没有找到任何JSON对象，则记录错误并返回默认决策
            logger.warning(
                f"AngelHeart分析器: AI响应中未找到有效的JSON对象。原始响应: {response_text[:200]}..."
            )
            # 返回一个默认的不参与决策
            return SecretaryDecision(
                should_reply=False, reply_strategy="分析内容无有效JSON", topic="未知",
                persona_name=persona_name, alias=alias
            )

        # 解析JSON
        try:
            decision_data = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.warning(
                f"AngelHeart分析器: 提取的JSON对象解析失败: {e}. 原始提取的JSON: {json_text[:200]}..."
            )
            # 返回一个默认的不参与决策
            return SecretaryDecision(
                should_reply=False, reply_strategy="分析内容JSON解析失败", topic="未知",
                persona_name=persona_name, alias=alias
            )

        # 对来自 AI 的 JSON 做健壮性处理，防止字段为 null 或类型不符合导致 pydantic 校验失败
        raw = decision_data
        # 解析 should_reply，兼容 bool、数字、字符串等形式
        should_reply_raw = raw.get("should_reply", False)
        if isinstance(should_reply_raw, bool):
            should_reply = should_reply_raw
        else:
            sr = str(should_reply_raw).strip().lower()
            should_reply = sr in ("true", "1", "yes", "y")

        # 解析 reply_strategy、topic 和 reply_target，确保为字符串，若为空或 None 则使用安全默认并记录警告
        reply_strategy_raw = raw.get("reply_strategy")
        topic_raw = raw.get("topic")
        reply_target_raw = raw.get("reply_target")

        if reply_strategy_raw is None:
            logger.warning(
                f"AngelHeart分析器: AI 返回的 reply_strategy 为 null，使用默认值。 原始提取的JSON: {json_text[:200]}"
            )
            reply_strategy = ""
        else:
            reply_strategy = str(reply_strategy_raw)

        if topic_raw is None:
            logger.warning(
                f"AngelHeart分析器: AI 返回的 topic 为 null，使用默认值。 原始提取的JSON: {json_text[:200]}"
            )
            topic = ""
        else:
            topic = str(topic_raw)

        if reply_target_raw is None:
            logger.warning(
                f"AngelHeart分析器: AI 返回的 reply_target 为 null，使用默认值。 原始提取的JSON: {json_text[:200]}"
            )
            reply_target = ""
        else:
            reply_target = str(reply_target_raw)

        decision = SecretaryDecision(
            should_reply=should_reply, reply_strategy=reply_strategy, topic=topic,
            reply_target=reply_target, persona_name=persona_name, alias=alias
        )

        logger.debug(
            f"AngelHeart分析器: 轻量级AI分析完成。决策: {decision} , 回复策略: {reply_strategy} ，话题: {topic}"
        )
        return decision

    def _format_conversation_history(self, conversations: List[Dict], persona_name: str) -> str:
        """
        格式化对话历史，生成统一的日志式格式。

        Args:
            conversations (List[Dict]): 包含对话历史的字典列表。
            persona_name (str): 当前使用的persona名称，用于助理消息的格式化。

        Returns:
            str: 格式化后的对话历史字符串。
        """
        # Phase 3: 增加空数据保护机制 - 开始
        # 防止空数据导致崩溃的保护机制
        if not conversations:
            logger.warning("_format_conversation_history 收到空数据流")
            return ""
        # Phase 3: 增加空数据保护机制 - 结束

        lines = []
        # 定义历史与新消息的分隔符对象
        SEPARATOR_OBJ = {"role": "system", "content": "history_separator"}
        # 状态标记：False表示处理历史消息，True表示处理新消息
        is_after_separator = False

        # 遍历最近的 MAX_CONVERSATION_LENGTH 条对话
        for conv in conversations[-self.MAX_CONVERSATION_LENGTH:]:
            # 确保 conv 是一个字典
            if not isinstance(conv, dict):
                logger.warning(f"跳过非字典类型的对话项: {type(conv)}")
                continue

            # 检查是否遇到分隔符
            if conv == SEPARATOR_OBJ:
                is_after_separator = True
                lines.append("\n--- 以上是历史消息，仅作为策略参考，不需要回应 ---\n")
                lines.append("\n--- 后续的最新对话，你需要分辨出里面的人是不是在对你说话 ---\n")
                continue  # 跳过分隔符本身，不添加到最终输出

            # 使用新的辅助方法格式化单条消息
            formatted_message = self._format_single_message(conv, persona_name)
            lines.append(formatted_message)

        # 将所有格式化后的行连接成一个字符串并返回
        return "\n".join(lines)

    def _format_single_message(self, conv: Dict, persona_name: str) -> str:
        """
        格式化单条消息，生成统一的日志式格式。

        Args:
            conv (Dict): 包含消息信息的字典。
            persona_name (str): 当前使用的persona名称，用于助理消息的格式化。

        Returns:
            str: 格式化后的消息字符串。
        """
        role = conv.get("role")
        content = conv.get("content", "")

        if role == "assistant":
            # 助理消息格式: [助理: {persona_name}]\n[内容: 文本]\n{content}
            formatted_content = convert_content_to_string(content)
            return f"[助理: {persona_name}]\n[内容: 文本]\n{formatted_content}"
        elif role == "user":
            # 用户消息需要区分来源
            # 检查是否包含sender_name字段，这通常意味着来自FrontDesk的缓存消息
            if "sender_name" in conv:
                # 来自缓存的新消息
                sender_id = conv.get("sender_id", "Unknown")
                sender_name = conv.get("sender_name", "成员")
                timestamp = conv.get("timestamp")
                relative_time_str = format_relative_time(timestamp)
                formatted_content = convert_content_to_string(content)

                # 新格式: [群友: 昵称 (ID: ...)] (相对时间)\n[内容: 类型]\n实际内容
                header = f"[群友: {sender_name} (ID: {sender_id})]{relative_time_str}"

                # 简单判断内容类型，这里可以更复杂
                content_type = "文本"
                if isinstance(content, str) and content.startswith("[图片]"):
                    content_type = "图片"
                elif isinstance(content, list):
                    # 如果content是列表，convert_content_to_string会处理成字符串
                    # 我们可以检查转换后的字符串是否包含[图片]
                    temp_str = convert_content_to_string(content)
                    if "[图片]" in temp_str:
                        content_type = "图片"

                return f"{header}\n[内容: {content_type}]\n{formatted_content}"
            else:
                # 来自数据库的历史消息
                formatted_content = convert_content_to_string(content)
                # 历史消息格式: [群友: (历史记录)]\n[内容: 类型]\n实际内容
                header = "[群友: (历史记录)]"

                # 同样判断内容类型
                content_type = "文本"
                if isinstance(formatted_content, str) and "[图片]" in formatted_content:
                    content_type = "图片"

                return f"{header}\n[内容: {content_type}]\n{formatted_content}"
        else:
            # 对于其他角色（如system等），可以考虑跳过或给予默认名称
            # 这里为了简化，我们给一个通用名称
            formatted_content = convert_content_to_string(content)
            return f"[{role}]\n[内容: 文本]\n{formatted_content}"
