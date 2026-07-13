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
import sys
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

# MessageChain / Plain 等给 patience 等兼容导入留桩
sys.modules["astrbot.api"].logger = MagicMock()
sys.modules["astrbot.api.event"].MessageChain = MagicMock
sys.modules["astrbot.core.message.components"].Plain = type("Plain", (), {})
sys.modules["astrbot.core.message.components"].At = type("At", (), {})
sys.modules["astrbot.core.star.context"].Context = type("Context", (), {})

from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.debounce_manager import (
    DebounceManager,
    PROCESS,
    KILL,
)
from astrbot_plugin_angel_heart.core.angel_heart_status import (
    AngelHeartStatus,
    StatusChecker,
)
from astrbot_plugin_angel_heart.models.analysis_result import SecretaryDecision


class DummyEvent:
    def __init__(self, name: str, message_str: str = "hello", chat_id: str = "group:1"):
        self.name = name
        self.message_str = message_str
        self.unified_msg_origin = chat_id
        self.extras = {}
        self._stopped = False
        self.is_at_or_wake_command = False
        self.angelheart_context = None
        self._result = MagicMock()
        self._result.chain = ["x"]

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
        return []

    def get_result(self):
        return self._result

    def stop_event(self):
        self._stopped = True

    def is_stopped(self):
        return self._stopped


def make_config(**timing_overrides):
    timing = {
        "assistant_debounce_time": 0.05,
        "secretary_debounce_time": 0.08,
        "accelerate_debounce_time": 0.05,
        "waiting_time": 0.08,
        "observation_timeout": 60,
        "no_reply_cooldown": 0.01,
    }
    timing.update(timing_overrides)
    return ConfigManager(
        {
            "analyzer_model": "mock-model",
            "timing": timing,
            "wake_interaction": {
                "alias": "草王|纳西妲",
                "force_reply_when_summoned": True,
                "reply_even_not_questioned": False,
                "analysis_on_mention_only": True,
            },
            "leave_reply": {},
            "access_control": {},
            "context_compression": {},
            "comfort": {},
            "debug": {"debug_mode": False},
            "personality": {
                "ai_self_identity": "test",
                "reply_strategy_guide": "",
            },
        }
    )


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
            event_id="1",
            is_wake=False,
            is_present=False,
        )
        assert f is None
        assert not dm.has_assistant_debounce("g1")
        assert not dm.has_secretary_debounce("g1")

    @pytest.mark.asyncio
    async def test_absent_wake_creates_assistant_and_process(self, dm):
        e = DummyEvent("e2")
        f = await dm.schedule(
            chat_id="g1",
            event=e,
            sender_id="a",
            event_id="2",
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
            event_id="1",
            is_wake=True,
            is_present=True,
        )
        e2 = DummyEvent("b1")
        f2 = await dm.schedule(
            chat_id="g1",
            event=e2,
            sender_id="b",
            event_id="2",
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
            event_id="1",
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
            event_id="1",
            is_wake=False,
            is_present=True,
        )
        e2 = DummyEvent("s2")
        f2 = await dm.schedule(
            chat_id="g1",
            event=e2,
            sender_id="b",
            event_id="2",
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
            event_id="1",
            is_wake=True,
            is_present=True,
        )
        e2 = DummyEvent("a2")
        f2 = await dm.schedule(
            chat_id="g1",
            event=e2,
            sender_id="a",
            event_id="2",
            is_wake=False,
            is_present=True,
        )
        assert await f1 == KILL
        assert await f2 == PROCESS
        assert e2.extras.get("angelheart_debounce_end_event_id") == "2"
        assert e2.extras.get("angelheart_debounce_start_event_id") == "1"

    @pytest.mark.asyncio
    async def test_multi_sender_assistant_parallel(self, dm):
        ea = DummyEvent("a")
        eb = DummyEvent("b")
        fa = await dm.schedule(
            chat_id="g1",
            event=ea,
            sender_id="A",
            event_id="a1",
            is_wake=True,
            is_present=False,
        )
        fb = await dm.schedule(
            chat_id="g1",
            event=eb,
            sender_id="B",
            event_id="b1",
            is_wake=True,
            is_present=True,
        )
        assert fa is not None and fb is not None
        ra, rb = await asyncio.gather(fa, fb)
        assert ra == PROCESS
        assert rb == PROCESS

    @pytest.mark.asyncio
    async def test_clear_chat_kills_all(self, dm):
        e1 = DummyEvent("a")
        e2 = DummyEvent("b")
        f1 = await dm.schedule(
            chat_id="g1",
            event=e1,
            sender_id="A",
            event_id="1",
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
            event_id="2",
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
            event_id="1",
            is_wake=True,
            is_present=True,
        )
        await asyncio.sleep(0.03)
        e2 = DummyEvent("2")
        f2 = await dm.schedule(
            chat_id="g1",
            event=e2,
            sender_id="a",
            event_id="2",
            is_wake=True,
            is_present=True,
        )
        assert await f1 == KILL
        assert await f2 == PROCESS


class TestEventWakeDetection:
    def setup_method(self):
        self.config = make_config()
        self.angel_context = MagicMock()
        self.angel_context.silenced_until = {}
        self.checker = StatusChecker(self.config, self.angel_context)

    def test_wake_by_alias(self):
        e = DummyEvent("w1", message_str="草王在吗")
        assert self.checker.is_event_wake(e) is True

    def test_non_wake_normal_text(self):
        e = DummyEvent("w2", message_str="今天天气不错")
        assert self.checker.is_event_wake(e) is False

    def test_wake_by_at_component(self):
        class At:
            def __init__(self, qq):
                self.qq = qq

        e = DummyEvent("w3", message_str="")
        e.get_messages = lambda: [At("bot1")]
        assert self.checker.is_event_wake(e) is True

    def test_silenced_blocks_wake(self):
        import time

        self.angel_context.silenced_until = {"group:1": time.time() + 100}
        e = DummyEvent("w4", message_str="草王")
        assert self.checker.is_event_wake(e) is False


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
        fd._ensure_internal_event_id = lambda event: "eid"

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
        fd._ensure_internal_event_id = lambda event: "eid"
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
        fd._ensure_internal_event_id = lambda event: "wake-1"
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
        angel.is_familiarity_in_cooldown.return_value = False
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
        context.familiarity_cooldown_until["g1"] = 3.0
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
        patience = asyncio.create_task(asyncio.sleep(60))
        degradation = asyncio.create_task(asyncio.sleep(60))
        context.patience_timers["g1"] = patience
        context.status_transition_manager.degradation_timers["g1"] = degradation
        context.proactive_manager.custom_triggers["test"] = lambda *_args: True
        ticket = await context.debounce_manager.schedule(
            chat_id="g1",
            event=DummyEvent("cleanup"),
            sender_id="u1",
            event_id="e1",
            is_wake=True,
            is_present=False,
        )

        await context.cleanup()

        assert ticket.done() and ticket.result() == KILL
        assert patience.done() and degradation.done()
        assert context.patience_timers == {}
        assert context.last_analysis_time == {}
        assert context.silenced_until == {}
        assert context.familiarity_cooldown_until == {}
        assert context.current_states == {}
        assert context.status_transition_manager.status_start_times == {}
        assert context.status_transition_manager.degradation_timers == {}
        assert context.work_ledger._items == {}
        assert context.proactive_manager.active_tasks == {}
        assert context.proactive_manager.custom_triggers == {}
        assert context.debounce_manager._assistant == {}
        assert context.debounce_manager._secretary == {}
        assert context.conversation_ledger._ledgers == {}
        assert context.conversation_ledger._compression_locks == {}
        assert context.conversation_ledger.db_conn is None
        assert context.conversation_ledger.db_cursor is None
