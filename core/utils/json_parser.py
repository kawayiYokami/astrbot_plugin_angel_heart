"""
JSON 解析工具，从文本中提取 JSON 数据
"""

import json
from typing import Dict, Optional, Any, List

def _strip_code_fences(text: str) -> str:
    """去除 Markdown 代码块围栏"""
    if not text:
        return text
    # 仅移除围栏标记，不移除内部内容
    return text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()


def _find_json_candidates(text: str) -> List[str]:
    """扫描文本中的平衡大括号，返回 JSON 候选"""
    candidates: List[str] = []
    if not text:
        return candidates

    in_string = False
    escape = False
    depth = 0
    start_idx: Optional[int] = None

    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start_idx is not None:
                    candidates.append(text[start_idx : i + 1])
                    start_idx = None

    return candidates


class JsonParser:
    """
    高鲁棒性 JSON 解析器类。

    负责从 LLM 响应中提取 JSON 部分并转换为结构化数据。
    使用智能候选识别和评分机制，确保在各种情况下都能正确解析。
    """

    def __init__(self):
        pass

    def parse_llm_response(self, response_text: str) -> Optional[Dict[str, Any]]:
        """
        从 LLM 响应文本中解析出 feedback_data 字典。

        Args:
            response_text: LLM 的原始响应文本

        Returns:
            解析后的 feedback_data 字典或 None
        """
        # 尝试提取JSON数据
        json_data = self.extract_json(response_text)

        if json_data is None:
            return None

        # 从 JSON 中提取 feedback_data
        if "feedback_data" in json_data:
            feedback_data = json_data["feedback_data"]

            # 如果 feedback_data 是字符串，尝试再次解析
            if isinstance(feedback_data, str):
                try:
                    feedback_data = json.loads(feedback_data)
                except json.JSONDecodeError:
                    return None

            return feedback_data
        else:
            return json_data

    def extract_json(
        self,
        text: str,
        separator: str = "---JSON---",
        required_fields: Optional[List[str]] = None,
        optional_fields: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        从文本中提取 JSON 对象。

        Args:
            text: 包含 JSON 的字符串
            separator: 分隔符
            required_fields: 必需字段列表
            optional_fields: 可选字段列表

        Returns:
            提取的 JSON 对象或 None
        """
        if not isinstance(text, str):
            return None

        if not text.strip():
            return None

        # 1) 分隔符处理
        json_part = text
        if separator in text:
            parts = text.split(separator, 1)
            if len(parts) > 1:
                json_part = parts[1].strip()
            else:
                return None

        # 2) 去掉代码围栏
        json_part = _strip_code_fences(json_part)

        # 3) 扫描所有平衡的大括号候选
        candidates = _find_json_candidates(json_part)
        # 扫描候选

        # 4) 筛选与评分
        qualified_jsons = []
        for candidate_str in candidates:
            try:
                parsed_json = json.loads(candidate_str)
                if not isinstance(parsed_json, dict):
                    continue  # 只处理对象类型的JSON

                # 硬性条件：检查必须字段
                if required_fields:
                    if not all(field in parsed_json for field in required_fields):
                        continue

                # 计算分数
                score = 0
                if optional_fields:
                    score = sum(1 for field in optional_fields if field in parsed_json)

                qualified_jsons.append({"json": parsed_json, "score": score})

            except json.JSONDecodeError:
                continue  # 解析失败，不是有效的JSON，跳过

        if not qualified_jsons:
            return None

        # 5) 决策：选择分数最高的，同分则取最后的
        # 先按分数排序（稳定排序），然后取最后一个，这样就能保证在分数相同时，选择原文中位置更靠后的
        qualified_jsons.sort(key=lambda x: x["score"])
        best_json_item = qualified_jsons[-1]
        return best_json_item["json"]
