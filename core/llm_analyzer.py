import asyncio
from typing import List, Dict
import json
from pathlib import Path
import string

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
from ..core.utils import convert_content_to_string, format_relative_time, JsonParser
from ..models.analysis_result import SecretaryDecision
from .prompt_module_loader import PromptModuleLoader


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

    def __init__(
        self,
        analyzer_model_name: str,
        context,
        strategy_guide: str = None,
        config_manager=None,
    ):
        self.analyzer_model_name = analyzer_model_name
        self.context = context  # 存储 context 对象，用于动态获取 provider
        self.strategy_guide = strategy_guide or ""  # 存储策略指导文本
        self.config_manager = config_manager  # 存储 config_manager 对象，用于访问配置
        self.is_ready = False  # 默认认为分析器未就绪

        # 初始化提示词模块加载器
        self.prompt_loader = PromptModuleLoader()

        # 初始化JSON解析器
        self.json_parser = JsonParser()

        # 加载外部 Prompt 模板
        try:
            # 使用 PromptModuleLoader 构建提示词模板
            is_reasoning_model = config_manager.is_reasoning_model if config_manager else False
            self.base_prompt_template = self.prompt_loader.build_prompt_template(is_reasoning_model)

            if self.base_prompt_template:
                self.is_ready = True
                output_type = "指令" if is_reasoning_model else "推理"
                logger.info(f"AngelHeart分析器: Prompt模块组装成功，使用 {output_type} 版本。")
            else:
                self.is_ready = False
                logger.critical("AngelHeart分析器: Prompt模块组装失败，未生成有效模板。分析器将无法工作。")
        except Exception as e:
            self.is_ready = False
            logger.critical(f"AngelHeart分析器: Prompt模块组装时发生错误: {e}。分析器将无法工作。")

        if not self.analyzer_model_name:
            logger.warning("AngelHeart的分析模型未配置，功能将受限。")

    def reload_config(self, new_config_manager):
        """重新加载配置"""
        self.config_manager = new_config_manager

        # 重新加载提示词模块
        try:
            self.prompt_loader.reload_modules()
            is_reasoning_model = new_config_manager.is_reasoning_model if new_config_manager else False
            self.base_prompt_template = self.prompt_loader.build_prompt_template(is_reasoning_model)

            if self.base_prompt_template:
                self.is_ready = True
                output_type = "指令" if is_reasoning_model else "推理"
                logger.info(f"AngelHeart分析器: Prompt模板重新加载成功，使用 {output_type} 版本。")
            else:
                self.is_ready = False
                logger.warning("AngelHeart分析器: Prompt模板重新加载失败，分析器未就绪。")
        except Exception as e:
            self.is_ready = False
            logger.error(f"AngelHeart分析器: Prompt模板重新加载时发生错误: {e}")

    def _parse_response(self, response_text: str, alias: str) -> SecretaryDecision:
        """
        解析AI模型的响应文本并返回SecretaryDecision对象

        Args:
            response_text (str): AI模型的响应文本
            alias (str): AI的昵称

        Returns:
            SecretaryDecision: 解析后的决策对象
        """
        return self._parse_and_validate_decision(response_text, alias)

    async def _call_ai_model(self, prompt: str, chat_id: str) -> str:
        """
        调用AI模型并返回响应文本，包含3秒后自动重试1次机制
        """
        # 3. 如果启用了提示词日志增强，则记录最终构建的完整提示词
        if False:  # prompt_logging_enabled 已废弃
            logger.info(
                f"[AngelHeart][{chat_id}]:最终构建的完整提示词 ----------------"
            )
            logger.info(prompt)
            logger.info("----------------------------------------")

        # 动态获取 provider
        provider = self.context.get_provider_by_id(self.analyzer_model_name)
        if not provider:
            logger.warning(
                f"AngelHeart分析器: 未找到名为 '{self.analyzer_model_name}' 的分析模型提供商。"
            )
            raise Exception("未找到分析模型提供商")

        # 重试机制：最多重试1次，间隔3秒
        max_retries = 1
        retry_delay = 3  # 秒

        for attempt in range(max_retries + 1):
            try:
                token = await provider.text_chat(prompt=prompt)
                response_text = token.completion_text.strip()

                # 记录AI模型的完整响应内容
                logger.debug(
                    f"[AngelHeart][{chat_id}]: 轻量模型的分析推理 ----------------"
                )
                logger.debug(response_text)
                logger.debug("----------------------------------------")

                return response_text

            except Exception as e:
                if attempt < max_retries:
                    logger.warning(
                        f"AngelHeart分析器: 第{attempt + 1}次调用AI模型失败，{retry_delay}秒后重试: {e}"
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(
                        f"💥 AngelHeart分析器: 调用AI模型失败，已重试{max_retries}次: {e}",
                        exc_info=True,
                    )
                    raise

    def _build_prompt(
        self, historical_context: List[Dict], recent_dialogue: List[Dict]
    ) -> str:
        """
        使用给定的对话历史构建分析提示词

        Args:
            conversations (List[Dict]): 对话历史

        Returns:
            str: 构建好的提示词
        """
        # 分别格式化历史上下文和最近对话
        historical_text = self._format_conversation_history(historical_context)
        recent_text = self._format_conversation_history(recent_dialogue)

        # 增强检查：如果历史文本为空，则记录警告日志
        if not historical_text and not recent_text:
            logger.warning(
                "AngelHeart分析器: 格式化后的对话历史为空，将生成一个空的分析提示词。"
            )

        # 获取配置中的昵称
        alias = self.config_manager.alias if self.config_manager else "AngelHeart"

        # 使用直接的字符串替换来构建提示词，规避.format()方法对特殊字符的解析问题
        base_prompt = self.base_prompt_template
        base_prompt = base_prompt.replace("{historical_context}", historical_text)
        base_prompt = base_prompt.replace("{recent_dialogue}", recent_text)
        base_prompt = base_prompt.replace("{reply_strategy_guide}", self.strategy_guide)
        base_prompt = base_prompt.replace("{alias}", alias)
        base_prompt = base_prompt.replace(
            "{ai_self_identity}",
            self.config_manager.ai_self_identity if self.config_manager else "",
        )

        return base_prompt

    async def analyze_and_decide(
        self, historical_context: List[Dict], recent_dialogue: List[Dict], chat_id: str
    ) -> SecretaryDecision:
        """
        分析对话历史，做出结构化的决策 (JSON)
        """
        # 获取昵称
        alias = self.config_manager.alias if self.config_manager else "AngelHeart"

        if not self.analyzer_model_name:
            logger.debug("AngelHeart分析器: 分析模型未配置, 跳过分析。")
            # 返回一个默认的不参与决策
            return SecretaryDecision(
                should_reply=False, reply_strategy="未配置", topic="未知", alias=alias
            )

        if not self.is_ready:
            logger.debug("AngelHeart分析器: 由于核心Prompt模板丢失，分析器已禁用。")
            return SecretaryDecision(
                should_reply=False,
                reply_strategy="分析器未就绪",
                topic="未知",
                alias=alias,
            )

        # 1. 调用轻量级AI进行分析
        logger.debug("AngelHeart分析器: 准备调用轻量级AI进行分析...")
        prompt = self._build_prompt(historical_context, recent_dialogue)

        # 2. 增强检查：如果生成的提示词为空，则记录警告日志并返回一个明确的决策
        if not prompt:
            logger.warning(
                "AngelHeart分析器: 生成的分析提示词为空，将返回'分析内容为空'的决策。"
            )
            return SecretaryDecision(
                should_reply=False,
                reply_strategy="分析内容为空",
                topic="未知",
                alias=alias,
            )

        response_text = ""
        try:
            response_text = await self._call_ai_model(prompt, chat_id)
            # 调用新方法解析和验证响应，并传递 alias
            return self._parse_response(response_text, alias)
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
            should_reply=False, reply_strategy="分析失败", topic="未知", alias=alias
        )

    def _parse_and_validate_decision(
        self, response_text: str, alias: str
    ) -> SecretaryDecision:
        """解析并验证来自AI的响应文本，构建SecretaryDecision对象"""

        # 定义SecretaryDecision的字段要求
        required_fields = ["should_reply", "reply_strategy", "topic", "reply_target"]
        optional_fields = ["needs_search", "is_questioned", "is_interesting"]

        # 使用JsonParser提取JSON数据
        try:
            decision_data = self.json_parser.extract_json(
                text=response_text,
                required_fields=required_fields,
                optional_fields=optional_fields,
            )
        except Exception as e:
            logger.warning(
                f"AngelHeart分析器: JsonParser提取JSON时发生异常: {e}. 原始响应: {response_text[:200]}..."
            )
            decision_data = None

        # 如果JsonParser未能提取到有效的JSON
        if decision_data is None:
            logger.warning(
                f"AngelHeart分析器: JsonParser无法从响应中提取有效的JSON。原始响应: {response_text[:200]}..."
            )
            # 返回一个默认的不参与决策
            return SecretaryDecision(
                should_reply=False,
                reply_strategy="分析内容无有效JSON",
                topic="未知",
                alias=alias,
            )

        # 对来自 AI 的 JSON 做健壮性处理，防止字段为 null 或类型不符合导致 pydantic 校验失败
        raw = decision_data
        # 解析 should_reply，兼容 bool、数字、字符串等形式
        should_reply_raw = raw.get("should_reply", False)
        if isinstance(should_reply_raw, bool):
            should_reply = should_reply_raw
        elif isinstance(should_reply_raw, (int, float)):
            should_reply = bool(should_reply_raw)
        elif isinstance(should_reply_raw, str):
            should_reply = should_reply_raw.lower() in ("true", "1", "yes", "是", "对")
        else:
            should_reply = False

        # 解析 is_questioned
        is_questioned_raw = raw.get("is_questioned", False)
        if isinstance(is_questioned_raw, bool):
            is_questioned = is_questioned_raw
        elif isinstance(is_questioned_raw, (int, float)):
            is_questioned = bool(is_questioned_raw)
        elif isinstance(is_questioned_raw, str):
            is_questioned = is_questioned_raw.lower() in (
                "true",
                "1",
                "yes",
                "是",
                "对",
            )
        else:
            is_questioned = False

        # 解析 is_interesting
        is_interesting_raw = raw.get("is_interesting", False)
        if isinstance(is_interesting_raw, bool):
            is_interesting = is_interesting_raw
        elif isinstance(is_interesting_raw, (int, float)):
            is_interesting = bool(is_interesting_raw)
        elif isinstance(is_interesting_raw, str):
            is_interesting = is_interesting_raw.lower() in (
                "true",
                "1",
                "yes",
                "是",
                "对",
            )
        else:
            is_interesting = False

        # 提取其他字段
        reply_strategy = str(raw.get("reply_strategy") or "未知策略")
        topic = str(raw.get("topic") or "未知话题")
        reply_target = str(raw.get("reply_target") or "")

        # 创建决策对象
        decision = SecretaryDecision(
            should_reply=should_reply,
            is_questioned=is_questioned,
            is_interesting=is_interesting,
            reply_strategy=reply_strategy,
            topic=topic,
            reply_target=reply_target,
            alias=alias,
        )

        # 代码校验和修正逻辑
        if (
            decision.should_reply
            and not decision.is_questioned
            and not decision.is_interesting
        ):
            logger.warning(
                "AngelHeart分析器: AI判断有矛盾 - should_reply=true 但没有触发原因，强制设为不回复"
            )
            decision.should_reply = False
            decision.reply_strategy = "继续观察"

        return decision

    def _format_conversation_history(self, conversations: List[Dict]) -> str:
        """
        格式化对话历史，生成统一的日志式格式。

        Args:
            conversations (List[Dict]): 包含对话历史的字典列表。

        Returns:
            str: 格式化后的对话历史字符串。
        """
        # Phase 3: 增加空数据保护机制 - 开始
        # 防止空数据导致崩溃的保护机制
        if not conversations:
            return ""
        # Phase 3: 增加空数据保护机制 - 结束

        lines = []
        # 定义历史与新消息的分隔符对象
        SEPARATOR_OBJ = {"role": "system", "content": "history_separator"}

        # 遍历最近的 MAX_CONVERSATION_LENGTH 条对话
        for conv in conversations[-self.MAX_CONVERSATION_LENGTH :]:
            # 确保 conv 是一个字典
            if not isinstance(conv, dict):
                logger.warning(f"跳过非字典类型的对话项: {type(conv)}")
                continue

            # 检查是否遇到分隔符
            if conv == SEPARATOR_OBJ:
                lines.append("\n--- 以上是历史消息，仅作为策略参考，不需要回应 ---\n")
                lines.append(
                    "\n--- 后续的最新对话，你需要分辨出里面的人是不是在对你说话 ---\n"
                )
                continue  # 跳过分隔符本身，不添加到最终输出

            # 使用新的辅助方法格式化单条消息
            formatted_message = self._format_single_message(conv)
            lines.append(formatted_message)

        # 将所有格式化后的行连接成一个字符串并返回
        return "\n".join(lines)

    def _format_single_message(self, conv: Dict) -> str:
        """
        格式化单条消息，生成统一的日志式格式。

        Args:
            conv (Dict): 包含消息信息的字典。

        Returns:
            str: 格式化后的消息字符串。
        """
        role = conv.get("role")
        content = conv.get("content", "")

        if role == "assistant":
            # 助理消息格式: [助理]\n[内容: 文本]\n{content}
            formatted_content = convert_content_to_string(content)
            return f"[助理]\n[内容: 文本]\n{formatted_content}"
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
