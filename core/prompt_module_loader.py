"""
提示词加载器
负责加载三份提示词文档

- system_prompt.md：群聊参与判定，作为第一条用户提示词
- output_contract.md：输出字段与格式要求，固定不变
- conversation_template.md：待分析的对话记录模板，固定不变
"""
from pathlib import Path
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)


class PromptModuleLoader:
    """提示词加载器

    系统提示词由会话人格提供（由 AstrBot 人格系统决定），
    本加载器只负责插件自己的规则与输出契约。
    """

    def __init__(self):
        """初始化加载器并加载提示词文档"""
        self.prompts_path = Path(__file__).parent.parent / "prompts"
        self.system_prompt = ""
        self.output_contract = ""
        self.conversation_template = ""
        self.load_all_modules()

    def load_all_modules(self):
        """一次性加载提示词文档到内存"""
        documents = (
            ("system_prompt", "system_prompt.md"),
            ("output_contract", "output_contract.md"),
            ("conversation_template", "conversation_template.md"),
        )

        for attr, name in documents:
            try:
                file_path = self.prompts_path / name
                setattr(self, attr, file_path.read_text(encoding="utf-8"))
                logger.debug(f"成功加载提示词文件: {name}")
            except Exception as e:
                setattr(self, attr, "")
                logger.error(f"加载提示词文件 {name} 时出错: {e}")

    def build_output_contract(self, is_reasoning_model: bool = False) -> str:
        """生成输出契约

        Args:
            is_reasoning_model: 分析模型是否自带思维链

        Returns:
            str: 已确定输出格式要求的输出契约
        """
        if is_reasoning_model:
            # 推理模型自带思维链，不再要求额外报告
            report_rule = "直接输出下面的 JSON 对象，不需要任何分析报告或思考过程。"
        else:
            report_rule = (
                "请先输出完整的分析和推理报告，展示你的思考过程。"
                "在报告的最后，严格按照下面的 JSON 结构输出一个 JSON 对象。"
            )

        contract = self.output_contract.replace("{output_report_rule}", report_rule)
        logger.debug(f"构建输出契约，总长度: {len(contract)}")
        return contract

    def reload_modules(self):
        """重新加载提示词文档"""
        logger.info("重新加载提示词文档...")
        self.system_prompt = ""
        self.output_contract = ""
        self.conversation_template = ""
        self.load_all_modules()
        logger.info("提示词文档重新加载完成")
