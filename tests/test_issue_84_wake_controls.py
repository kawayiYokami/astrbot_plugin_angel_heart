"""Issue #84: ignored group senders and alias-only wake cooldowns."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.message.components import At, Plain, Poke, Reply
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter

from astrbot_plugin_angel_heart.core import angel_heart_status as status_module
from astrbot_plugin_angel_heart.core.angel_heart_status import StatusChecker
from astrbot_plugin_angel_heart.core.chat_profile import ChatProfileStore
from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.debounce_manager import DebounceManager, KILL
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


CHAT = "aiocqhttp:GroupMessage:84"
ALIAS_DECISION = "angelheart_alias_wake_accepted"


def make_config(**wake_overrides):
    wake = {"alias": "AngelHeart", "enter_on_mention_only": True}
    wake.update(wake_overrides)
    return ConfigManager(
        {
            "wake_interaction": wake,
            "timing": {
                "assistant_debounce_time": 60.0,
                "secretary_debounce_time": 60.0,
                "accelerate_debounce_time": 0.05,
            },
        }
    )


class DummyEvent:
    def __init__(
        self,
        message_id="m1",
        text="AngelHeart",
        *,
        sender="human",
        self_id="10000",
        chat_id=CHAT,
        chain=None,
        is_at=False,
    ):
        self.unified_msg_origin = chat_id
        self.message_str = text
        self.is_at_or_wake_command = is_at
        self.message_obj = SimpleNamespace(
            message_id=message_id,
            sender=SimpleNamespace(user_id=sender, nickname=str(sender), role="member"),
        )
        self.sender = sender
        self.self_id = self_id
        self.chain = [Plain(text)] if chain is None else chain
        self.extras = {}
        self.stopped = False
        self.angelheart_context = None
        self._result = MagicMock()
        self._result.chain = ["reply"]

    def get_messages(self):
        return self.chain

    def get_message_outline(self):
        return self.message_str or "[mention]"

    def get_sender_id(self):
        return self.sender

    def get_sender_name(self):
        return str(self.sender)

    def get_self_id(self):
        return self.self_id

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_result(self):
        return self._result

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped


def pointed_event(kind, **kwargs):
    self_id = kwargs.get("self_id", "10000")
    if kind == "at":
        component = At(qq=self_id)
    else:
        component = Reply()
        component.sender_id = self_id
    return DummyEvent(chain=[component, Plain("AngelHeart")], is_at=True, **kwargs)


class FakeRuntimeTasks:
    async def run(self, event, fn):
        return await fn()


def make_plugin(config):
    from astrbot_plugin_angel_heart.main import AngelHeartPlugin

    plugin = object.__new__(AngelHeartPlugin)
    plugin.config_manager = config
    plugin._runtime_tasks = FakeRuntimeTasks()
    plugin._is_blocked_by_provider_wake_prefix = MagicMock(return_value=False)
    plugin.front_desk = SimpleNamespace(handle_event=AsyncMock())
    return plugin


def make_checker(config):
    context = SimpleNamespace(
        silenced_until={},
        last_alias_wake_time={},
        alias_wake_lock=threading.Lock(),
    )
    return StatusChecker(config, context)


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    # Replace only this module's time facade; asyncio keeps its real clock.
    monkeypatch.setattr(
        status_module, "time", SimpleNamespace(time=time.time, monotonic=clock)
    )
    return clock


@pytest.fixture(params=["components", "metadata"])
def alias_event(request):
    def build(*args, **kwargs):
        event = DummyEvent(*args, **kwargs)
        if request.param == "metadata":
            event.set_extra(
                "angelheart_message_metadata",
                {"hits": [{"type": "alias", "phrase": "AngelHeart"}]},
            )
        return event

    return build


def test_wake_control_defaults_preserve_existing_behavior():
    config = ConfigManager({})
    assert config.ignored_sender_ids == []
    assert config.alias_wake_cooldown_seconds == 0.0
    assert isinstance(config.alias_wake_cooldown_seconds, float)


def test_ignored_sender_ids_normalize_numeric_and_padded_ids():
    config = make_config(ignored_sender_ids=[123, " 456 ", "", "   ", None])
    assert config.ignored_sender_ids == ["123", "456"]


@pytest.mark.parametrize("value", [None, "123", 123])
def test_malformed_ignored_sender_ids_fall_back_to_empty(value):
    assert make_config(ignored_sender_ids=value).ignored_sender_ids == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2, 2.0),
        ("2.5", 2.5),
        (0, 0.0),
        (-3, 0.0),
        (None, 0.0),
        ("invalid", 0.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
    ],
)
def test_alias_cooldown_is_a_nonnegative_finite_float(value, expected):
    seconds = make_config(alias_wake_cooldown_seconds=value).alias_wake_cooldown_seconds
    assert seconds == expected
    assert isinstance(seconds, float)


def test_wake_controls_follow_chat_profile_overrides_and_global_fallback(tmp_path):
    config = make_config(ignored_sender_ids=["global-bot"], alias_wake_cooldown_seconds=5)
    store = ChatProfileStore(str(tmp_path))
    config.attach_profile_store(store)
    template = store.create_template(
        "wake controls",
        config={"wake_interaction": {
            "ignored_sender_ids": [123, " 456 "],
            "alias_wake_cooldown_seconds": 2.5,
        }},
    )
    store.set_binding(CHAT, template["id"])
    assert config.for_chat(CHAT).ignored_sender_ids == ["123", "456"]
    assert config.for_chat(CHAT).alias_wake_cooldown_seconds == 2.5
    assert config.for_chat("aiocqhttp:GroupMessage:85").ignored_sender_ids == ["global-bot"]
    assert config.ignored_sender_ids == ["global-bot"]
    assert config.alias_wake_cooldown_seconds == 5.0

    empty = store.create_template(
        "no ignored senders", config={"wake_interaction": {"ignored_sender_ids": []}}
    )
    store.set_binding(CHAT, empty["id"])
    assert config.for_chat(CHAT).ignored_sender_ids == []
    assert config.for_chat(CHAT).alias_wake_cooldown_seconds == 5.0


def test_sender_filter_and_alias_cooldown_use_bound_chat_controls(tmp_path, clock):
    config = make_config(ignored_sender_ids=["global-bot"], alias_wake_cooldown_seconds=5)
    store = ChatProfileStore(str(tmp_path))
    config.attach_profile_store(store)
    template = store.create_template(
        "group-specific controls",
        config={"wake_interaction": {
            "ignored_sender_ids": ["group-bot"],
            "alias_wake_cooldown_seconds": 2.5,
        }},
    )
    store.set_binding(CHAT, template["id"])
    plugin = make_plugin(config)
    ignored = DummyEvent(sender="group-bot")
    assert plugin._should_process(ignored) is False
    assert ignored.is_stopped()
    assert plugin._should_process(DummyEvent(sender="global-bot")) is True
    unbound = DummyEvent(sender="global-bot", chat_id="aiocqhttp:GroupMessage:85")
    assert plugin._should_process(unbound) is False
    assert unbound.is_stopped()

    checker = make_checker(config)
    assert checker.is_event_wake(DummyEvent("bound-first")) is True
    assert checker.is_event_wake(
        DummyEvent("unbound-first", chat_id="aiocqhttp:GroupMessage:85")
    ) is True
    clock.advance(2.5)
    assert checker.is_event_wake(DummyEvent("bound-expired")) is True
    assert checker.is_event_wake(
        DummyEvent("unbound-cooling", chat_id="aiocqhttp:GroupMessage:85")
    ) is False


@pytest.mark.parametrize("kind", ["alias", "ordinary", "at", "reply"])
@pytest.mark.parametrize("sender", ["999", 999])
def test_ignored_group_sender_is_stopped_before_wake_matching(kind, sender):
    plugin = make_plugin(make_config(ignored_sender_ids=["999"]))
    if kind in {"at", "reply"}:
        event = pointed_event(kind, sender=sender)
    else:
        event = DummyEvent(sender=sender, text="AngelHeart" if kind == "alias" else "hello")

    assert plugin._should_process(event) is False
    assert event.is_stopped()
    plugin._is_blocked_by_provider_wake_prefix.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_type", [CommandFilter, CommandGroupFilter])
async def test_ignored_sender_upstream_command_bypasses_filter_without_stopping(filter_type):
    plugin = make_plugin(make_config(ignored_sender_ids=["999"]))
    event = DummyEvent(sender="999", text="/help", is_at=True)
    event.set_extra(
        "activated_handlers",
        [SimpleNamespace(event_filters=[filter_type()])],
    )

    await plugin.smart_reply_handler(event)

    assert not event.is_stopped()
    plugin.front_desk.handle_event.assert_not_awaited()
    plugin._is_blocked_by_provider_wake_prefix.assert_not_called()


@pytest.mark.asyncio
async def test_ignored_sender_poke_keeps_existing_nonstopping_bypass():
    plugin = make_plugin(make_config(ignored_sender_ids=["999"]))
    event = DummyEvent(sender="999", chain=[Poke()])

    await plugin.smart_reply_handler(event)

    assert not event.is_stopped()
    plugin.front_desk.handle_event.assert_not_awaited()
    plugin._is_blocked_by_provider_wake_prefix.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("is_at", [False, True])
async def test_ignored_sender_private_chat_still_reaches_front_desk(is_at):
    plugin = make_plugin(make_config(ignored_sender_ids=["999"]))
    event = DummyEvent(
        sender="999", chat_id="aiocqhttp:FriendMessage:999", is_at=is_at
    )

    await plugin.smart_reply_handler(event)

    plugin.front_desk.handle_event.assert_awaited_once_with(event)
    assert not event.is_stopped()


@pytest.mark.asyncio
async def test_human_at_still_reaches_front_desk_with_ignored_bot_configured():
    plugin = make_plugin(make_config(ignored_sender_ids=["999"]))
    event = pointed_event("at", sender="human")

    await plugin.smart_reply_handler(event)

    plugin.front_desk.handle_event.assert_awaited_once_with(event)
    assert not event.is_stopped()


def test_alias_cooldown_expiry_and_event_rechecks_keep_original_decision(clock, alias_event):
    checker = make_checker(make_config(alias_wake_cooldown_seconds=5))
    times = checker.angel_context.last_alias_wake_time
    first = alias_event("first")
    assert checker.is_event_wake(first) is True
    assert first.get_extra(ALIAS_DECISION) is True
    assert times == {CHAT: 100.0}

    clock.advance(4.5)
    assert checker.is_event_wake(first) is True
    suppressed = alias_event("suppressed")
    assert checker.is_event_wake(suppressed) is False
    assert suppressed.get_extra(ALIAS_DECISION) is False
    assert checker.is_event_wake(suppressed) is False
    assert times == {CHAT: 100.0}

    clock.advance(0.5)
    assert checker.is_event_wake(suppressed) is False
    assert times == {CHAT: 100.0}
    assert checker.is_event_wake(alias_event("expired")) is True
    assert times == {CHAT: 105.0}
    assert checker.is_event_wake(first) is True
    assert times == {CHAT: 105.0}


def test_alias_first_wake_at_monotonic_zero_still_starts_cooldown(clock, alias_event):
    clock.now = 0.0
    checker = make_checker(make_config(alias_wake_cooldown_seconds=5))

    assert checker.is_event_wake(alias_event("first")) is True
    assert checker.is_event_wake(alias_event("second")) is False
    assert checker.angel_context.last_alias_wake_time == {CHAT: 0.0}


def test_alias_cooldown_is_shared_by_senders_but_scoped_to_full_origin(clock, alias_event):
    checker = make_checker(make_config(alias_wake_cooldown_seconds=5))
    assert checker.is_event_wake(alias_event("human", sender="human")) is True
    assert checker.is_event_wake(alias_event("bot", sender="999")) is False
    other_platform = "other-bot:GroupMessage:84"
    other_group = "aiocqhttp:GroupMessage:85"
    assert checker.is_event_wake(alias_event("platform", chat_id=other_platform)) is True
    assert checker.is_event_wake(alias_event("group", chat_id=other_group)) is True
    assert set(checker.angel_context.last_alias_wake_time) == {
        CHAT, other_platform, other_group
    }


@pytest.mark.parametrize("kind", ["at", "reply"])
@pytest.mark.parametrize("with_metadata", [False, True])
def test_explicit_wake_has_priority_and_never_consumes_alias_cooldown(
    clock, kind, with_metadata
):
    checker = make_checker(make_config(alias_wake_cooldown_seconds=5))
    for index in range(2):
        event = pointed_event(kind, message_id=f"explicit-{index}")
        if with_metadata:
            event.set_extra(
                "angelheart_message_metadata",
                {"hits": [{"type": "alias"}, {"type": "at_self"}]},
            )
        # Even a previously suppressed alias decision cannot override @/Reply.
        event.set_extra(ALIAS_DECISION, False)
        assert checker.is_event_wake(event) is True
        if index == 0:
            assert checker.angel_context.last_alias_wake_time == {}
            assert checker.is_event_wake(DummyEvent("alias")) is True
            clock.advance(1)
        else:
            assert checker.angel_context.last_alias_wake_time == {CHAT: 100.0}

    assert checker.is_event_wake(DummyEvent("blocked-alias")) is False
    clock.advance(4)
    assert checker.is_event_wake(DummyEvent("expired-alias")) is True


@pytest.mark.parametrize("seconds", [0, 5])
def test_private_alias_wakes_do_not_use_group_cooldown(seconds):
    checker = make_checker(make_config(alias_wake_cooldown_seconds=seconds))
    for message_id in ("private-1", "private-2"):
        event = DummyEvent(message_id, chat_id="aiocqhttp:FriendMessage:999")
        assert checker.is_event_wake(event) is True
    assert checker.angel_context.last_alias_wake_time == {}


def test_zero_alias_cooldown_preserves_repeated_group_wakes_without_state():
    checker = StatusChecker(make_config(), SimpleNamespace(silenced_until={}))
    assert checker.is_event_wake(DummyEvent("first")) is True
    assert checker.is_event_wake(DummyEvent("second")) is True


@pytest.mark.asyncio
async def test_context_initializes_and_cleans_up_alias_cooldown(tmp_path):
    from astrbot_plugin_angel_heart.core.angel_heart_context import AngelHeartContext

    context = AngelHeartContext(
        make_config(alias_wake_cooldown_seconds=5), MagicMock(), tmp_path
    )
    try:
        assert context.last_alias_wake_time == {}
        assert context.alias_wake_lock.acquire(blocking=False)
        context.alias_wake_lock.release()
        context.last_alias_wake_time[CHAT] = 100.0
    finally:
        await context.cleanup()

    assert context.last_alias_wake_time == {}


def make_runtime(config, monkeypatch):
    """Real entrypoint/cache/wake/debounce; hold timers before dispatch."""
    plugin = make_plugin(config)
    context = MagicMock()
    context.astr_context = MagicMock()
    context.silenced_until = {}
    context.last_alias_wake_time = {}
    context.alias_wake_lock = threading.Lock()
    context.is_present.return_value = True
    context.debounce_manager = DebounceManager(config)
    front_desk = FrontDesk(config, context)
    front_desk._check_and_handle_timeout = AsyncMock()
    front_desk._activate_group_event = AsyncMock()
    plugin.front_desk = front_desk
    plugin.angel_context = context
    scheduled = asyncio.Queue()
    schedule = context.debounce_manager.schedule

    async def observe_schedule(**kwargs):
        ticket = await schedule(**kwargs)
        scheduled.put_nowait((kwargs["event"], ticket))
        return ticket

    async def hold_timer(_record):
        await asyncio.Event().wait()

    monkeypatch.setattr(context.debounce_manager, "schedule", observe_schedule)
    monkeypatch.setattr(context.debounce_manager, "_timer_handler", hold_timer)
    return plugin, context, scheduled


async def next_ticket(scheduled, event):
    observed, ticket = await asyncio.wait_for(scheduled.get(), timeout=1.0)
    assert observed is event
    assert ticket is not None
    return ticket


@pytest.mark.asyncio
async def test_ignored_bot_cannot_replace_pending_human_ticket_or_write_history(
    clock, monkeypatch
):
    plugin, context, scheduled = make_runtime(
        make_config(ignored_sender_ids=["999"], alias_wake_cooldown_seconds=5),
        monkeypatch,
    )
    manager = context.debounce_manager
    human = DummyEvent("human", text="ordinary human message")
    task = asyncio.create_task(plugin.smart_reply_handler(human))
    try:
        ticket = await next_ticket(scheduled, human)
        original = manager._secretary[CHAT]
        assert original.must_reply is False
        context.conversation_ledger.add_message.assert_called_once()
        before = context.conversation_ledger.add_message.call_args
        bot = DummyEvent("bot", sender="999")

        await asyncio.wait_for(plugin.smart_reply_handler(bot), timeout=1.0)

        assert bot.is_stopped()
        assert not human.is_stopped()
        assert manager._secretary[CHAT] is original
        assert original.future is ticket and not ticket.done()
        assert manager._assistant == {}
        assert scheduled.empty()
        context.conversation_ledger.add_message.assert_called_once()
        assert context.conversation_ledger.add_message.call_args == before
        assert context.last_alias_wake_time == {}
        plugin.front_desk._activate_group_event.assert_not_awaited()
    finally:
        await manager.cleanup()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["assistant", "secretary"])
async def test_cold_alias_repeat_updates_boundary_without_accelerating_debounce(
    clock, monkeypatch, kind
):
    config = make_config(alias_wake_cooldown_seconds=5)
    plugin, context, scheduled = make_runtime(config, monkeypatch)
    manager = context.debounce_manager
    if kind == "secretary":
        assert plugin.front_desk.status_checker.is_event_wake(DummyEvent("seed")) is True
    first = DummyEvent("first", text="AngelHeart" if kind == "assistant" else "hello")
    tasks = [asyncio.create_task(plugin.smart_reply_handler(first))]
    try:
        first_ticket = await next_ticket(scheduled, first)
        store = manager._assistant if kind == "assistant" else manager._secretary
        key = (CHAT, "human") if kind == "assistant" else CHAT
        original = store[key]
        assert original.delay == 60.0
        assert original.must_reply is (kind == "assistant")
        clock.advance(1)
        repeat = DummyEvent("repeat")
        tasks.append(asyncio.create_task(plugin.smart_reply_handler(repeat)))
        repeat_ticket = await next_ticket(scheduled, repeat)
        replacement = store[key]

        assert repeat.get_extra(ALIAS_DECISION) is False
        assert first_ticket.done() and first_ticket.result() == KILL
        assert not repeat_ticket.done()
        assert replacement.event is repeat
        assert replacement.start_message_id == "first"
        assert replacement.end_message_id == "repeat"
        assert replacement.must_reply is original.must_reply
        assert 50.0 < replacement.delay <= 60.0
        assert context.last_alias_wake_time == {CHAT: 100.0}
        assert context.conversation_ledger.add_message.call_count == 2
        plugin.front_desk._activate_group_event.assert_not_awaited()
    finally:
        await manager.cleanup()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_two_bots_cannot_ping_pong_but_human_at_still_schedules(clock, monkeypatch):
    runtimes = [
        make_runtime(
            make_config(ignored_sender_ids=[peer], alias_wake_cooldown_seconds=5),
            monkeypatch,
        )
        for peer in ("20000", "10000")
    ]
    tasks = []
    try:
        for plugin, context, scheduled in runtimes:
            peer = plugin.config_manager.ignored_sender_ids[0]
            self_id = "10000" if peer == "20000" else "20000"
            for index in range(3):
                event = DummyEvent(f"echo-{index}", sender=peer, self_id=self_id)
                await asyncio.wait_for(plugin.smart_reply_handler(event), timeout=1.0)
                assert event.is_stopped()

            context.conversation_ledger.add_message.assert_not_called()
            assert context.last_alias_wake_time == {}
            assert context.debounce_manager._assistant == {}
            assert context.debounce_manager._secretary == {}
            assert scheduled.empty()
            human = pointed_event("at", message_id="human", self_id=self_id)
            tasks.append(asyncio.create_task(plugin.smart_reply_handler(human)))
            ticket = await next_ticket(scheduled, human)
            assert not ticket.done()
            assert not human.is_stopped()
            assert context.debounce_manager._assistant[(CHAT, "human")].must_reply is True
            assert context.last_alias_wake_time == {}
            context.conversation_ledger.add_message.assert_called_once()
    finally:
        for _, context, _ in runtimes:
            await context.debounce_manager.cleanup()
        await asyncio.gather(*tasks, return_exceptions=True)
