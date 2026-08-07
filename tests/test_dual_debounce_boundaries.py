"""群聊双防抖 / 扣押边界测试。

覆盖：
- 离场/在场 + 助理/秘书防抖规则
- 旧事件 KILL / 最后边界放行
- 加速 must_reply
- 多群友并发助理防抖
- clear_chat
- 当前事件唤醒判定
- 秘书 must_reply / 空消息
- 私聊不进双防抖
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

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

# AstrBot 兼容导入留桩
sys.modules["astrbot.api"].logger = MagicMock()
sys.modules["astrbot.api.event"].MessageChain = MagicMock
sys.modules["astrbot.core.message.components"].Plain = type("Plain", (), {})
sys.modules["astrbot.core.message.components"].At = type("At", (), {})
sys.modules["astrbot.core.star.context"].Context = type("Context", (), {})

from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.debounce_manager import (
    ChatEnergyState,
    DebounceManager,
    PROCESS,
    KILL,
)
from astrbot_plugin_angel_heart.core.work_ledger import WorkLedger
from astrbot_plugin_angel_heart.core.angel_heart_status import (
    AngelHeartStatus,
    StatusChecker,
)
from astrbot_plugin_angel_heart.models.analysis_result import SecretaryDecision


class FakeClock:
    """可推进的假时钟，注入 WorkLedger(time_func=...) 构造超时状态。"""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class DummyEvent:
    def __init__(self, name: str, message_str: str = "hello", chat_id: str = "group:1"):
        self.name = name
        self.message_str = message_str
        self.message_obj = types.SimpleNamespace(message_id=name)
        self.unified_msg_origin = chat_id
        self.extras = {}
        self._stopped = False
        self.is_at_or_wake_command = False
        self.angelheart_context = None
        self._result = MagicMock()
        self._result.chain = ["x"]
        self._messages = None

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def get_sender_id(self):
        return "u1"

    def get_sender_name(self):
        return "user1"

    def get_self_id(self):
        return "bot1"

    def get_message_outline(self):
        return self.message_str

    def get_messages(self):
        if self._messages is not None:
            return self._messages
        if not self.message_str:
            return []

        class Plain:
            def __init__(self, text):
                self.text = text

        return [Plain(self.message_str)]

    def get_result(self):
        return self._result

    def stop_event(self):
        self._stopped = True

    def is_stopped(self):
        return self._stopped


def make_config(
    leave_reply_overrides=None,
    wake_reply_overrides=None,
    energy_overrides=None,
    **timing_overrides,
):
    timing = {
        "assistant_debounce_time": 0.05,
        "secretary_debounce_time": 0.08,
        "accelerate_debounce_time": 0.05,
        "waiting_time": 0.08,
        "observation_timeout": 60,
    }
    timing.update(timing_overrides)
    leave_reply = {"familiarity_cooldown_duration": 1800}
    leave_reply.update(leave_reply_overrides or {})
    energy = {}
    energy.update(energy_overrides or {})
    wake_interaction = {
        "alias": "草王|纳西妲",
        "force_reply_when_summoned": True,
        "reply_even_not_questioned": False,
        "enter_on_mention_only": True,
    }
    wake_interaction.update(wake_reply_overrides or {})
    return ConfigManager(
        {
            "analyzer_model": "mock-model",
            "timing": timing,
            "energy": energy,
            "wake_interaction": wake_interaction,
            "leave_reply": leave_reply,
            "access_control": {},
            "context_compression": {},
            "debug": {"debug_mode": False},
            "personality": {
                "ai_self_identity": "test",
                "reply_strategy_guide": "",
            },
        }
    )


class TestEnergyConfiguration:
    def test_energy_defaults_and_nested_overrides(self):
        default = make_config()
        assert default.initial_energy == 100.0
        assert default.max_energy == 100.0
        assert default.min_energy == -100.0
        assert default.recovery_per_second == 0.6
        assert default.base_reply_cost == 14.0
        assert default.reply_cost_per_character == 0.12

        custom = make_config(
            energy_overrides={
                "initial_energy": 40.0,
                "max_energy": 80.0,
                "min_energy": -30.0,
                "recovery_per_second": 1.5,
                "base_reply_cost": 9.0,
                "reply_cost_per_character": 0.2,
            }
        )
        assert custom.initial_energy == 40.0
        assert custom.max_energy == 80.0
        assert custom.min_energy == -30.0
        assert custom.recovery_per_second == 1.5
        assert custom.base_reply_cost == 9.0
        assert custom.reply_cost_per_character == 0.2

    def test_schema_exposes_energy_as_separate_section(self):
        schema = json.loads(
            (PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8")
        )
        assert set(schema["energy"]["items"]) == {
            "initial_energy",
            "max_energy",
            "min_energy",
            "recovery_per_second",
            "base_reply_cost",
            "reply_cost_per_character",
        }
        assert set(schema["reply_length"]["items"]) == {
            "focus_instructions",
            "normal_reply_max_chars",
            "focus_reply_max_chars",
        }

    def test_reply_length_defaults_and_focus_not_below_normal(self):
        default = make_config()
        assert default.focus_instructions == "分析 总结 好好想想 为什么 到底"
        assert default.normal_reply_max_chars == 20
        assert default.focus_reply_max_chars == 200

        custom = ConfigManager(
            {
                "reply_length": {
                    "focus_instructions": "深入 复盘",
                    "normal_reply_max_chars": 30,
                    "focus_reply_max_chars": 10,
                }
            }
        )
        assert custom.focus_instructions == "深入 复盘"
        assert custom.normal_reply_max_chars == 30
        assert custom.focus_reply_max_chars == 30


@pytest.fixture
def dm():
    return DebounceManager(make_config())


class TestDebounceManagerBoundaries:
    @pytest.mark.asyncio
    async def test_absent_non_wake_only_store(self, dm):
        e = DummyEvent("e1")
        f = await dm.schedule(
            chat_id="g1",
            event=e,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=False,
        )
        assert f is None
        assert not dm.has_assistant_debounce("g1")
        assert not dm.has_secretary_debounce("g1")

    @pytest.mark.asyncio
    async def test_absent_leave_reply_creates_must_reply_secretary_record(self, dm):
        event = DummyEvent("echo")
        ticket = await dm.schedule(
            chat_id="g1",
            event=event,
            sender_id="a",
            message_id="echo-1",
            is_wake=False,
            is_present=False,
            leave_reply_trigger="echo_chamber",
        )

        assert ticket is not None
        assert dm.has_secretary_debounce("g1")
        assert await ticket == PROCESS
        assert event.extras["angelheart_must_reply"] is True
        assert event.extras["angelheart_debounce_kind"] == "secretary"
        assert event.extras["angelheart_leave_reply_trigger"] == "echo_chamber"
        await dm.finish_secretary_dispatch(
            "g1", event.extras["angelheart_secretary_dispatch_id"], reason="test_done"
        )

    @pytest.mark.asyncio
    async def test_absent_wake_creates_assistant_and_process(self, dm):
        e = DummyEvent("e2")
        f = await dm.schedule(
            chat_id="g1",
            event=e,
            sender_id="a",
            message_id="2",
            is_wake=True,
            is_present=False,
        )
        assert f is not None
        assert dm.has_assistant_debounce("g1")
        assert await f == PROCESS
        assert e.extras.get("angelheart_must_reply") is True
        assert e.extras.get("angelheart_debounce_kind") == "assistant"

    @pytest.mark.asyncio
    async def test_assistant_blocks_secretary(self, dm):
        e1 = DummyEvent("a1")
        f1 = await dm.schedule(
            chat_id="g1",
            event=e1,
            sender_id="a",
            message_id="1",
            is_wake=True,
            is_present=True,
        )
        e2 = DummyEvent("b1")
        f2 = await dm.schedule(
            chat_id="g1",
            event=e2,
            sender_id="b",
            message_id="2",
            is_wake=False,
            is_present=True,
        )
        assert f1 is not None
        assert f2 is None
        assert not dm.has_secretary_debounce("g1")
        assert await f1 == PROCESS

    @pytest.mark.asyncio
    async def test_present_non_wake_creates_secretary(self, dm):
        e = DummyEvent("s1")
        f = await dm.schedule(
            chat_id="g1",
            event=e,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )
        assert f is not None
        assert dm.has_secretary_debounce("g1")
        assert await f == PROCESS
        assert e.extras.get("angelheart_must_reply") is False
        assert e.extras.get("angelheart_debounce_kind") == "secretary"

    @pytest.mark.asyncio
    async def test_secretary_wake_accelerate_must_reply(self, dm):
        e1 = DummyEvent("s1")
        f1 = await dm.schedule(
            chat_id="g1",
            event=e1,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )
        e2 = DummyEvent("s2")
        f2 = await dm.schedule(
            chat_id="g1",
            event=e2,
            sender_id="b",
            message_id="2",
            is_wake=True,
            is_present=True,
        )
        assert await f1 == KILL
        assert await f2 == PROCESS
        assert e2.extras.get("angelheart_must_reply") is True
        assert e2.extras.get("angelheart_debounce_kind") == "secretary"

    @pytest.mark.asyncio
    async def test_assistant_same_sender_updates_boundary_kills_old(self, dm):
        e1 = DummyEvent("a1")
        f1 = await dm.schedule(
            chat_id="g1",
            event=e1,
            sender_id="a",
            message_id="1",
            is_wake=True,
            is_present=True,
        )
        e2 = DummyEvent("a2")
        f2 = await dm.schedule(
            chat_id="g1",
            event=e2,
            sender_id="a",
            message_id="2",
            is_wake=False,
            is_present=True,
        )
        assert await f1 == KILL
        assert await f2 == PROCESS
        assert e2.extras.get("angelheart_debounce_end_message_id") == "2"
        assert e2.extras.get("angelheart_debounce_start_message_id") == "1"

    @pytest.mark.asyncio
    async def test_multi_sender_assistant_dispatches_serially_per_chat(self, dm):
        ea = DummyEvent("a")
        fa = await dm.schedule(
            chat_id="g1",
            event=ea,
            sender_id="A",
            message_id="a1",
            is_wake=True,
            is_present=False,
        )
        await asyncio.sleep(0.01)
        eb = DummyEvent("b")
        fb = await dm.schedule(
            chat_id="g1",
            event=eb,
            sender_id="B",
            message_id="b1",
            is_wake=True,
            is_present=True,
        )

        assert await fa == PROCESS
        await asyncio.sleep(0.07)
        assert not fb.done()

        await dm.finish_secretary_dispatch(
            "g1",
            ea.extras["angelheart_secretary_dispatch_id"],
            reason="test_first_done",
        )
        assert await asyncio.wait_for(fb, timeout=0.15) == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            eb.extras["angelheart_secretary_dispatch_id"],
            reason="test_second_done",
        )

    @pytest.mark.asyncio
    async def test_clear_chat_kills_all(self, dm):
        e1 = DummyEvent("a")
        e2 = DummyEvent("b")
        f1 = await dm.schedule(
            chat_id="g1",
            event=e1,
            sender_id="A",
            message_id="1",
            is_wake=True,
            is_present=True,
        )
        # 先清助理，再建秘书
        await dm.clear_chat("g1", reason="test")
        assert await f1 == KILL
        f2 = await dm.schedule(
            chat_id="g1",
            event=e2,
            sender_id="B",
            message_id="2",
            is_wake=False,
            is_present=True,
        )
        await dm.clear_chat("g1", reason="silence")
        assert await f2 == KILL
        assert not dm.has_assistant_debounce("g1")
        assert not dm.has_secretary_debounce("g1")

    @pytest.mark.asyncio
    async def test_expiry_race_prefers_newer_boundary(self, dm):
        # 到期瞬间再来新消息：旧 future 必须 KILL，新 future PROCESS
        e1 = DummyEvent("1")
        f1 = await dm.schedule(
            chat_id="g1",
            event=e1,
            sender_id="a",
            message_id="1",
            is_wake=True,
            is_present=True,
        )
        await asyncio.sleep(0.03)
        e2 = DummyEvent("2")
        f2 = await dm.schedule(
            chat_id="g1",
            event=e2,
            sender_id="a",
            message_id="2",
            is_wake=True,
            is_present=True,
        )
        assert await f1 == KILL
        assert await f2 == PROCESS
    @pytest.mark.asyncio
    async def test_assistant_rest_blocks_ordinary_patrol_creation(self):
        dm = DebounceManager(
            make_config(secretary_debounce_time=0.05, waiting_time=0.07)
        )
        assert await dm.start_assistant_rest("g1", 0.07, reason="assistant_invoked") is True

        event = DummyEvent("ordinary-during-rest")
        ticket = await dm.schedule(
            chat_id="g1",
            event=event,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )

        assert ticket is None
        assert not dm.has_secretary_debounce("g1")

    @pytest.mark.asyncio
    async def test_leave_reply_ignores_present_chat_cooldown(self):
        dm = DebounceManager(
            make_config(secretary_debounce_time=0.05, waiting_time=0.20)
        )
        present_event = DummyEvent("present")
        present_ticket = await dm.schedule(
            chat_id="g1",
            event=present_event,
            sender_id="a",
            message_id="present-1",
            is_wake=False,
            is_present=True,
        )
        assert await present_ticket == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            present_event.extras["angelheart_secretary_dispatch_id"],
            reason="reply_handoff",
        )
        assert await dm.start_assistant_rest("g1", 0.20, reason="assistant_invoked") is True

        leave_event = DummyEvent("leave")
        leave_ticket = await dm.schedule(
            chat_id="g1",
            event=leave_event,
            sender_id="b",
            message_id="leave-1",
            is_wake=False,
            is_present=False,
            leave_reply_trigger="echo_chamber",
        )
        assert await asyncio.wait_for(leave_ticket, timeout=0.15) == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            leave_event.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_ordinary_patrol_recovers_before_energy_gate(self):
        dm = DebounceManager(make_config(secretary_debounce_time=0.05))
        dm.energy_states["g1"] = ChatEnergyState(
            energy=-0.2,
            updated_at=time.time() - 1.0,
        )
        event = DummyEvent("recover")
        ticket = await dm.schedule(
            chat_id="g1",
            event=event,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )

        assert await asyncio.wait_for(ticket, timeout=0.15) == PROCESS
        assert dm.get_chat_energy("g1") > 0
        await dm.finish_secretary_dispatch(
            "g1",
            event.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_insufficient_energy_restarts_current_patrol(self):
        dm = DebounceManager(make_config(secretary_debounce_time=0.05))
        dm.energy_states["g1"] = ChatEnergyState(
            energy=-100.0,
            updated_at=time.time(),
        )
        event = DummyEvent("insufficient")
        ticket = await dm.schedule(
            chat_id="g1",
            event=event,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )

        await asyncio.sleep(0.06)
        assert not ticket.done()
        assert dm.has_secretary_debounce("g1")

        dm.energy_states["g1"].energy = 1.0
        assert await asyncio.wait_for(ticket, timeout=0.15) == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            event.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_mention_upgrades_current_patrol_but_ignores_energy_gate(self):
        dm = DebounceManager(make_config(secretary_debounce_time=0.10))
        dm.energy_states["g1"] = ChatEnergyState(
            energy=-100.0,
            updated_at=time.time(),
        )
        ordinary = DummyEvent("ordinary")
        ordinary_ticket = await dm.schedule(
            chat_id="g1",
            event=ordinary,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )
        mention = DummyEvent("mention")
        mention_ticket = await dm.schedule(
            chat_id="g1",
            event=mention,
            sender_id="b",
            message_id="2",
            is_wake=True,
            is_present=True,
        )

        assert len(dm._secretary) == 1
        assert await ordinary_ticket == KILL
        assert await asyncio.wait_for(mention_ticket, timeout=0.20) == PROCESS
        assert dm.get_chat_energy("g1") == pytest.approx(-100.0)
        assert mention.extras["angelheart_must_reply"] is True
        await dm.finish_secretary_dispatch(
            "g1",
            mention.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_point_wake_assistant_debounce_ignores_energy_gate(self):
        dm = DebounceManager(make_config(assistant_debounce_time=0.05))
        dm.energy_states["g1"] = ChatEnergyState(
            energy=-100.0,
            updated_at=time.time(),
        )
        mention = DummyEvent("mention")
        mention_ticket = await dm.schedule(
            chat_id="g1",
            event=mention,
            sender_id="b",
            message_id="1",
            is_wake=True,
            is_present=True,
        )

        assert await asyncio.wait_for(mention_ticket, timeout=0.15) == PROCESS
        assert dm.get_chat_energy("g1") == pytest.approx(-100.0)
        assert mention.extras["angelheart_must_reply"] is True
        assert mention.extras["angelheart_debounce_kind"] == "assistant"
        await dm.finish_secretary_dispatch(
            "g1",
            mention.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_energy_is_independent_per_chat(self):
        dm = DebounceManager(make_config(secretary_debounce_time=0.05))
        dm.energy_states["g1"] = ChatEnergyState(
            energy=-100.0,
            updated_at=time.time(),
        )
        first = DummyEvent("g1")
        first_ticket = await dm.schedule(
            chat_id="g1",
            event=first,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )
        second = DummyEvent("g2", chat_id="group:2")
        second_ticket = await dm.schedule(
            chat_id="g2",
            event=second,
            sender_id="a",
            message_id="2",
            is_wake=False,
            is_present=True,
        )

        await asyncio.sleep(0.06)
        assert not first_ticket.done()
        assert await second_ticket == PROCESS
        await dm.finish_secretary_dispatch(
            "g2",
            second.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

        dm.energy_states["g1"].energy = 1.0
        assert await asyncio.wait_for(first_ticket, timeout=0.15) == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            first.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_point_patrol_still_respects_assistant_rest(self):
        dm = DebounceManager(
            make_config(
                assistant_debounce_time=0.05,
                accelerate_debounce_time=0.05,
                waiting_time=0.12,
            )
        )
        first = DummyEvent("first")
        first_ticket = await dm.schedule(
            chat_id="g1",
            event=first,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )
        assert await first_ticket == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            first.extras["angelheart_secretary_dispatch_id"],
            reason="reply_handoff",
        )
        assert await dm.start_assistant_rest("g1", 0.12, reason="assistant_invoked") is True

        mention = DummyEvent("mention")
        mention_ticket = await dm.schedule(
            chat_id="g1",
            event=mention,
            sender_id="b",
            message_id="2",
            is_wake=True,
            is_present=True,
        )
        await asyncio.sleep(0.06)
        assert not mention_ticket.done()
        assert await asyncio.wait_for(mention_ticket, timeout=0.20) == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            mention.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_reply_energy_uses_final_chain_and_charges_once(self, dm):
        event = DummyEvent("reply")
        event.set_extra("angelheart_energy_charge_eligible", True)
        message_chain = [types.SimpleNamespace(text="清洗后的内容")]
        initial = dm.get_chat_energy("group:1")

        assert await dm.charge_reply_energy(event, message_chain) is True
        expected = initial - 14.0 - len("清洗后的内容") * 0.12
        assert dm.get_chat_energy("group:1") == pytest.approx(expected)
        assert await dm.charge_reply_energy(event, message_chain) is False
        assert dm.get_chat_energy("group:1") == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_unmarked_event_does_not_charge_reply_energy(self, dm):
        event = DummyEvent("other")
        initial = dm.get_chat_energy("g1")

        assert await dm.charge_reply_energy(
            event, [types.SimpleNamespace(text="其他事件")]
        ) is False
        assert dm.get_chat_energy("g1") == pytest.approx(initial)

    @pytest.mark.asyncio
    async def test_configured_energy_values_drive_initial_recovery_and_reply_cost(self):
        dm = DebounceManager(
            make_config(
                energy_overrides={
                    "initial_energy": 40.0,
                    "max_energy": 50.0,
                    "min_energy": -20.0,
                    "recovery_per_second": 0.0,
                    "base_reply_cost": 5.0,
                    "reply_cost_per_character": 1.0,
                }
            )
        )
        assert dm.get_chat_energy("g1") == 40.0

        event = DummyEvent("configured", chat_id="g1")
        event.set_extra("angelheart_energy_charge_eligible", True)
        assert await dm.charge_reply_energy(
            event, [types.SimpleNamespace(text="abc")]
        ) is True
        assert dm.get_chat_energy("g1") == 32.0

    @pytest.mark.asyncio
    async def test_configured_recovery_respects_configured_upper_bound(self):
        dm = DebounceManager(
            make_config(
                secretary_debounce_time=0.05,
                energy_overrides={
                    "max_energy": 3.0,
                    "recovery_per_second": 2.0,
                },
            )
        )
        dm.energy_states["g1"] = ChatEnergyState(
            energy=0.0,
            updated_at=time.time() - 10.0,
        )
        event = DummyEvent("configured-recovery")
        ticket = await dm.schedule(
            chat_id="g1",
            event=event,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )

        assert await asyncio.wait_for(ticket, timeout=0.15) == PROCESS
        assert dm.get_chat_energy("g1") == pytest.approx(3.0)
        await dm.finish_secretary_dispatch(
            "g1",
            event.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_configured_lower_bound_limits_reply_cost(self):
        dm = DebounceManager(
            make_config(
                energy_overrides={
                    "initial_energy": 0.0,
                    "min_energy": -2.0,
                    "base_reply_cost": 10.0,
                    "reply_cost_per_character": 1.0,
                }
            )
        )
        event = DummyEvent("configured-min", chat_id="g1")
        event.set_extra("angelheart_energy_charge_eligible", True)

        assert await dm.charge_reply_energy(
            event, [types.SimpleNamespace(text="abc")]
        ) is True
        assert dm.get_chat_energy("g1") == -2.0

    @pytest.mark.asyncio
    async def test_running_secretary_restarts_later_secretary_debounce(self):
        dm = DebounceManager(make_config(secretary_debounce_time=0.05))
        first = DummyEvent("first")
        first_future = await dm.schedule(
            chat_id="g1",
            event=first,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )
        assert await first_future == PROCESS

        second = DummyEvent("second")
        second_future = await dm.schedule(
            chat_id="g1",
            event=second,
            sender_id="b",
            message_id="2",
            is_wake=False,
            is_present=True,
        )
        await asyncio.sleep(0.06)
        assert not second_future.done()

        await dm.finish_secretary_dispatch(
            "g1",
            first.extras["angelheart_secretary_dispatch_id"],
            reason="first_done",
        )
        assert await asyncio.wait_for(second_future, timeout=0.15) == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            second.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_reply_handoff_releases_dispatch_so_next_wake_can_process(self):
        """秘书放行后立即释放单飞；助理生成期间不应再挡住下一轮点名。"""
        dm = DebounceManager(make_config(assistant_debounce_time=0.05, secretary_debounce_time=0.05))
        first = DummyEvent("first-reply")
        first_future = await dm.schedule(
            chat_id="g1",
            event=first,
            sender_id="a",
            message_id="1",
            is_wake=True,
            is_present=False,
        )
        assert await first_future == PROCESS
        assert dm.has_secretary_dispatch("g1") is True

        # 纯防抖层未注入工作账本时，只验证秘书单飞已释放。
        assert await dm.finish_secretary_dispatch(
            "g1",
            first.extras["angelheart_secretary_dispatch_id"],
            cooldown_seconds=0.0,
            reason="reply_handoff",
        ) is True
        assert dm.has_secretary_dispatch("g1") is False

        # 助理仍在忙 first 时，新的点名应能独立放行。
        second = DummyEvent("second-wake")
        second_future = await dm.schedule(
            chat_id="g1",
            event=second,
            sender_id="b",
            message_id="2",
            is_wake=True,
            is_present=True,
        )
        assert await asyncio.wait_for(second_future, timeout=0.15) == PROCESS
        assert second.extras.get("angelheart_must_reply") is True
        assert dm.has_secretary_dispatch("g1") is True

        await dm.finish_secretary_dispatch(
            "g1",
            second.extras["angelheart_secretary_dispatch_id"],
            cooldown_seconds=0.0,
            reason="reply_handoff",
        )

    @pytest.mark.asyncio
    async def test_running_assistant_work_restarts_wake_with_full_patrol_delay(self):
        work_ledger = WorkLedger()
        dm = DebounceManager(
            make_config(assistant_debounce_time=0.05, secretary_debounce_time=0.10),
            work_ledger=work_ledger,
        )
        work_ledger.start_work(
            chat_id="g1",
            work_id="work-a",
            trigger_message_id="1",
            trigger_summary="正在回答 A",
            kind="assistant",
        )

        event = DummyEvent("wake-while-busy")
        ticket = await dm.schedule(
            chat_id="g1",
            event=event,
            sender_id="b",
            message_id="2",
            is_wake=True,
            is_present=True,
        )
        await asyncio.sleep(0.06)
        assert not ticket.done()
        assert dm.has_assistant_debounce("g1") is False
        assert dm.has_secretary_debounce("g1") is True

        work_ledger.complete_work("g1", "work-a", status="done", result_summary="已回复")
        assert await asyncio.wait_for(ticket, timeout=0.20) == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            event.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_orphan_running_work_expires_and_patrol_passes(self):
        """孤儿 running（complete_work 永不触发）超时后自动失效，巡检放行不再死锁。

        钉死 issue #64：主脑失败/流式回复漏收口时，running 工作残留会导致
        assistant_busy 门闩无限重试；超时兜底应让其自愈。
        """
        clock = FakeClock(1000.0)
        work_ledger = WorkLedger(running_timeout=0.05, time_func=clock)
        dm = DebounceManager(
            make_config(assistant_debounce_time=0.05, secretary_debounce_time=0.10),
            work_ledger=work_ledger,
        )
        # 孤儿工作：登记后从未 complete_work
        work_ledger.start_work(
            chat_id="g1",
            work_id="orphan",
            trigger_message_id="1",
            trigger_summary="孤儿工作",
            kind="assistant",
        )
        # 模拟长时间未收口
        clock.advance(100)

        event = DummyEvent("patrol-after-orphan-expired")
        ticket = await dm.schedule(
            chat_id="g1",
            event=event,
            sender_id="b",
            message_id="2",
            is_wake=False,
            is_present=True,
        )
        # 巡检到期：孤儿已超时失效，门闩不再阻断，正常放行进入秘书
        assert await asyncio.wait_for(ticket, timeout=0.30) == PROCESS
        assert dm.has_secretary_dispatch("g1") is True
        await dm.finish_secretary_dispatch(
            "g1",
            event.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )

    @pytest.mark.asyncio
    async def test_start_assistant_rest_without_dispatch(self):
        """助理休息可在秘书单飞已释放后单独启动。"""
        dm = DebounceManager(
            make_config(secretary_debounce_time=0.05, waiting_time=0.10)
        )
        first = DummyEvent("first")
        first_future = await dm.schedule(
            chat_id="g1",
            event=first,
            sender_id="a",
            message_id="1",
            is_wake=False,
            is_present=True,
        )
        assert await first_future == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            first.extras["angelheart_secretary_dispatch_id"],
            cooldown_seconds=0.0,
            reason="reply_handoff",
        )
        assert dm.has_secretary_dispatch("g1") is False

        assert await dm.start_assistant_rest(
            "g1", 0.10, reason="assistant_invoked"
        ) is True

        ordinary = DummyEvent("ordinary")
        ordinary_future = await dm.schedule(
            chat_id="g1",
            event=ordinary,
            sender_id="b",
            message_id="2",
            is_wake=False,
            is_present=True,
        )
        assert ordinary_future is None

        mention = DummyEvent("mention")
        mention_future = await dm.schedule(
            chat_id="g1",
            event=mention,
            sender_id="b",
            message_id="3",
            is_wake=True,
            is_present=True,
        )
        await asyncio.sleep(0.06)
        assert not mention_future.done()
        assert dm.has_assistant_debounce("g1") is False
        assert dm.has_secretary_debounce("g1") is True

        boundary = DummyEvent("ordinary-boundary")
        boundary_future = await dm.schedule(
            chat_id="g1",
            event=boundary,
            sender_id="c",
            message_id="4",
            is_wake=False,
            is_present=True,
        )
        assert await mention_future == KILL
        assert await asyncio.wait_for(boundary_future, timeout=0.20) == PROCESS
        await dm.finish_secretary_dispatch(
            "g1",
            boundary.extras["angelheart_secretary_dispatch_id"],
            reason="test_done",
        )


class TestEventWakeDetection:
    def setup_method(self):
        self.config = make_config()
        self.angel_context = MagicMock()
        self.angel_context.silenced_until = {}
        self.checker = StatusChecker(self.config, self.angel_context)

    def test_wake_by_alias(self):
        e = DummyEvent("w1", message_str="草王在吗")
        assert self.checker.is_event_wake(e) is True

    def test_wake_by_alias_case_insensitive(self):
        config = make_config(wake_reply_overrides={"alias": "AngelHeart|草王"})
        checker = StatusChecker(config, MagicMock(silenced_until={}))
        e = DummyEvent("w1", message_str="angelheart 在吗")
        assert checker.is_event_wake(e) is True
        e = DummyEvent("w1", message_str="aNgElHeArT 帮我看下")
        assert checker.is_event_wake(e) is True

    def test_non_wake_normal_text(self):
        e = DummyEvent("w2", message_str="今天天气不错")
        assert self.checker.is_event_wake(e) is False

    def test_wake_by_at_component(self):
        class At:
            def __init__(self, qq):
                self.qq = qq

        e = DummyEvent("w3", message_str="")
        e._messages = [At("bot1")]
        assert self.checker.is_event_wake(e) is True

    def test_reply_outline_does_not_wake_by_alias_or_focus_text(self):
        class Reply:
            def __init__(self):
                self.message_str = "草王在吗，帮我分析一下"
                self.sender_nickname = "草王"
                self.text = "草王在吗，帮我分析一下"

        e = DummyEvent("w-reply", message_str="")
        e.get_message_outline = lambda: "[引用消息(草王: 草王在吗，帮我分析一下)]"
        e._messages = [Reply()]
        assert self.checker.is_event_wake(e) is False

    def test_cache_message_writes_hits_from_plain_body_only(self):
        import asyncio
        from types import SimpleNamespace
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        class Plain:
            def __init__(self, text):
                self.text = text

        class At:
            def __init__(self, qq, name=""):
                self.qq = qq
                self.name = name

        class Reply:
            def __init__(self):
                self.message_str = "草王帮我分析"
                self.sender_nickname = "草王"
                self.text = "草王帮我分析"

        angel = MagicMock()
        angel.astr_context = MagicMock()
        angel.conversation_ledger.add_message = MagicMock()
        config = make_config()
        config = type(config)(
            {
                **config._config,
                "reply_length": {
                    "focus_instructions": "分析 总结",
                    "normal_reply_max_chars": 20,
                    "focus_reply_max_chars": 200,
                },
            }
        )
        fd = FrontDesk(config, angel)

        event = DummyEvent("hit-1", message_str="")
        event._messages = [
            Reply(),
            At("someone", name="草王"),
            Plain("今天天气不错"),
        ]
        event.get_message_outline = (
            lambda: "[引用消息(草王: 草王帮我分析)] @草王 今天天气不错"
        )

        asyncio.run(fd.cache_message("group:1", event))
        cached = angel.conversation_ledger.add_message.call_args.args[1]
        assert cached["metadata"]["body_text"] == "今天天气不错"
        assert cached["metadata"]["hits"] == []
        assert event.get_extra("angelheart_message_metadata")["hits"] == []

        event2 = DummyEvent("hit-2", message_str="")
        event2._messages = [Plain("草王帮我分析一下")]
        asyncio.run(fd.cache_message("group:1", event2))
        cached2 = angel.conversation_ledger.add_message.call_args.args[1]
        assert cached2["metadata"]["body_text"] == "草王帮我分析一下"
        hit_types = {item["type"] for item in cached2["metadata"]["hits"]}
        assert "alias" in hit_types
        assert "focus" in hit_types
        assert any(
            item.get("type") == "alias" and item.get("phrase") == "草王"
            for item in cached2["metadata"]["hits"]
        )
        assert any(
            item.get("type") == "focus" and item.get("phrase") == "分析"
            for item in cached2["metadata"]["hits"]
        )

    def test_silenced_blocks_wake(self):
        import time

        self.angel_context.silenced_until = {"group:1": time.time() + 100}
        e = DummyEvent("w4", message_str="草王")
        assert self.checker.is_event_wake(e) is False

    def test_leave_reply_trigger_respects_switches_and_cooldown(self):
        config = make_config(
            leave_reply_overrides={
                "leave_echo_reply": True,
                "leave_dense_reply": True,
            }
        )
        angel = MagicMock()
        angel.is_leave_reply_in_cooldown.return_value = False
        checker = StatusChecker(config, angel)
        checker._detect_echo_chamber = MagicMock(return_value=True)
        checker._detect_dense_conversation = MagicMock(return_value=True)

        assert checker.get_leave_reply_trigger("g1") == "echo_chamber"
        checker._detect_dense_conversation.assert_not_called()

        angel.is_leave_reply_in_cooldown.return_value = True
        assert checker.get_leave_reply_trigger("g1") == ""

        angel.is_leave_reply_in_cooldown.return_value = False
        disabled_checker = StatusChecker(make_config(), angel)
        assert disabled_checker.get_leave_reply_trigger("g1") == ""

        dense_only_config = make_config(
            leave_reply_overrides={"leave_dense_reply": True}
        )
        dense_only_checker = StatusChecker(dense_only_config, angel)
        angel.is_leave_reply_in_cooldown.return_value = False
        dense_only_checker._detect_echo_chamber = MagicMock(return_value=True)
        dense_only_checker._detect_dense_conversation = MagicMock(return_value=True)
        assert dense_only_checker.get_leave_reply_trigger("g1") == "dense_conversation"
        dense_only_checker._detect_echo_chamber.assert_not_called()

class TestSecretaryActivation:
    @pytest.mark.asyncio
    async def test_must_reply_forces_true_when_force_enabled(self):
        from astrbot_plugin_angel_heart.roles.secretary import Secretary

        config = make_config()
        angel = MagicMock()
        angel.get_chat_status.return_value = AngelHeartStatus.OBSERVATION
        angel.is_present.return_value = True
        angel.debounce_manager.get_must_reply.return_value = True
        angel.debounce_manager.get_debounce_kind.return_value = "assistant"
        angel.debounce_manager.get_end_message_id.return_value = "boundary-2"
        angel.conversation_ledger.get_context_snapshot.return_value = (
            [{"role": "user", "content": "hist"}],
            [{"role": "user", "content": "new"}],
            1.0,
        )
        angel.status_transition_manager.transition_to_status = AsyncMock()

        secretary = Secretary(config, MagicMock(), angel)
        secretary.perform_analysis = AsyncMock(
            return_value=SecretaryDecision(
                should_reply=False,
                is_questioned=False,
                is_interesting=False,
                reply_strategy="继续观察",
                topic="t",
                entities=[],
                facts=[],
                keywords=[],
            )
        )

        event = DummyEvent("s", message_str="草王帮我看下")
        decision = await secretary.handle_message_by_state(event)
        angel.conversation_ledger.get_context_snapshot.assert_called_once_with(
            event.unified_msg_origin, "boundary-2"
        )
        assert event.get_extra("angelheart_decision_context")["boundary_message_id"] == "boundary-2"
        assert decision.should_reply is True
        assert decision.reply_strategy == "必须回应"

    @pytest.mark.asyncio
    async def test_must_reply_ignores_force_reply_configuration(self):
        from astrbot_plugin_angel_heart.roles.secretary import Secretary

        config = make_config(
            wake_reply_overrides={"force_reply_when_summoned": False}
        )
        angel = MagicMock()
        angel.get_chat_status.return_value = AngelHeartStatus.OBSERVATION
        angel.is_present.return_value = True
        angel.debounce_manager.get_must_reply.return_value = True
        angel.debounce_manager.get_debounce_kind.return_value = "secretary"
        angel.debounce_manager.get_end_message_id.return_value = "boundary-1"
        angel.conversation_ledger.get_context_snapshot.return_value = (
            [],
            [{"role": "user", "content": "被点名"}],
            1.0,
        )
        angel.status_transition_manager.transition_to_status = AsyncMock()

        secretary = Secretary(config, MagicMock(), angel)
        secretary.perform_analysis = AsyncMock(
            return_value=SecretaryDecision(
                should_reply=False,
                is_questioned=False,
                is_interesting=False,
                reply_strategy="继续观察",
                topic="t",
                entities=[],
                facts=[],
                keywords=[],
            )
        )

        decision = await secretary.handle_message_by_state(DummyEvent("mention"))
        assert decision.should_reply is True
        assert decision.reply_strategy == "必须回应"

    @pytest.mark.asyncio
    async def test_empty_message_str_no_longer_short_circuits(self):
        """正文只认 ledger/outline；秘书不再用 message_str 判空短路。"""
        from astrbot_plugin_angel_heart.roles.secretary import Secretary

        config = make_config()
        angel = MagicMock()
        angel.get_chat_status.return_value = AngelHeartStatus.OBSERVATION
        angel.is_present.return_value = True
        angel.debounce_manager.get_must_reply.return_value = False
        angel.debounce_manager.get_debounce_kind.return_value = "secretary"
        angel.status_transition_manager.transition_to_status = AsyncMock()
        angel.conversation_ledger.get_context_snapshot.return_value = (
            [],
            [{"role": "user", "content": "@bot"}],
            1.0,
        )
        angel.work_ledger.format_for_secretary.return_value = ""

        secretary = Secretary(config, MagicMock(), angel)
        secretary.perform_analysis = AsyncMock(
            return_value=SecretaryDecision(
                should_reply=False,
                reply_strategy="继续观察",
                topic="t",
                entities=[],
                facts=[],
                keywords=[],
            )
        )

        event = DummyEvent("empty", message_str="   ")
        decision = await secretary.handle_message_by_state(event)
        assert decision.reply_strategy == "继续观察"
        secretary.perform_analysis.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_recent_dialogue_skips(self):
        from astrbot_plugin_angel_heart.roles.secretary import Secretary

        config = make_config()
        angel = MagicMock()
        angel.get_chat_status.return_value = AngelHeartStatus.OBSERVATION
        angel.is_present.return_value = True
        angel.debounce_manager.get_must_reply.return_value = True
        angel.debounce_manager.get_debounce_kind.return_value = "assistant"
        angel.conversation_ledger.get_context_snapshot.return_value = ([], [], 0)
        angel.status_transition_manager.transition_to_status = AsyncMock()

        secretary = Secretary(config, MagicMock(), angel)
        event = DummyEvent("none", message_str="草王")
        decision = await secretary.handle_message_by_state(event)
        assert decision.should_reply is False
        assert decision.reply_strategy == "无新消息"


class TestMessageIdFlow:
    @pytest.mark.asyncio
    async def test_cache_uses_astrbot_message_id(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        angel = MagicMock()
        angel.astr_context = MagicMock()
        angel.conversation_ledger.add_message = MagicMock()
        fd = FrontDesk(make_config(), angel)
        event = DummyEvent("native-message-id")

        await fd.cache_message("group:1", event)

        cached = angel.conversation_ledger.add_message.call_args.args[1]
        assert cached["source_message_id"] == "native-message-id"
        assert not hasattr(event, "angelheart_event_id")

    def test_missing_astrbot_message_id_gets_fallback_on_message_object(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        angel = MagicMock()
        angel.astr_context = MagicMock()
        fd = FrontDesk(make_config(), angel)
        event = DummyEvent("")

        message_id = fd._ensure_message_id(event)

        assert message_id
        assert event.message_obj.message_id == message_id
        assert not hasattr(event, "angelheart_event_id")

    @pytest.mark.asyncio
    async def test_execute_decision_reuses_frozen_context(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        angel = MagicMock()
        angel.astr_context = MagicMock()
        fd = FrontDesk(make_config(), angel)
        event = DummyEvent("m2", chat_id="aiocqhttp:GroupMessage:1")
        decision = SecretaryDecision(
            should_reply=True,
            reply_strategy="回复",
            topic="t",
            entities=[],
            facts=[],
            keywords=[],
        )
        recent = [{"source_message_id": "m2", "content": "当前消息"}]
        historical = [{"role": "system", "content": "摘要"}]
        fd._get_decision_context_for_rewrite = MagicMock(
            return_value=(recent, historical, 2.0)
        )
        fd._process_decision_result = AsyncMock()

        await fd._execute_secretary_decision(decision, event, event.unified_msg_origin)

        angel.conversation_ledger.get_context_snapshot.assert_not_called()
        fd._process_decision_result.assert_awaited_once_with(
            decision,
            recent,
            historical,
            2.0,
            event,
            event.unified_msg_origin,
        )


class TestSecretaryDispatchCompletion:
    @pytest.mark.asyncio
    async def test_leave_reply_uses_direct_strategy_without_secretary_analysis(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        angel = MagicMock()
        angel.astr_context = MagicMock()
        angel.debounce_manager.get_leave_reply_trigger.return_value = "echo_chamber"
        fd = FrontDesk(make_config(), angel)
        fd.secretary = MagicMock()
        fd.secretary.handle_message_by_state = AsyncMock()
        decision = SecretaryDecision(
            should_reply=True,
            reply_strategy="跟紧复读队形",
            topic="复读互动",
            entities=[],
            facts=[],
            keywords=[],
        )
        fd.fishing_reply.generate_reply_strategy = AsyncMock(return_value=decision)
        fd._execute_secretary_decision = AsyncMock()
        event = DummyEvent("leave-reply", chat_id="GroupMessage:1")

        await fd._call_secretary_and_execute(event, event.unified_msg_origin)

        fd.fishing_reply.generate_reply_strategy.assert_awaited_once_with(
            event.unified_msg_origin, event, "echo_chamber"
        )
        fd.secretary.handle_message_by_state.assert_not_awaited()
        fd._execute_secretary_decision.assert_awaited_once_with(
            decision, event, event.unified_msg_origin
        )

    @pytest.mark.asyncio
    async def test_generate_reply_strategy_freezes_decision_context(self):
        """离场应答策略生成必须把账本快照固化到事件，供执行链取用。"""
        from astrbot_plugin_angel_heart.core.fishing_direct_reply import (
            FishingDirectReply,
        )

        angel = MagicMock()
        angel.debounce_manager.get_end_message_id.return_value = "echo-1"
        historical = [{"role": "system", "content": "摘要"}]
        recent = [{"source_message_id": "echo-1", "content": "复读"}]
        angel.conversation_ledger.get_context_snapshot.return_value = (
            historical,
            recent,
            2.0,
        )
        fdr = FishingDirectReply(make_config(), angel)
        event = DummyEvent("echo-1", chat_id="aiocqhttp:GroupMessage:1")

        decision = await fdr.generate_reply_strategy(
            event.unified_msg_origin, event, "echo_chamber"
        )

        assert decision.should_reply is True
        frozen = event.get_extra("angelheart_decision_context")
        assert frozen == {
            "historical_context": historical,
            "recent_dialogue": recent,
            "boundary_ts": 2.0,
            "boundary_message_id": "echo-1",
        }

    @pytest.mark.asyncio
    async def test_leave_reply_full_chain_executes_with_frozen_context(self):
        """离场应答完整链路：真实策略生成 + 真实执行决策，不得抛「秘书决策上下文缺失」。"""
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        angel = MagicMock()
        angel.astr_context = MagicMock()
        angel.debounce_manager.get_leave_reply_trigger.return_value = "echo_chamber"
        angel.debounce_manager.get_end_message_id.return_value = "echo-1"
        historical = [{"role": "system", "content": "摘要"}]
        recent = [{"source_message_id": "echo-1", "content": "复读"}]
        angel.conversation_ledger.get_context_snapshot.return_value = (
            historical,
            recent,
            2.0,
        )
        fd = FrontDesk(make_config(), angel)
        fd.secretary = MagicMock()
        fd.secretary.handle_message_by_state = AsyncMock()
        fd._process_decision_result = AsyncMock()
        event = DummyEvent("echo-1", chat_id="aiocqhttp:GroupMessage:1")

        # 不 mock fishing_reply.generate_reply_strategy，走真实实现
        await fd._call_secretary_and_execute(event, event.unified_msg_origin)

        # 离场应答不经过秘书分析
        fd.secretary.handle_message_by_state.assert_not_awaited()
        # 决策上下文已固化到事件
        frozen = event.get_extra("angelheart_decision_context")
        assert frozen is not None
        assert frozen["recent_dialogue"] == recent
        assert frozen["historical_context"] == historical
        # 执行链拿到同一份切片继续，不再抛「秘书决策上下文缺失」
        fd._process_decision_result.assert_awaited_once()
        call_args = fd._process_decision_result.await_args
        assert call_args.args[1] == recent
        assert call_args.args[2] == historical

    @pytest.mark.asyncio
    async def test_no_reply_only_releases_dispatch(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        config = make_config()
        angel = MagicMock()
        angel.astr_context = MagicMock()
        angel.debounce_manager.finish_secretary_dispatch = AsyncMock(return_value=True)
        angel.debounce_manager.get_leave_reply_trigger.return_value = ""
        fd = FrontDesk(config, angel)
        fd.secretary = MagicMock()
        fd.secretary.handle_message_by_state = AsyncMock(
            return_value=SecretaryDecision(
                should_reply=False,
                reply_strategy="继续观察",
                topic="测试",
                entities=[],
                facts=[],
                keywords=[],
            )
        )
        event = DummyEvent("no-reply", chat_id="GroupMessage:1")
        event.set_extra("angelheart_secretary_dispatch_id", "dispatch-1")

        await fd._call_secretary_and_execute(event, event.unified_msg_origin)

        angel.debounce_manager.finish_secretary_dispatch.assert_awaited_once_with(
            event.unified_msg_origin,
            "dispatch-1",
            cooldown_seconds=0.0,
            reason="no_reply",
        )
        assert event.is_stopped()

    @pytest.mark.asyncio
    async def test_reply_handoff_releases_dispatch_before_assistant_runs(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        config = make_config()
        angel = MagicMock()
        angel.astr_context = MagicMock()
        angel.debounce_manager.finish_secretary_dispatch = AsyncMock(return_value=True)
        angel.debounce_manager.start_assistant_rest = AsyncMock(return_value=True)
        angel.debounce_manager.get_leave_reply_trigger.return_value = ""
        fd = FrontDesk(config, angel)
        fd.secretary = MagicMock()
        fd.secretary.handle_message_by_state = AsyncMock(
            return_value=SecretaryDecision(
                should_reply=True,
                reply_strategy="直接回答",
                topic="测试",
                entities=[],
                facts=[],
                keywords=[],
            )
        )
        fd._get_decision_context_for_rewrite = MagicMock(
            return_value=([], [], time.time())
        )
        event = DummyEvent("reply-handoff", chat_id="GroupMessage:1")
        event.set_extra("angelheart_secretary_dispatch_id", "dispatch-reply")

        await fd._call_secretary_and_execute(event, event.unified_msg_origin)

        angel.debounce_manager.start_assistant_rest.assert_awaited_once_with(
            event.unified_msg_origin,
            config.waiting_time,
            reason="assistant_invoked",
        )
        angel.debounce_manager.finish_secretary_dispatch.assert_awaited_once_with(
            event.unified_msg_origin,
            "dispatch-reply",
            cooldown_seconds=0.0,
            reason="reply_handoff",
        )
        assert event.is_at_or_wake_command is True
        assert event.is_stopped() is False

    @pytest.mark.asyncio
    async def test_secretary_error_lets_assistant_handle(self):
        """秘书链路异常时放行事件，让主脑无脑处理，而不是 stop 导致不回复。"""
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        angel = MagicMock()
        angel.astr_context = MagicMock()
        angel.debounce_manager.finish_secretary_dispatch = AsyncMock(return_value=True)
        angel.debounce_manager.get_leave_reply_trigger.return_value = ""
        fd = FrontDesk(make_config(), angel)
        fd.secretary = MagicMock()
        fd.secretary.handle_message_by_state = AsyncMock(
            side_effect=RuntimeError("秘书决策上下文缺失")
        )
        event = DummyEvent("secretary-error", chat_id="GroupMessage:1")
        event.set_extra("angelheart_secretary_dispatch_id", "dispatch-error")

        await fd._call_secretary_and_execute(event, event.unified_msg_origin)

        # 秘书死了，事件不被拦死，放行主脑
        assert event.is_stopped() is False
        assert event.is_at_or_wake_command is True
        angel.debounce_manager.finish_secretary_dispatch.assert_awaited_once_with(
            event.unified_msg_origin,
            "dispatch-error",
            cooldown_seconds=0.0,
            reason="secretary_error",
        )

    @pytest.mark.asyncio
    async def test_secretary_error_debug_mode_keeps_handling(self):
        """调试模式下秘书异常：仍不拦死事件，但不在非唤醒场景伪造唤醒标志。"""
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        angel = MagicMock()
        angel.astr_context = MagicMock()
        angel.debounce_manager.finish_secretary_dispatch = AsyncMock(return_value=True)
        angel.debounce_manager.get_leave_reply_trigger.return_value = ""
        config = make_config()
        fd = FrontDesk(config, angel)
        fd.secretary = MagicMock()
        fd.secretary.handle_message_by_state = AsyncMock(
            side_effect=RuntimeError("分析失败")
        )
        event = DummyEvent("secretary-error-debug", chat_id="GroupMessage:1")
        event.set_extra("angelheart_secretary_dispatch_id", "dispatch-error-debug")

        await fd._call_secretary_and_execute(event, event.unified_msg_origin)

        assert event.is_stopped() is False
        angel.debounce_manager.finish_secretary_dispatch.assert_awaited_once_with(
            event.unified_msg_origin,
            "dispatch-error-debug",
            cooldown_seconds=0.0,
            reason="secretary_error",
        )


class TestFrontDeskPrivateAndGroupRouting:
    @pytest.mark.asyncio
    async def test_private_skips_debounce(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        config = make_config()
        angel = MagicMock()
        angel.silenced_until = {}
        angel.debounce_manager = MagicMock()
        angel.debounce_manager.schedule = AsyncMock()
        angel.conversation_ledger.add_message = MagicMock()
        angel.astr_context = MagicMock()

        fd = FrontDesk(config, angel)
        fd.cache_message = AsyncMock()
        fd._schedule_group_debounce = AsyncMock()
        fd._is_private_chat = lambda chat_id: True
        fd._ensure_message_id = lambda event: "eid"

        event = DummyEvent("p1", chat_id="FriendMessage:1")
        event.get_message_outline = lambda: "私聊内容"
        event.get_extra = lambda key, default=None: False

        await fd.handle_event(event)
        fd.cache_message.assert_awaited()
        fd._schedule_group_debounce.assert_not_awaited()
        angel.debounce_manager.schedule.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_group_absent_non_wake_stops_event(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        config = make_config()
        angel = MagicMock()
        angel.silenced_until = {}
        angel.is_present.return_value = False
        angel.debounce_manager = DebounceManager(config)
        angel.conversation_ledger.add_message = MagicMock()
        angel.astr_context = MagicMock()
        angel.get_chat_status.return_value = AngelHeartStatus.NOT_PRESENT
        angel.status_transition_manager.get_status_start_time.return_value = 0
        angel.transition_to_status = AsyncMock()

        fd = FrontDesk(config, angel)
        fd.cache_message = AsyncMock()
        fd._is_private_chat = lambda chat_id: False
        fd._ensure_message_id = lambda event: "eid"
        fd.status_checker.is_event_wake = lambda event: False
        fd._activate_group_event = AsyncMock()

        event = DummyEvent("g1", message_str="闲聊", chat_id="GroupMessage:1")
        event.get_message_outline = lambda: "闲聊"
        event.get_extra = lambda key, default=None: False

        await fd.handle_event(event)
        fd.cache_message.assert_awaited()
        fd._activate_group_event.assert_not_awaited()
        assert event.is_stopped() is True

    @pytest.mark.asyncio
    async def test_group_wake_activates_after_debounce(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        config = make_config(
            assistant_debounce_time=0.05,
            accelerate_debounce_time=0.05,
        )
        angel = MagicMock()
        angel.silenced_until = {}
        angel.is_present.return_value = False
        angel.debounce_manager = DebounceManager(config)
        angel.conversation_ledger.add_message = MagicMock()
        angel.astr_context = MagicMock()
        angel.get_chat_status.return_value = AngelHeartStatus.NOT_PRESENT
        angel.status_transition_manager.get_status_start_time.return_value = 0
        angel.transition_to_status = AsyncMock()

        fd = FrontDesk(config, angel)
        fd.cache_message = AsyncMock()
        fd._is_private_chat = lambda chat_id: False
        fd._ensure_message_id = lambda event: "wake-1"
        fd.status_checker.is_event_wake = lambda event: True
        fd._activate_group_event = AsyncMock()
        fd._ensure_minimum_context = AsyncMock()

        event = DummyEvent("wake", message_str="草王", chat_id="GroupMessage:9")
        event.get_message_outline = lambda: "草王"
        event.get_extra = lambda key, default=None: False
        event.get_sender_id = lambda: "u9"

        await fd.handle_event(event)
        fd._activate_group_event.assert_awaited()
        angel.transition_to_status.assert_awaited()


class TestStatusSemantics:
    def test_status_labels(self):
        assert AngelHeartStatus.NOT_PRESENT.value == "离场"
        assert AngelHeartStatus.OBSERVATION.value == "在场"

    @pytest.mark.asyncio
    async def test_getting_familiar_not_entry_in_determine_status_path(self):
        # determine_status 在非召唤情况下应回离场，不再因复读/密集进混脸熟
        config = make_config()
        angel = MagicMock()
        angel.silenced_until = {}
        angel.is_in_observation_period.return_value = False
        angel.get_chat_status.return_value = AngelHeartStatus.NOT_PRESENT
        angel.is_leave_reply_in_cooldown.return_value = False
        angel.conversation_ledger.get_all_messages.return_value = [
            {"role": "user", "content": "复读", "timestamp": 1},
            {"role": "user", "content": "复读", "timestamp": 2},
            {"role": "user", "content": "复读", "timestamp": 3},
        ]
        checker = StatusChecker(config, angel)
        checker._is_summoned = lambda chat_id: False
        status = await checker.determine_status("g1")
        assert status == AngelHeartStatus.NOT_PRESENT


class TestRuntimeCleanup:
    @pytest.mark.asyncio
    async def test_leave_reply_sent_keeps_not_present(self, tmp_path):
        from astrbot_plugin_angel_heart.core.angel_heart_context import AngelHeartContext

        context = AngelHeartContext(make_config(), MagicMock(), tmp_path)
        context.current_states["g1"] = AngelHeartStatus.NOT_PRESENT

        await context.handle_message_sent("g1", keep_not_present=True)

        assert context.get_chat_status("g1") is AngelHeartStatus.NOT_PRESENT
        assert context.is_leave_reply_in_cooldown("g1") is True
        assert "g1" not in context.status_transition_manager.status_start_times
        await context.cleanup()

    @pytest.mark.asyncio
    async def test_front_desk_cancels_registered_private_compression(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        angel = MagicMock()
        angel.astr_context = MagicMock()
        fd = FrontDesk(make_config(), angel)
        released = asyncio.Event()

        async def blocked(_chat_id):
            try:
                await asyncio.Event().wait()
            finally:
                released.set()

        fd._maybe_private_llm_compress = blocked
        fd._schedule_private_compression("FriendMessage:1")
        fd._schedule_private_compression("FriendMessage:1")
        await asyncio.sleep(0)

        assert len(fd._private_compression_tasks) == 1
        await fd.cleanup_background_tasks()

        assert released.is_set()
        assert fd._private_compression_tasks == {}

    @pytest.mark.asyncio
    async def test_context_cleanup_releases_all_runtime_state(self, tmp_path):
        from astrbot_plugin_angel_heart.core.angel_heart_context import AngelHeartContext

        context = AngelHeartContext(make_config(), MagicMock(), tmp_path)
        context.last_analysis_time["g1"] = 1.0
        context.silenced_until["g1"] = 2.0
        context.leave_reply_cooldown_until["g1"] = 3.0
        context.current_states["g1"] = AngelHeartStatus.OBSERVATION
        context.status_transition_manager.status_start_times["g1"] = (
            AngelHeartStatus.OBSERVATION,
            1.0,
        )
        context.work_ledger.start_work(
            chat_id="g1",
            work_id="w1",
            trigger_message_id="m1",
            trigger_summary="测试任务",
        )
        context.conversation_ledger.add_message(
            "g1",
            {"role": "user", "content": "hello", "timestamp": 1.0},
        )
        context.proactive_manager.custom_triggers["test"] = lambda *_args: True
        ticket = await context.debounce_manager.schedule(
            chat_id="g1",
            event=DummyEvent("cleanup"),
            sender_id="u1",
            message_id="e1",
            is_wake=True,
            is_present=False,
        )
        context.debounce_manager._secretary_dispatching["g1"] = "dispatch-1"
        context.debounce_manager._assistant_rest_until["g1"] = 9999999999.0
        context.energy_states["g1"] = ChatEnergyState(energy=-10.0)

        await context.cleanup()

        assert ticket.done() and ticket.result() == KILL
        assert context.last_analysis_time == {}
        assert context.silenced_until == {}
        assert context.leave_reply_cooldown_until == {}
        assert context.current_states == {}
        assert context.status_transition_manager.status_start_times == {}
        assert context.work_ledger._items == {}
        assert context.proactive_manager.active_tasks == {}
        assert context.proactive_manager.custom_triggers == {}
        assert context.debounce_manager._assistant == {}
        assert context.debounce_manager._secretary == {}
        assert context.debounce_manager._secretary_dispatching == {}
        assert context.debounce_manager._assistant_rest_until == {}
        assert context.energy_states == {}
        assert context.conversation_ledger._ledgers == {}
        assert context.conversation_ledger._compression_locks == {}
        assert context.conversation_ledger.db_conn is None
        assert context.conversation_ledger.db_cursor is None

    @pytest.mark.asyncio
    async def test_debounce_replacement_waits_until_old_timer_exits(self, dm):
        released = asyncio.Event()

        async def blocked():
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                released.set()

        event = DummyEvent("old")
        future = asyncio.get_running_loop().create_future()
        from astrbot_plugin_angel_heart.core.debounce_manager import DebounceRecord

        record = DebounceRecord(
            kind="assistant",
            chat_id="g1",
            sender_id="u1",
            event=event,
            future=future,
            version=1,
            must_reply=True,
            start_message_id="m1",
            end_message_id="m1",
            delay=60,
            timer=asyncio.create_task(blocked()),
        )
        await asyncio.sleep(0)

        await dm._kill_record(record, "test")

        assert record.timer.done()
        assert released.is_set()
        assert future.done() and future.result() == KILL

    @pytest.mark.asyncio
    async def test_proactive_replacement_keeps_new_task_registered(self):
        from astrbot_plugin_angel_heart.core.proactive_manager import ProactiveManager

        manager = ProactiveManager(MagicMock())
        assert await manager.trigger_delayed("g1", "old", "old", 60)
        old_task = manager.active_tasks["g1"].task
        await asyncio.sleep(0)

        assert await manager.trigger_delayed("g1", "new", "new", 60)
        new_request = manager.active_tasks["g1"]
        new_task = new_request.task
        await asyncio.sleep(0)

        assert old_task.done()
        assert manager.active_tasks["g1"] is new_request
        assert not new_task.done()

        await manager.cleanup()

        assert new_task.done()
        assert manager.active_tasks == {}

    @pytest.mark.asyncio
    async def test_runtime_tracker_stops_shared_pipeline_before_provider(self):
        from astrbot_plugin_angel_heart.core.runtime_task_tracker import (
            RuntimeTaskTracker,
            track_runtime_handler,
        )

        class RuntimeOwner:
            def __init__(self):
                self._runtime_tasks = RuntimeTaskTracker()
                self.started = asyncio.Event()
                self.released = asyncio.Event()
                self.calls = 0

            @track_runtime_handler
            async def handler(self, _event):
                self.calls += 1
                self.started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(0)
                    self.released.set()

        owner = RuntimeOwner()
        event = DummyEvent("runtime-shared")
        provider_called = asyncio.Event()

        async def pipeline():
            try:
                await owner.handler(event)
            except BaseException:
                pass  # AstrBot call_event_hook 会捕获 BaseException
            if event.is_stopped():
                return
            provider_called.set()

        pipeline_task = asyncio.create_task(pipeline())
        await owner.started.wait()

        await owner._runtime_tasks.stop()
        await pipeline_task

        assert event.is_stopped()
        assert not provider_called.is_set()
        assert owner.released.is_set()
        assert owner._runtime_tasks._children == {}
        assert owner._runtime_tasks._pipelines == {}

    @pytest.mark.asyncio
    async def test_runtime_tracker_waits_pipeline_already_in_provider(self):
        from astrbot_plugin_angel_heart.core.runtime_task_tracker import (
            RuntimeTaskTracker,
            track_runtime_handler,
        )

        class RuntimeOwner:
            def __init__(self):
                self._runtime_tasks = RuntimeTaskTracker()

            @track_runtime_handler
            async def handler(self, _event):
                return None

        owner = RuntimeOwner()
        event = DummyEvent("runtime-provider")
        provider_started = asyncio.Event()
        provider_cancelled = asyncio.Event()

        async def pipeline():
            await owner.handler(event)
            provider_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                provider_cancelled.set()

        pipeline_task = asyncio.create_task(pipeline())
        await provider_started.wait()

        await owner._runtime_tasks.stop()

        assert event.is_stopped()
        assert provider_cancelled.is_set()
        assert pipeline_task.cancelled()
        assert owner._runtime_tasks._children == {}
        assert owner._runtime_tasks._pipelines == {}

    @pytest.mark.asyncio
    async def test_runtime_tracker_rejects_old_handler_snapshot_and_stops_event(self):
        from astrbot_plugin_angel_heart.core.runtime_task_tracker import (
            RuntimeTaskTracker,
            track_runtime_handler,
        )

        class RuntimeOwner:
            def __init__(self):
                self._runtime_tasks = RuntimeTaskTracker()
                self.calls = 0

            @track_runtime_handler
            async def handler(self, _event):
                self.calls += 1

        owner = RuntimeOwner()
        await owner._runtime_tasks.stop()
        event = DummyEvent("runtime-rejected")

        assert await owner.handler(event) is None
        assert event.is_stopped()
        assert owner.calls == 0
        assert owner._runtime_tasks._children == {}
        assert owner._runtime_tasks._pipelines == {}

    @pytest.mark.asyncio
    async def test_runtime_tracker_stops_event_when_handler_swallows_cancellation(self):
        from astrbot_plugin_angel_heart.core.runtime_task_tracker import (
            RuntimeTaskTracker,
            track_runtime_handler,
        )

        class RuntimeOwner:
            def __init__(self):
                self._runtime_tasks = RuntimeTaskTracker()
                self.started = asyncio.Event()
                self.cancelled = asyncio.Event()

            @track_runtime_handler
            async def handler(self, _event):
                self.started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    return None

        owner = RuntimeOwner()
        event = DummyEvent("runtime-swallow")
        pipeline_task = asyncio.create_task(owner.handler(event))
        await owner.started.wait()

        await owner._runtime_tasks.stop()

        assert event.is_stopped()
        assert owner.cancelled.is_set()
        assert pipeline_task.done()
        assert owner._runtime_tasks._children == {}
        assert owner._runtime_tasks._pipelines == {}
