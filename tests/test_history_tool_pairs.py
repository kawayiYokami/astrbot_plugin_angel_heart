"""Historical selection boundaries keep tool exchanges replayable."""

import copy
from types import SimpleNamespace

import pytest

from astrbot_plugin_angel_heart.core.config_manager import ConfigManager
from astrbot_plugin_angel_heart.core.conversation_ledger import ConversationLedger
from astrbot_plugin_angel_heart.core.utils.message_utils import (
    normalize_history_tool_pairs,
    retain_complete_history_blocks,
)
from astrbot_plugin_angel_heart.roles.front_desk import FrontDesk


def _call(timestamp=1, *ids):
    return {
        "role": "assistant",
        "content": [{"type": "think", "think": "synthetic reasoning"}],
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "synthetic_tool",
                    "arguments": '{"synthetic": true}',
                },
            }
            for call_id in (ids or ("synthetic-call",))
        ],
        "timestamp": float(timestamp),
        "cost": 2,
    }


def _result(timestamp=2, call_id="synthetic-call", text="synthetic result"):
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": text,
        "timestamp": float(timestamp),
        "cost": 1,
    }


def _users(count, start=3):
    return [
        {
            "role": "user",
            "content": f"synthetic user {i}",
            "timestamp": float(i),
            "cost": 1,
        }
        for i in range(start, start + count)
    ]


@pytest.fixture
def ledger(tmp_path):
    config = ConfigManager(
        {
            "context_compression": {
                "max_conversation_tokens": 1000000,
                "content_retain_tokens": 1000,
                "forgetting_timeout": 0,
            }
        }
    )
    instance = ConversationLedger(config, tmp_path, astr_context=None)
    instance._count_message_tokens = lambda message: message.get("cost", 1)
    yield instance
    instance.close()


def test_cold_seed_seven_message_cut_drops_entire_split_exchange():
    front = object.__new__(FrontDesk)
    history = [_call(), _result(), *_users(6)]
    original = copy.deepcopy(history)

    selected = front._convert_astrbot_history_to_angelheart_format(history, 19)

    assert len(selected) == 6
    assert all(message["role"] == "user" for message in selected)
    assert history == original


def test_cold_seed_token_cut_drops_result_when_its_call_does_not_fit():
    front = object.__new__(FrontDesk)
    call = _call()
    call["content"] = "synthetic " * 10000
    selected = front._convert_astrbot_history_to_angelheart_format(
        [call, _result(), *_users(1)], 19
    )
    assert len(selected) == 1
    assert selected[0]["role"] == "user"


def test_cold_seed_keeps_normal_chain_and_reasoning_when_whole_block_fits():
    front = object.__new__(FrontDesk)
    call = _call()
    selected = front._convert_astrbot_history_to_angelheart_format(
        [call, _result(), *_users(3)], 19
    )
    assert len(selected) == 5
    assert selected[0]["tool_calls"] == call["tool_calls"]
    assert selected[0]["content"] == call["content"]
    assert selected[1]["tool_call_id"] == "synthetic-call"


def test_rule_tool_block_dropped_whole_when_budget_runs_out(ledger):
    """正文把预算吃到装不下整块工具时，工具块整体丢弃，不留孤立回执。"""
    chat = "GroupMessage:synthetic"
    # 7 条 user 各 1 token，工具块（call 2 + result 1）共 3 token
    ledger.config_manager._config["context_compression"]["content_retain_tokens"] = 8
    ledger.set_messages(chat, [_call(), _result(), *_users(7)])

    assert ledger._rule_organize(chat)

    messages = ledger.get_all_messages(chat)
    # 预算只够 7 条 user 加 1 token，工具块装不下 → 整块丢
    assert not any(
        message.get("tool_calls") or message["role"] == "tool" for message in messages
    )
    # 工具内容不进历史：摘要只压真人消息
    assert "synthetic result" not in ledger.get_current_summary(chat)
    assert len([message for message in messages if not message.get("kind")]) == 7


def test_rule_minimum_count_fallback_also_keeps_atomic_blocks(ledger):
    """保底回退同样按整块收，不出现半个工具链。"""
    chat = "GroupMessage:synthetic"
    ledger.config_manager._config["context_compression"]["content_retain_tokens"] = 0
    ledger.set_messages(chat, [_call(), _result(), *_users(6)])
    ledger._rule_organize(chat)
    remaining = ledger.get_all_messages(chat)
    call_ids = {
        call.get("id")
        for message in remaining
        for call in (message.get("tool_calls") or [])
    }
    result_ids = {
        message.get("tool_call_id")
        for message in remaining
        if message.get("role") == "tool"
    }
    # 有调用就必须有对应回执，不能只留一半
    assert call_ids == result_ids
    assert "synthetic result" not in ledger.get_current_summary(chat)


def test_split_by_budget_never_splits_tool_exchange(ledger):
    """切边界时工具块整块出，右侧不出现半条链。"""
    msgs = [_call(), _result(), *_users(6)]
    left, right = ledger._split_by_budget(msgs, 0)

    assert left
    assert not any(
        message.get("tool_calls") or message["role"] == "tool" for message in right
    )


@pytest.mark.asyncio
async def test_private_summary_accounts_for_entire_discarded_exchange(ledger):
    chat = "FriendMessage:synthetic"
    call = _call()
    call["cost"] = 100
    ledger.set_messages(chat, [call, _result(), *_users(6)])
    ledger.config_manager._config["context_compression"]["content_retain_tokens"] = 7
    ledger._should_compress = lambda _chat: True
    prompts = []

    async def summarize(prompt):
        prompts.append(prompt)
        return "synthetic summary including tool result"

    assert await ledger.maybe_llm_compress_private(chat, summarize)
    assert "synthetic result" in prompts[0]
    assert not any(
        message["role"] == "tool" for message in ledger.get_all_messages(chat)
    )


def test_global_oldest_message_limit_compresses_oldest_group_chat(ledger):
    """超限时压缩最旧的群聊会话，不删消息。"""
    old_chat = "aiocqhttp:GroupMessage:old"
    new_chat = "aiocqhttp:GroupMessage:new"
    ledger.TOTAL_MESSAGE_LIMIT = 5
    ledger.config_manager._config["context_compression"]["content_retain_tokens"] = 10

    for i in range(1, 11):
        ledger.add_message(
            old_chat,
            {"role": "user", "content": f"old{i}" * 50, "timestamp": float(i)},
        )
    for i in range(20, 30):
        ledger.add_message(
            new_chat,
            {"role": "user", "content": f"new{i}" * 50, "timestamp": float(i)},
        )

    calls = []
    real_organize = ledger.organize_context

    def spy(chat_id, mode="auto", **kwargs):
        calls.append(chat_id)
        return real_organize(chat_id, mode=mode, **kwargs)

    ledger.organize_context = spy
    ledger._enforce_total_message_limit()

    # 先压的是最旧的那个群聊会话，而不是按时间戳删消息
    assert calls and calls[0] == old_chat


def test_selection_preserves_complete_parallel_call_block():
    block = [_call(1, "a", "b"), _result(2, "a"), _result(3, "b")]
    assert retain_complete_history_blocks(block, block[1:]) == []
    assert retain_complete_history_blocks(block, block) == block


def test_existing_orphan_result_becomes_labeled_history_text_without_data_loss():
    source = [_result(text="synthetic historical fact")]
    original = copy.deepcopy(source)
    repaired = normalize_history_tool_pairs(source)
    assert repaired[0]["role"] == "user"
    assert "历史工具结果" in repaired[0]["content"]
    assert "synthetic historical fact" in repaired[0]["content"]
    assert "tool_call_id" not in repaired[0]
    assert source == original
    assert normalize_history_tool_pairs(repaired) == repaired


def test_history_output_boundary_repairs_orphan_without_mutating_source_or_prompt():
    front = object.__new__(FrontDesk)
    source = [_result(text="synthetic historical fact")]
    req = SimpleNamespace(
        contexts=[],
        prompt="synthetic current prompt",
        system_prompt="synthetic persona",
    )
    front._update_request(req, source, "", "synthetic bot")
    assert req.contexts[0]["role"] == "user"
    assert "synthetic historical fact" in req.contexts[0]["content"]
    assert source[0]["role"] == "tool"
    assert req.prompt == "synthetic current prompt"
    assert req.system_prompt == "synthetic persona"


def test_valid_chain_reasoning_and_tool_result_are_unchanged():
    source = [_call(), _result(), *_users(1)]
    original = copy.deepcopy(source)
    repaired = normalize_history_tool_pairs(source)
    assert repaired == original
    assert repaired[0] is source[0]
    assert repaired[1] is source[1]
    assert repaired[0]["content"][0]["type"] == "think"


def test_duplicate_result_fragments_are_combined_without_repeating_protocol_output():
    source = [
        _call(),
        _result(text="synthetic part A"),
        _result(3, text="synthetic part B"),
    ]
    original = copy.deepcopy(source)
    repaired = normalize_history_tool_pairs(source)
    assert len(repaired) == 2
    assert repaired[1]["role"] == "tool"
    assert "synthetic part A" in repaired[1]["content"]
    assert "synthetic part B" in repaired[1]["content"]
    assert source == original


def test_incomplete_parallel_exchange_preserves_calls_and_result_as_history_text():
    source = [_call(1, "a", "b"), _result(2, "a", "synthetic partial result")]
    repaired = normalize_history_tool_pairs(source)
    assert not any(
        message.get("tool_calls") or message["role"] == "tool" for message in repaired
    )
    assert repaired[0]["content"][0]["think"] == "synthetic reasoning"
    assert '"id": "b"' in repaired[0]["content"][1]["text"]
    assert "synthetic partial result" in repaired[1]["content"]


def test_reused_call_id_is_not_replayed_as_a_second_function_call():
    source = [
        _call(),
        _result(),
        *_users(1),
        _call(4),
        _result(5, text="synthetic later fact"),
    ]
    repaired = normalize_history_tool_pairs(source)
    assert sum(bool(message.get("tool_calls")) for message in repaired) == 1
    assert sum(message["role"] == "tool" for message in repaired) == 1
    assert "synthetic later fact" in repaired[-1]["content"]


@pytest.mark.parametrize(
    "calls,result_id",
    [
        ([{"id": ["invalid"], "function": {}}], "synthetic-call"),
        ([{"id": "synthetic-call", "function": {}}], {"invalid": True}),
        (True, "synthetic-call"),
    ],
)
def test_malformed_history_is_preserved_as_text_without_crashing(calls, result_id):
    call = _call()
    call["tool_calls"] = calls
    repaired = normalize_history_tool_pairs([call, _result(call_id=result_id)])
    assert not any(
        message.get("tool_calls") or message["role"] == "tool" for message in repaired
    )
    assert "synthetic result" in repaired[-1]["content"]


def test_duplicate_original_fragment_is_not_repeated_after_merge():
    source = [
        _call(),
        _result(text="synthetic A"),
        _result(3, text="synthetic B"),
        _result(4, text="synthetic B"),
    ]
    result = normalize_history_tool_pairs(source)
    assert len(result) == 2
    assert result[1]["content"].count("synthetic A") == 1
    assert result[1]["content"].count("synthetic B") == 1


@pytest.mark.parametrize("field", ["name", "arguments"])
def test_missing_function_field_is_preserved_as_history_text(field):
    source = _call()
    del source["tool_calls"][0]["function"][field]
    result = normalize_history_tool_pairs([source, _result()])
    assert not any(item.get("tool_calls") or item["role"] == "tool" for item in result)
    assert "synthetic result" in result[-1]["content"]


def test_upstream_pairing_boundary_preserves_incomplete_historical_facts():
    front = FrontDesk.__new__(FrontDesk)
    source = [
        _call(),
        _result(text="synthetic A"),
        _result(3, text="synthetic B"),
        _result(4, text="synthetic B"),
    ]
    result = front._ensure_tool_call_pairing("synthetic", source)
    assert len(result) == 2
    assert result[1]["content"].count("synthetic B") == 1
    orphan = front._ensure_tool_call_pairing(
        "synthetic", [_result(text="synthetic orphan fact")]
    )
    assert "synthetic orphan fact" in orphan[0]["content"]
    assert orphan[0]["role"] == "user"
    missing = front._ensure_tool_call_pairing("synthetic", [_call()])
    assert not any(item.get("tool_calls") or item["role"] == "tool" for item in missing)
    assert "no reply" not in str(missing)
