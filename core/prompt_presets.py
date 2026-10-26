"""系统提示词预设。

预设固定为 prompts/system_prompt.md 的全文，不随配置变化：
用户要么用这份预设，要么完全自己写。
"""
from pathlib import Path

SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.md"


def load_preset_system_prompt() -> str:
    """读取内置系统提示词全文。

    每次调用直接读文件，保证热重载编辑后的内容立即生效。
    """
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


# 自定义输入框的初始提示词：用户点按钮一键填入，再按自己人设改。
INITIAL_CUSTOM_PROMPT = (
    "请根据你的人设，选择一个适合的参与决策。\n"
    "你是群聊中的普通一员，不是旁观者，也不是主角。\n"
    "- 有人明确提到你、向你提问或期待你回应时，优先参与。\n"
    "- 话题与你的人设相符、你能提供信息或活跃气氛时，可以主动加入一次；拿不准时先观察。\n"
    "- 话题敏感、有争议、火药味大，或没人需要你时，保持沉默。\n"
    "- 一次只做一种选择，简短有分寸，不刷屏，不重复打扰。"
)
