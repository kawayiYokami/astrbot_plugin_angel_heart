"""
消息正文命中工具。

关键词匹配只看本条消息的 Plain 正文，不看引用、昵称展示名或 @ 显示名。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence


def parse_pipe_phrases(raw: Any) -> List[str]:
    """解析 `|` 分隔短语，去掉空项并去重保序。"""
    if isinstance(raw, list):
        phrases = [str(item).strip() for item in raw]
    else:
        phrases = [part.strip() for part in str(raw or "").split("|")]
    return _dedupe_keep_order(phrases)


def parse_space_phrases(raw: Any) -> List[str]:
    """解析空格分隔短语，去掉空项并去重保序。"""
    if isinstance(raw, list):
        phrases = [str(item).strip() for item in raw]
    else:
        phrases = str(raw or "").split()
    return _dedupe_keep_order(phrases)


def _dedupe_keep_order(phrases: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for phrase in phrases:
        if not phrase or phrase in seen:
            continue
        seen.add(phrase)
        result.append(phrase)
    return result


def extract_plain_body_from_components(components: Sequence[Any] | None) -> str:
    """只拼接 Plain 组件文本，忽略 Reply / At / 图片等。"""
    if not components:
        return ""
    parts: List[str] = []
    for component in components:
        cls_name = component.__class__.__name__
        text = getattr(component, "text", None)
        if not isinstance(text, str) or not text:
            continue
        # 只认 Plain；Reply 虽也可能带 text，但那是引用正文，必须排除
        if cls_name == "Plain" or (
            "Plain" in cls_name and "Reply" not in cls_name and not hasattr(component, "message_str")
        ):
            parts.append(text)
    return "".join(parts).strip()


def match_phrases(body_text: str, phrases: Sequence[str], *, casefold: bool = False) -> List[str]:
    """返回正文中命中的短语列表；同一短语只记一次。"""
    if not body_text or not phrases:
        return []
    haystack = body_text.casefold() if casefold else body_text
    hits: List[str] = []
    seen = set()
    for phrase in phrases:
        needle = phrase.casefold() if casefold else phrase
        if not needle or needle in seen:
            continue
        if needle in haystack:
            seen.add(needle)
            hits.append(phrase)
    return hits


def build_message_hits(
    *,
    body_text: str,
    alias_phrases: Sequence[str],
    focus_phrases: Sequence[str],
    is_at_self: bool,
) -> List[Dict[str, str]]:
    """构建命中列表。type: at_self | alias | focus。"""
    hits: List[Dict[str, str]] = []
    if is_at_self:
        hits.append({"type": "at_self"})
    for phrase in match_phrases(body_text, alias_phrases, casefold=False):
        hits.append({"type": "alias", "phrase": phrase})
    for phrase in match_phrases(body_text, focus_phrases, casefold=True):
        hits.append({"type": "focus", "phrase": phrase})
    return hits


def build_message_metadata(
    *,
    body_text: str,
    alias_phrases: Sequence[str],
    focus_phrases: Sequence[str],
    is_at_self: bool,
) -> Dict[str, Any]:
    """构建消息 metadata：正文快照 + 命中列表。"""
    return {
        "body_text": body_text or "",
        "hits": build_message_hits(
            body_text=body_text or "",
            alias_phrases=alias_phrases,
            focus_phrases=focus_phrases,
            is_at_self=is_at_self,
        ),
    }


def metadata_has_hit(metadata: Dict[str, Any] | None, hit_type: str) -> bool:
    """metadata.hits 是否包含指定 type。"""
    if not isinstance(metadata, dict):
        return False
    hits = metadata.get("hits")
    if not isinstance(hits, list):
        return False
    for item in hits:
        if isinstance(item, dict) and item.get("type") == hit_type:
            return True
        if isinstance(item, str) and item == hit_type:
            return True
    return False


def metadata_hit_phrases(metadata: Dict[str, Any] | None, hit_type: str) -> List[str]:
    """取出指定 type 的命中短语。"""
    if not isinstance(metadata, dict):
        return []
    hits = metadata.get("hits")
    if not isinstance(hits, list):
        return []
    phrases: List[str] = []
    for item in hits:
        if not isinstance(item, dict):
            continue
        if item.get("type") != hit_type:
            continue
        phrase = str(item.get("phrase", "") or "").strip()
        if phrase:
            phrases.append(phrase)
    return phrases
