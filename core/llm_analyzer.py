from typing import List, Dict, Optional
import json

from astrbot.api import logger
from astrbot.core.star.context import Context
from ..models.analysis_result import SecretaryDecision

class LLMAnalyzer:
    """
    LLM分析器 - 执行实时分析和标注
    采用两级AI协作体系：
    1. 轻量级AI（分析员）：低成本、快速地判断是否需要回复。
    2. 重量级AI（专家）：在需要时，生成高质量的回复。
    """

    def __init__(self, analyzer_model_name: str, context, strategy_guide: str = None):
        self.analyzer_model_name = analyzer_model_name
        self.context = context  # 存储 context 对象，用于动态获取 provider
        self.strategy_guide = strategy_guide or ""  # 存储策略指导文本
        if not self.analyzer_model_name:
            logger.warning("AngelHeart的分析模型未配置，功能将受限。" )

    async def analyze_and_decide(self, conversations: List[Dict]) -> SecretaryDecision:
        """分析对话历史，做出结构化的决策 (JSON)"""
        if not self.analyzer_model_name:
            logger.debug("AngelHeart分析器: 分析模型未配置, 跳过分析。" )
            # 返回一个默认的不参与决策
            return SecretaryDecision(
                should_reply=False,
                reply_strategy="未配置",
                topic="未知"
            )

        # 1. 调用轻量级AI进行分析
        logger.debug("AngelHeart分析器: 准备调用轻量级AI进行分析...")
        prompt = self._build_analysis_prompt(conversations)

        # 动态获取 provider
        provider = self.context.get_provider_by_id(self.analyzer_model_name)
        if not provider:
            logger.warning(f"AngelHeart分析器: 未找到名为 '{self.analyzer_model_name}' 的分析模型提供商。")
            # 返回一个默认的不参与决策
            return SecretaryDecision(
                should_reply=False,
                reply_strategy="模型未找到",
                topic="未知"
            )

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                token = await provider.text_chat(prompt=prompt)
                response_text = token.completion_text.strip()

                # 尝试提取可能被包裹在代码块中的JSON
                if response_text.startswith("```json"):
                    response_text = response_text.split("```json")[1].split("```")[0].strip()
                elif response_text.startswith("```"):
                    response_text = response_text.split("```")[1].strip()

                # 解析JSON
                decision_data = json.loads(response_text)

                decision = SecretaryDecision(
                    should_reply=decision_data["should_reply"],
                    reply_strategy=decision_data["reply_strategy"],
                    topic=decision_data["topic"]
                )

                logger.debug(f"AngelHeart分析器: 轻量级AI分析完成。决策: {decision}")
                return decision

            except json.JSONDecodeError as e:
                logger.warning(f"AngelHeart分析器: AI返回了无效的JSON (尝试 {attempt + 1}/{max_retries + 1}): {e}. 原始响应: {response_text[:200]}...")
                if attempt == max_retries:
                    logger.error("AngelHeart分析器: JSON解析失败，已达到最大重试次数。")
                    break
                # 在下一次尝试前，可以考虑修改prompt以更明确地要求JSON格式
                # 这里我们简单地重试
                continue
            except KeyError as e:
                logger.warning(f"AngelHeart分析器: AI返回的JSON缺少必要字段 (尝试 {attempt + 1}/{max_retries + 1}): {e}. 原始响应: {response_text}")
                if attempt == max_retries:
                    logger.error("AngelHeart分析器: JSON字段缺失，已达到最大重试次数。")
                    break
                continue
            except Exception as e:
                logger.error(f"💥 AngelHeart分析器: 轻量级AI分析失败 (尝试 {attempt + 1}/{max_retries + 1}): {e}", exc_info=True)
                if attempt == max_retries:
                    break
                continue

        # 如果所有尝试都失败了，返回一个默认的不参与决策
        logger.error("AngelHeart分析器: 所有尝试均失败，返回默认决策。")
        return SecretaryDecision(
            should_reply=False,
            reply_strategy="分析失败",
            topic="未知"
        )

    def _build_analysis_prompt(self, conversations: List[Dict]) -> str:
        history_text = self._format_conversation_history(conversations)

        # 构建基础提示词
        base_prompt = f"""
你是一个高度智能的群聊分析员。你的任务是分析以下对话历史，并以JSON格式返回你的决策。

# 对话历史
{history_text}

# 决策要求
请分析以上对话，判断是否符合以下回复条件。如果不符合，请将 should_reply 设置为 false。
如果符合，请将 should_reply 设置为 true，并提供相应的回复策略和话题概括。
请注意，你只需要考虑最新的话题和最新的对话，不需要考虑已经过去的历史对话。
优先考虑最近的7条发言。
如果新的话题已经开始，停止分析旧话题的气氛。
一旦你得出结论，马上生成回复建议。

严格按照以下JSON格式返回你的分析结果。不要添加任何额外的解释。

{{
  "should_reply": <布尔值: 是否应该介入回复？>,
  "reply_strategy": "<字符串: 建议的回复策略，例如：缓和气氛、技术指导、表示共情等>",
  "topic": "<字符串: 对当前对话核心主题的精确概括>"
}}
"""

        # 如果有策略指导文本，则添加到提示词中
        if self.strategy_guide:
            base_prompt += f"\n# 回复策略指导\n请仅在以下情况才考虑回复：\n{self.strategy_guide}\n"

        return base_prompt

    def _format_conversation_history(self, conversations: List[Dict]) -> str:
        lines = []
        for conv in conversations[-50:]:
            # 确保 conv 是一个字典
            if not isinstance(conv, dict):
                logger.warning(f"跳过非字典类型的对话项: {type(conv)}")
                continue

            if content := str(conv.get('content', '')).strip():
                role = conv.get('role')
                # 修复字典访问错误，支持不同格式的对话数据结构
                if role == 'user':
                    # 尝试从不同的字段获取用户名称
                    user_name = conv.get('sender_name', conv.get('nickname', conv.get('metadata', {}).get('user_name', '成员')))
                else:
                    user_name = '你'
                lines.append(f"{user_name}: {content}")
        return '\n'.join(lines)
