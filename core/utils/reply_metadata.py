"""Bounded message IDs for explicit reply links, independent of message contents."""

import re

MAX_REPLY_TARGETS = 4
MAX_SUMMARY_REPLY_RELATIONS = 16
_MESSAGE_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.:-]{0,63}\Z")
_EMPTY_IDS = frozenset({"", "0", "none", "null", "unknown"})


def normalize_reference_id(value) -> str:
    """Accept actual scalar IDs, never arbitrary objects or embedded markup."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    value = str(value).strip()
    if value.lower() in _EMPTY_IDS or not _MESSAGE_ID.fullmatch(value):
        return ""
    return value


def normalize_reply_targets(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        message_id = normalize_reference_id(value)
        if message_id and message_id not in result:
            result.append(message_id)
            if len(result) >= MAX_REPLY_TARGETS:
                break
    return result


def collect_reply_relations(messages) -> list[dict]:
    """Keep only the most recent explicit links; no quoted bodies or media."""
    relations = {}
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        inherited = message.get("reply_relations")
        candidates = (
            list(inherited[-MAX_SUMMARY_REPLY_RELATIONS:])
            if isinstance(inherited, list)
            else []
        )
        candidates.append(message)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            source = normalize_reference_id(candidate.get("source_message_id"))
            targets = normalize_reply_targets(candidate.get("reply_to_message_ids"))
            if not source or not targets:
                continue
            sender = normalize_reference_id(candidate.get("sender_id"))
            key = (source, sender)
            relations.pop(key, None)
            relations[key] = {
                "source_message_id": source,
                "reply_to_message_ids": targets,
                "sender_id": sender,
            }
            if len(relations) > MAX_SUMMARY_REPLY_RELATIONS:
                del relations[next(iter(relations))]
    return list(relations.values())


def format_reply_metadata(message: dict) -> str:
    """Show current IDs and reply target IDs with unambiguous ownership."""
    lines = []
    source = normalize_reference_id(message.get("source_message_id"))
    targets = normalize_reply_targets(message.get("reply_to_message_ids"))
    if source:
        lines.append(f"[本消息 ID: {source}]")
    if targets:
        lines.append(f"[本消息引用的目标消息 ID: {', '.join(targets)}]")
    if message.get("kind") in ("context_summary", "summary_context", "context_compaction"):
        for relation in collect_reply_relations([message]):
            sender = relation["sender_id"] or "未知"
            targets_text = ", ".join(relation["reply_to_message_ids"])
            lines.append(
                f"[历史引用关系: 发送者 ID={sender}; 消息 ID={relation['source_message_id']}; "
                f"引用目标消息 ID={targets_text}]"
            )
    return "\n".join(lines)
