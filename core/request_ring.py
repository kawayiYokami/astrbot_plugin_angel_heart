"""
AngelHeart 插件 - 最近若干次请求的摘要快照

排查请求体层面的问题（同一 call_id 出现两条回执、回执缺失、角色序列错位）时，
需要的是「这一次请求到底带了哪些消息」，而不是消息原文。

本模块只保留摘要：role、字数、预览、tool_calls 的 call_id 与工具名、tool_call_id，
外加按消息顺序得出的角色序列与配对检查结果。

两个边界必须清楚：
1. 快照记的是插件重建之后的请求体（system + contexts + 当前提示词），
   框架之后还会做上下文压缩、图片预处理与 skills_like 的二次请求，这些拿不到。
2. 只在内存，进程重启即丢；不写磁盘，避免把用户原话留在文件里。
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable, Dict, Iterable, List, Optional

# 每条消息保留的预览字数
PREVIEW_CHARS = 120
# 每个会话保留的最近请求条数
PER_CHAT_LIMIT = 10
# 最多跟踪多少个会话，防止长时间运行后会话表无限增长
MAX_CHATS = 200

# 图片类内容块的类型名，摘要里统一换成占位标记
_IMAGE_PART_TYPES = {"image_url", "image", "input_image"}
_TEXT_PART_TYPES = {"text", "input_text"}


def _content_to_text(content: Any) -> str:
    """把消息内容压成纯文本：多模态块只保留文字与类型标记。"""
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


def _tool_calls_of(message: Dict) -> List[Dict[str, str]]:
    """取出消息里的工具调用，只留 call_id 与工具名。"""
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


def summarize_message(message: Dict, preview_chars: int = PREVIEW_CHARS) -> Dict[str, Any]:
    """把一条消息压成摘要，不保留原文。"""
    text = _content_to_text((message or {}).get("content"))
    limit = max(0, int(preview_chars))
    preview = text[:limit]
    if limit and len(text) > limit:
        preview = f"{preview}…"
    return {
        "role": str((message or {}).get("role") or ""),
        "chars": len(text),
        "preview": preview,
        "tool_calls": _tool_calls_of(message or {}),
        "tool_call_id": str((message or {}).get("tool_call_id") or ""),
    }


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
    """按会话保存最近若干次请求摘要的环形队列。

    秘书与助理共用同一条队列，用 source 区分：排查时最需要的是
    「秘书这一判」到「助理这一发」的先后关系，分两份内存反而看不到因果。
    """

    def __init__(
        self,
        per_chat_limit: int = PER_CHAT_LIMIT,
        max_chats: int = MAX_CHATS,
        preview_chars: int = PREVIEW_CHARS,
        clock: Optional[Callable[[], float]] = None,
    ):
        self._limit = max(1, int(per_chat_limit))
        self._max_chats = max(1, int(max_chats))
        self._preview_chars = max(0, int(preview_chars))
        self._clock = clock or time.time
        self._lock = threading.Lock()
        # OrderedDict 用来记录会话的最近活跃顺序，超出上限时淘汰最久未更新的
        self._rings: "OrderedDict[str, deque]" = OrderedDict()
        self._seq = 0

    def record(
        self,
        chat_id: str,
        source: str,
        messages: Optional[List[Dict]] = None,
        model: str = "",
    ) -> Dict[str, Any]:
        """记录一次请求的摘要快照，返回该快照。"""
        chat = str(chat_id or "")
        payload = [message for message in (messages or []) if isinstance(message, dict)]
        summarized = [summarize_message(message, self._preview_chars) for message in payload]
        now = self._clock()
        with self._lock:
            self._seq += 1
            ring = self._rings.get(chat)
            if ring is None:
                ring = deque(maxlen=self._limit)
                self._rings[chat] = ring
                self._evict_locked()
            else:
                self._rings.move_to_end(chat)
            snapshot = {
                "seq": self._seq,
                "ts": now,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "chat_id": chat,
                "source": str(source or ""),
                "model": str(model or ""),
                "message_count": len(summarized),
                "total_chars": sum(item["chars"] for item in summarized),
                "role_sequence": [item["role"] for item in summarized],
                "tool_calls": sum(len(item["tool_calls"]) for item in summarized),
                "tool_results": sum(1 for item in summarized if item["tool_call_id"]),
                "pairing": inspect_tool_pairing(payload),
                "messages": summarized,
            }
            ring.append(snapshot)
            return copy.deepcopy(snapshot)

    def recent(self, chat_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """取某会话最近的请求摘要，时间倒序。"""
        chat = str(chat_id or "")
        with self._lock:
            ring = self._rings.get(chat)
            if not ring:
                return []
            items = list(ring)
        if limit and int(limit) > 0:
            items = items[-int(limit):]
        return copy.deepcopy(list(reversed(items)))

    def chats(self) -> List[Dict[str, Any]]:
        """有快照的会话列表，最近活跃在前。"""
        with self._lock:
            entries = [
                {"chat_id": chat, "count": len(ring)}
                for chat, ring in self._rings.items()
            ]
        return list(reversed(entries))

    def clear(self, chat_id: Optional[str] = None) -> None:
        """清空指定会话或全部快照。"""
        with self._lock:
            if chat_id is None:
                self._rings.clear()
            else:
                self._rings.pop(str(chat_id or ""), None)

    def _evict_locked(self) -> None:
        """会话数超出上限时淘汰最久未更新的，至少留一个。"""
        while len(self._rings) > self._max_chats and len(self._rings) > 1:
            self._rings.popitem(last=False)
