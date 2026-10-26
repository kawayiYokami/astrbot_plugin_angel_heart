"""戳一戳事件入口无视测试。

对应 issue #75：戳一戳不是对话消息，插件入口直接无视——
不缓存、不分析、不登记工作；不停事件，专门的戳一戳回应插件仍可处理。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from astrbot.core.message.components import At, Image, Plain, Poke, Reply


class _FakeRuntimeTasks:
    async def run(self, event, fn):
        return await fn()


class _Event:
    """最小事件桩：消息链、大纲、extra 与停止标记。"""

    def __init__(self, chain=None, outline="", message_str="", is_at=False):
        self.unified_msg_origin = "aiocqhttp:GroupMessage:1108124091"
        self.message_str = message_str
        self.is_at_or_wake_command = is_at
        self._chain = chain or []
        self._outline = outline
        self._extras = {}
        self.stopped = False

    def get_messages(self):
        return self._chain

    def get_message_outline(self):
        return self._outline

    def get_sender_id(self):
        return "2172762770"

    def get_self_id(self):
        return "10000"

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value):
        self._extras[key] = value

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped


def _plugin():
    from astrbot_plugin_angel_heart.main import AngelHeartPlugin

    plugin = object.__new__(AngelHeartPlugin)
    plugin._runtime_tasks = _FakeRuntimeTasks()
    plugin._is_upstream_command_event = MagicMock(return_value=False)
    plugin._is_blocked_by_provider_wake_prefix = MagicMock(return_value=False)
    plugin.config_manager = MagicMock()
    plugin.config_manager.whitelist_enabled = False
    return plugin


class TestPokeEntranceIgnore:
    def test_poke_event_ignored_and_not_stopped(self):
        plugin = _plugin()
        event = _Event(chain=[Poke()], outline="[Poke]")
        assert plugin._should_process(event) is False
        assert event.stopped is False

    @pytest.mark.asyncio
    async def test_poke_never_reaches_front_desk(self):
        plugin = _plugin()
        plugin.front_desk = MagicMock()
        plugin.front_desk.handle_event = AsyncMock()
        event = _Event(chain=[Poke()], outline="[Poke]")
        await plugin.smart_reply_handler(event)
        plugin.front_desk.handle_event.assert_not_awaited()
        assert event.stopped is False

    def test_plain_text_event_still_processed(self):
        plugin = _plugin()
        event = _Event(chain=[Plain("你好")], outline="你好", message_str="你好")
        assert plugin._should_process(event) is True

    def test_at_self_event_still_processed(self):
        plugin = _plugin()
        event = _Event(
            chain=[At(qq="10000")], outline="[At:10000]", message_str="", is_at=True
        )
        assert plugin._should_process(event) is True


class TestEmptyAtPromptFill:
    """仅 @ 无正文时补 @显示名：否则主脑被核心跳过，running 工作卡住门闩（issue #79）。"""

    def _front_desk(self):
        from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk

        return object.__new__(FrontDesk)

    def test_at_with_name_fills_display_name(self):
        front_desk = self._front_desk()
        event = _Event(chain=[At(qq="10000", name="红豆")], outline="[At:红豆]")
        front_desk._ensure_prompt_text_for_empty_at(event, event.unified_msg_origin)
        assert event.message_str == "@红豆"

    def test_at_without_name_falls_back_to_qq(self):
        front_desk = self._front_desk()
        event = _Event(chain=[At(qq="10000")], outline="[At:10000]")
        front_desk._ensure_prompt_text_for_empty_at(event, event.unified_msg_origin)
        assert event.message_str == "@10000"

    def test_plain_text_untouched(self):
        front_desk = self._front_desk()
        event = _Event(chain=[At(qq="10000", name="红豆")], message_str="在吗")
        front_desk._ensure_prompt_text_for_empty_at(event, event.unified_msg_origin)
        assert event.message_str == "在吗"

    def test_reply_component_not_filled(self):
        front_desk = self._front_desk()
        event = _Event(chain=[At(qq="10000", name="红豆"), Reply()])
        front_desk._ensure_prompt_text_for_empty_at(event, event.unified_msg_origin)
        assert event.message_str == ""

    def test_media_component_not_filled(self):
        front_desk = self._front_desk()
        event = _Event(chain=[At(qq="10000", name="红豆"), Image()])
        front_desk._ensure_prompt_text_for_empty_at(event, event.unified_msg_origin)
        assert event.message_str == ""

    def test_no_at_component_left_empty(self):
        front_desk = self._front_desk()
        event = _Event(chain=[Plain("")])
        front_desk._ensure_prompt_text_for_empty_at(event, event.unified_msg_origin)
        assert event.message_str == ""
