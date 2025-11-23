"""
提示词模块加载器
用于动态加载和组装模块化的提示词文件
"""
from pathlib import Path
try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

class PromptModuleLoader:
    """提示词模块加载器

    负责加载modules目录下的所有模块文件，
    并根据配置动态组装成完整的提示词模板
    """

    def __init__(self):
        """初始化加载器并加载所有模块"""
        self.modules_path = Path(__file__).parent.parent / "prompts" / "modules"
        self.base_modules = {}
        self.load_all_modules()

    def load_all_modules(self):
        """一次性加载所有模块文件到内存"""
        module_names = [
            "identity.md", "behavior_rules.md", "decision_logic.md",
            "conversation_analysis.md", "strategy_generation.md",
            "instruction_prompts.md", "reasoning_prompts.md"
        ]

        for name in module_names:
            try:
                file_path = self.modules_path / name
                if file_path.exists():
                    self.base_modules[name] = file_path.read_text(encoding="utf-8")
                    logger.debug(f"成功加载模块: {name}")
                else:
                    logger.warning(f"模块文件 {name} 不存在")
            except Exception as e:
                logger.error(f"加载模块 {name} 时出错: {e}")

    def build_prompt_template(self, is_reasoning_model: bool = False) -> str:
        """根据配置动态组装完整提示词模板

        Args:
            is_reasoning_model: 是否使用推理模式（详细版本）

        Returns:
            str: 组装好的完整提示词模板
        """
        # 基础模块（两个版本共用）
        base_parts = [
            self.base_modules.get("identity.md", ""),
            self.base_modules.get("behavior_rules.md", ""),
            self.base_modules.get("decision_logic.md", ""),
            self.base_modules.get("conversation_analysis.md", ""),
            self.base_modules.get("strategy_generation.md", "")
        ]

        # 根据配置选择输出格式模块
        # 推理模型自带思维链，使用简单指令；普通模型需要详细推理提示
        output_module = "instruction_prompts.md" if is_reasoning_model else "reasoning_prompts.md"

        # 组装完整模板
        all_parts = base_parts + [self.base_modules.get(output_module, "")]

        # 过滤空字符串并组装
        valid_parts = [part for part in all_parts if part.strip()]
        template = "\n\n---\n\n".join(valid_parts)

        logger.debug(f"组装提示词模板，使用 {output_module}，总长度: {len(template)}")
        return template

    def reload_modules(self):
        """重新加载所有模块"""
        logger.info("重新加载提示词模块...")
        self.base_modules.clear()
        self.load_all_modules()
        logger.info("提示词模块重新加载完成")