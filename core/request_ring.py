"""
AngelHeart 插件 - 最近若干次请求的快照

排查请求体层面的问题（同一 call_id 出现两条回执、回执缺失、角色序列错位）时，
需要看的就是那一次请求到底发了什么。

全局只留最近 10 次，不分会话：要的正是一次报错前面那几轮的前后因果。
因为总量小，消息存原文，不做摘要；只有图片块里的内联二进制换成大小标记，
否则一张图几兆，十条就吃掉几十兆内存。

两个边界必须清楚：
1. 快照记的是插件重建之后的请求体（system + contexts + 当前提示词），
   框架之后还会做上下文压缩、图片预处理与 skills_like 的二次请求，这些拿不到。
2. 只在内存，进程重启即丢；不写磁盘，避免把用户原话留在文件里。
"""

from __future__ import annotations

import copy
import threading
import time
from collections import deque
from typing import Any, Callable, Dict, Iterable, List, Optional

# 全局保留的最近请求条数
RECENT_LIMIT = 10

# 图片内容块的类型名
_IMAGE_PART_TYPES = {"image_url", "image", "input_image"}
_TEXT_PART_TYPES = {"text", "input_text"}


def _content_to_text(content: Any) -> str:
    """把消息内容压成纯文本，只用于算字数。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                ptype = str(part.get("type") or "")
                if ptype in _TEXT_PART_TYPES:
                    parts.append(str(part.get("text") or ""))
                elif ptype in _IMAGE_PART_TYPES:
                    parts.append("[图片]")
                else:
                    parts.append(f"[{ptype}]" if ptype else "[未知块]")
            else:
                parts.append(str(part))
        return "".join(parts)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        ptype = content.get("type")
        return f"[{ptype}]" if ptype else "[未知块]"
    return str(content)


def _inline_url_placeholder(url: str) -> Optional[str]:
    """内联二进制换成大小标记；短链接与本地路径返回 None 表示原样保留。"""
    if not url.startswith(("data:", "base64://")):
        return None
    if url.startswith("data:"):
        # data:image/jpeg;base64,xxxx 里 base64 约占七成
        approx_kb = max(1, int(len(url) * 0.7 / 1024))
    else:
        approx_kb = max(1, int(len(url) / 1024))
    return f"[内联图片 {approx_kb}KB]"


def _sanitize_image_part(part: Dict) -> Dict:
    """图片块：内联二进制换大小标记，其余字段照抄。"""
    for holder_key in ("image_url", "image"):
        holder = part.get(holder_key)
        if isinstance(holder, dict):
            url = str(holder.get("url") or "")
            placeholder = _inline_url_placeholder(url)
            if placeholder is not None:
                cleaned = copy.deepcopy(part)
                cleaned[holder_key] = dict(holder)
                cleaned[holder_key]["url"] = placeholder
                return cleaned
    return copy.deepcopy(part)


def _sanitize_part(part: Any) -> Any:
    """内容块：文本原样保留，只处理图片。"""
    if not isinstance(part, dict):
        return copy.deepcopy(part)
    if str(part.get("type") or "") in _IMAGE_PART_TYPES:
        return _sanitize_image_part(part)
    cleaned: Dict[str, Any] = {}
    for key, value in part.items():
        if key in ("image_url", "image") and isinstance(value, dict):
            cleaned[key] = _sanitize_image_part({key: value}).get(key, copy.deepcopy(value))
        else:
            cleaned[key] = copy.deepcopy(value)
    return cleaned


def _sanitize_message(message: Dict) -> Dict:
    """复制一条消息用于留档：不碰原对象，只替换内联图片的二进制。"""
    cleaned: Dict[str, Any] = {}
    for key, value in message.items():
        if key == "content":
            cleaned[key] = _sanitize_content(value)
        else:
            cleaned[key] = copy.deepcopy(value)
    return cleaned


def _sanitize_content(content: Any) -> Any:
    if isinstance(content, list):
        return [_sanitize_part(part) for part in content]
    return copy.deepcopy(content)


def _tool_calls_of(message: Dict) -> List[Dict[str, str]]:
    raw = message.get("tool_calls")
    if not isinstance(raw, list):
        return []
    calls: List[Dict[str, str]] = []
    for call in raw:
        if not isinstance(call, dict):
            continue
        name = ""
        function = call.get("function")
        if isinstance(function, dict):
            name = str(function.get("name") or "")
        calls.append({"id": str(call.get("id") or ""), "name": name})
    return calls


def inspect_tool_pairing(messages: Iterable[Any]) -> Dict[str, Any]:
    """核对 tool_call 与 tool 回执的配对情况，只报结果不做修正。

    返回四类问题与一个总判定：
    - missing：有调用没有对应回执
    - orphan：有回执找不到对应调用
    - duplicate_call：同一个 call_id 被调用多次
    - duplicate_result：同一个 call_id 有多条回执
    """
    calls: Dict[str, int] = {}
    results: Dict[str, int] = {}
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        for call in _tool_calls_of(message):
            call_id = call["id"]
            if call_id:
                calls[call_id] = calls.get(call_id, 0) + 1
        result_id = str(message.get("tool_call_id") or "")
        if result_id:
            results[result_id] = results.get(result_id, 0) + 1

    missing = sorted(cid for cid in calls if cid not in results)
    orphan = sorted(cid for cid in results if cid not in calls)
    duplicate_call = sorted(cid for cid, count in calls.items() if count > 1)
    duplicate_result = sorted(cid for cid, count in results.items() if count > 1)
    return {
        "ok": not (missing or orphan or duplicate_call or duplicate_result),
        "missing": missing,
        "orphan": orphan,
        "duplicate_call": duplicate_call,
        "duplicate_result": duplicate_result,
    }


class RequestRing:
    """全局最近若干次请求的环形队列。

    秘书与助理共用同一条队列，用 source 区分；每条另记 chat_id，
    方便在混合队列里认出是哪个会话。不分会话分桶：只留最近 N 次。
    """

    def __init__(
        self,
        limit: int = RECENT_LIMIT,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._limit = max(1, int(limit))
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._items: "deque[Dict[str, Any]]" = deque(maxlen=self._limit)
        self._seq = 0

    def record(
        self,
        chat_id: str,
        source: str,
        messages: Optional[List[Dict]] = None,
        model: str = "",
    ) -> Dict[str, Any]:
        """记录一次请求，返回该快照。留档为副本，不改动传入的消息。"""
        payload = [message for message in (messages or []) if isinstance(message, dict)]
        stored = [_sanitize_message(message) for message in payload]
        now = self._clock()
        with self._lock:
            self._seq += 1
            snapshot = {
                "seq": self._seq,
                "ts": now,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "chat_id": str(chat_id or ""),
                "source": str(source or ""),
                "model": str(model or ""),
                "message_count": len(stored),
                "total_chars": sum(len(_content_to_text(m.get("content"))) for m in stored),
                "role_sequence": [str(m.get("role") or "") for m in stored],
                "tool_calls": sum(len(_tool_calls_of(m)) for m in stored),
                "tool_results": sum(1 for m in stored if m.get("tool_call_id")),
                "pairing": inspect_tool_pairing(payload),
                "messages": stored,
            }
            self._items.append(snapshot)
            return copy.deepcopy(snapshot)

    def recent(
        self, chat_id: Optional[str] = None, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """全局最近的请求，时间倒序；给 chat_id 时只筛出该会话的。"""
        with self._lock:
            items = list(self._items)
        chat = str(chat_id or "")
        if chat:
            items = [item for item in items if item["chat_id"] == chat]
        if limit and int(limit) > 0:
            items = items[-int(limit):]
        return copy.deepcopy(list(reversed(items)))

    def chats(self) -> List[Dict[str, Any]]:
        """当前这几条覆盖了哪些会话，各自几条。"""
        with self._lock:
            items = list(self._items)
        counts: Dict[str, int] = {}
        for item in items:
            chat = item["chat_id"]
            counts[chat] = counts.get(chat, 0) + 1
        return [{"chat_id": chat, "count": count} for chat, count in counts.items()]

    def clear(self) -> None:
        """清空全部快照。"""
        with self._lock:
            self._items.clear()

    @property
    def limit(self) -> int:
        return self._limit
