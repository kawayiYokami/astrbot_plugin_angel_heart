"""群聊工具可见性回归：秘书轻量/助理完整、整理条件保留"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
_PARENT = str(PLUGIN_ROOT.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

for _mod_path in (
    "astrbot",
    "astrbot.api",
    "astrbot.api.event",
    "astrbot.core",
    "astrbot.core.agent",
    "astrbot.core.agent.message",
    "astrbot.core.message",
    "astrbot.core.message.components",
    "astrbot.core.star",
    "astrbot.core.star.context",
):
    sys.modules.setdefault(_mod_path, types.ModuleType(_mod_path))

sys.modules["astrbot.api"].logger = MagicMock()
sys.modules["astrbot.core.message.components"].Image = type("Image", (), {})
sys.modules["astrbot.core.message.components"].At = type("At", (), {})
sys.modules["astrbot.core.message.components"].File = type("File", (), {})
sys.modules["astrbot.core.star.context"].Context = type("Context", (), {})
sys.modules["astrbot.core.agent.message"].ImageURLPart = type(
    "ImageURLPart",
    (),
    {"__init__": lambda self, image_url: setattr(self, "image_url", SimpleNamespace(**image_url))},
)

from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger
from astrbot_plugin_angel_heart.core.utils.context_utils import partition_dialogue, partition_dialogue_raw
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk
from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger


def _ledger(tmp_path, forgetting_timeout=86400, max_tokens=2000, content_retain=300, tool_retain=500):
    cfg = ConfigManager({})
    cfg._config.setdefault("context_compression", {})["max_conversation_tokens"] = max_tokens
    cfg._config["context_compression"]["content_retain_tokens"] = content_retain
    cfg._config["context_compression"]["tool_retain_tokens"] = tool_retain
    cfg._config["context_compression"]["forgetting_timeout"] = forgetting_timeout
    cfg._config["context_compression"]["context_compression_threshold"] = 0.82
    lg = ConversationLedger(cfg, Path(tmp_path), astr_context=None)
    return lg, cfg


def _msg(mid, role, content, ts, extra=None):
    m = {"source_message_id": mid, "role": role, "content": content, "timestamp": ts, "sender_id": "u1", "sender_name": "红豆", "chat_id": "aiocqhttp:GroupMessage:10000"}
    if extra:
        m.update(extra)
    return m


class TestSecretaryVsAssistant:
    def test_secretary_drops_tools_assistant_keeps(self, tmp_path):
        lg, _ = _ledger(tmp_path)
        chat = "aiocqhttp:GroupMessage:10000"
        try:
            lg.add_message(chat, _msg("m1", "user", [{"type": "text", "text": "hi"}], 1000))
            lg.add_message(chat, _msg("m2", "assistant", "call", 1001, extra={"tool_calls": [{"function": {"name": "angel_image_generate"}}]}))
            lg.add_message(chat, _msg("m3", "tool", "path=/tmp/a.png", 1002))
            hist, recent, _ = partition_dialogue(lg, chat, "m3")
            assert "tool" not in [r["role"] for r in recent]
            assert not any(r.get("tool_calls") for r in recent)
            hist2, recent2, _ = partition_dialogue_raw(lg, chat, "m3")
            assert "tool" in [r["role"] for r in recent2]
        finally:
            lg.close()

    def test_group_two_round_tool_traceable(self, tmp_path):
        """群聊两轮：上一轮 tool 链在下一轮 raw 可见，秘书不可见"""
        lg, _ = _ledger(tmp_path)
        chat = "aiocqhttp:GroupMessage:10000"
        try:
            lg.add_message(chat, _msg("u1", "user", [{"type": "text", "text": "画只猫"}], 1000))
            lg.add_message(chat, _msg("a1", "assistant", "tool_call", 1001, extra={"tool_calls": [{"id": "c1", "function": {"name": "angel_image_generate", "arguments": "{}"}}]}))
            lg.add_message(chat, _msg("t1", "tool", '{"images":[{"absolute_path":"/data/generated-images/20260904/abc.png"}]}', 1002))
            lg.add_message(chat, _msg("a2", "assistant", "好了", 1003))
            lg.add_message(chat, _msg("u2", "user", [{"type": "text", "text": "你刚才发的图路径是？"}], 1004))
            # 旧逻辑（压缩切片）看不到
            _, recent_old, _ = partition_dialogue(lg, chat, "u2")
            assert not any(r["role"] == "tool" for r in recent_old)
            # 新逻辑（raw）必须看到
            _, recent_new, _ = partition_dialogue_raw(lg, chat, "u2")
            assert any("abc.png" in str(r.get("content", "")) for r in recent_new)
        finally:
            lg.close()

    def test_group_rewrite_uses_raw_at_same_boundary(self, tmp_path):
        """群聊助理复用同一 boundary 用 raw 重算，同边界不扩窗且包含工具"""
        # 用真实 ledger + FrontDesk 验证
        lg, cfg = _ledger(tmp_path)
        chat = "aiocqhttp:GroupMessage:10000"
        try:
            # 准备 ledger：两轮 + 工具
            lg.add_message(chat, _msg("u1", "user", [{"type": "text", "text": "画只猫"}], 1000))
            lg.add_message(chat, _msg("a1", "assistant", "tool_call", 1001, extra={"tool_calls": [{"id": "c1", "function": {"name": "angel_image_generate"}}]}))
            lg.add_message(chat, _msg("t1", "tool", "path /data/generated-images/20260904/abc.png", 1002))
            lg.add_message(chat, _msg("a2", "assistant", "好了", 1003))
            lg.add_message(chat, _msg("u2", "user", [{"type": "text", "text": "继续"}], 1004))
            # 额外再塞一条未来的消息，不该被边界包含
            lg.add_message(chat, _msg("u3", "user", [{"type": "text", "text": "未来消息"}], 1005))

            config = MagicMock()
            config.for_chat.return_value = config
            config.alias = "fairy"
            config.image_caption_provider_id = ""
            config.focus_instructions = ""
            config.normal_reply_max_chars = 20
            config.focus_reply_max_chars = 200
            angel = MagicMock()
            angel.work_ledger = WorkLedger()
            angel.conversation_ledger = lg
            angel.astr_context = MagicMock()
            fd = FrontDesk(config, angel)
            fd._provider_supports_images = MagicMock(return_value=False)
            fd.filter_images_for_provider = MagicMock(side_effect=lambda _cid, contexts: contexts)

            class E:
                unified_msg_origin = chat
                message_str = ""
                message_obj = SimpleNamespace(message_id="u2")
                def __init__(self):
                    self._extras = {"angelheart_decision_context": {"recent_dialogue": [{"source_message_id": "u2"}], "historical_context": [], "boundary_message_id": "u2", "boundary_ts": 1004.0}}
                def get_extra(self, k, d=None): return self._extras.get(k, d)
                def set_extra(self, k, v): self._extras[k]=v

            event = E()
            # 应取到截至 u2 的 raw（含 t1），不含 u3
            recent, hist, _ = fd._get_decision_context_for_rewrite(chat, event)
            assert any(r.get("source_message_id")=="u2" for r in recent)
            assert not any(r.get("source_message_id")=="u3" for r in recent)
            assert any(r["role"]=="tool" for r in recent)
        finally:
            lg.close()


class TestGroupOrganizeConditional:
    def test_token_trigger_retains_tail_tools(self, tmp_path):
        lg, _ = _ledger(tmp_path, forgetting_timeout=86400, max_tokens=800, content_retain=200, tool_retain=500)
        chat = "aiocqhttp:GroupMessage:10000"
        try:
            base = time.time()
            # 塞满正文触 token
            for i in range(20):
                lg.add_message(chat, _msg(f"m{i}", "user", [{"type": "text", "text": "x"*200}], base + i))
            # 尾部工具
            lg.add_message(chat, _msg("ta", "assistant", "c", base+100, extra={"tool_calls":[{"function":{"name":"angel_image_generate"}}]}))
            lg.add_message(chat, _msg("tt", "tool", "tail tool result with path /data/generated-images/a.png", base+101))
            lg._last_compression_time[chat] = time.time()  # 保证未超时
            assert not lg._is_forgetting_timeout(chat)
            lg.organize_context(chat, mode="group_rule")
            msgs = lg.get_all_messages(chat)
            assert any(m["role"]=="tool" for m in msgs), "未超时应保留尾部工具"
        finally:
            lg.close()

    def test_forgetting_timeout_drops_tools(self, tmp_path):
        lg, _ = _ledger(tmp_path, forgetting_timeout=86400, max_tokens=20000, content_retain=200, tool_retain=500)
        chat = "aiocqhttp:GroupMessage:10000"
        try:
            base = time.time()
            for i in range(5):
                lg.add_message(chat, _msg(f"m{i}", "user", [{"type":"text","text":"old"}], base+i))
            lg.add_message(chat, _msg("ta", "assistant", "c", base+10, extra={"tool_calls":[{"function":{"name":"x"}}]}))
            lg.add_message(chat, _msg("tt", "tool", "tool", base+11))
            # 人为超时
            lg._last_compression_time[chat] = base - 90000
            assert lg._is_forgetting_timeout(chat)
            lg.organize_context(chat, mode="group_rule")
            msgs = lg.get_all_messages(chat)
            assert not any(m["role"]=="tool" for m in msgs), "超时应全清工具"
        finally:
            lg.close()

    def test_group_enter_always_drops_tools(self, tmp_path):
        lg, _ = _ledger(tmp_path, forgetting_timeout=86400)
        chat = "aiocqhttp:GroupMessage:10000"
        try:
            base = time.time()
            for i in range(5):
                lg.add_message(chat, _msg(f"m{i}", "user", [{"type":"text","text":"z"*50}], base+i))
            lg.add_message(chat, _msg("ta", "assistant", "c", base+10, extra={"tool_calls":[{"function":{"name":"x"}}]}))
            lg.add_message(chat, _msg("tt", "tool", "tool", base+11))
            lg.add_message(chat, _msg("now", "user", [{"type":"text","text":"enter"}], base+20))
            lg._last_compression_time[chat] = time.time()  # 未超时
            lg.organize_on_group_enter(chat, keep_from_timestamp=base+20)
            msgs = lg.get_all_messages(chat)
            assert not any(m["role"]=="tool" for m in msgs), "入场整理应全清"
            # 且只保留触发后
            body = [m for m in msgs if m.get("kind")!="context_summary"]
            assert all(m["timestamp"] >= base+20 for m in body)
        finally:
            lg.close()
