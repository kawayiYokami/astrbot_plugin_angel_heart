"""Leave replies use the effective cooldown of their own chat profile."""

from types import SimpleNamespace

from astrbot_plugin_angel_heart.core import angel_heart_context as context_module
from astrbot_plugin_angel_heart.core.angel_heart_context import AngelHeartContext


def test_leave_reply_cooldowns_use_each_chat_profile(monkeypatch):
    now = 1000.0
    monkeypatch.setattr(context_module, "time", SimpleNamespace(time=lambda: now))
    durations = {"short-profile": 30, "long-profile": 600, "default-profile": 1800}
    context = AngelHeartContext.__new__(AngelHeartContext)
    context.config_manager = SimpleNamespace(
        leave_reply_cooldown_duration=1800,
        for_chat=lambda chat_id: SimpleNamespace(
            leave_reply_cooldown_duration=durations[chat_id]
        ),
    )
    context.leave_reply_cooldown_until = {}

    for chat_id, duration in durations.items():
        context.start_leave_reply_cooldown(chat_id)
        assert context.leave_reply_cooldown_until[chat_id] == now + duration

    now += 31
    assert not context.is_leave_reply_in_cooldown("short-profile")
    assert context.is_leave_reply_in_cooldown("long-profile")
    assert context.is_leave_reply_in_cooldown("default-profile")
    assert "short-profile" not in context.leave_reply_cooldown_until
