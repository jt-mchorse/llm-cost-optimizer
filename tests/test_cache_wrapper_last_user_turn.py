"""`messages_prefix` marks the last user turn, not whatever comes last (#373).

Claude's mid-conversation operator channel appends a ``{"role": "system", ...}``
message after the last user turn. ``_mark_messages_prefix`` used to mark
``messages[-1]`` regardless of role, so:

* a ``clear_at: "next_user_message"`` system message got the marker -- a 400,
  because a turn-scoped message is not cache-eligible and the breakpoint goes on
  the preceding user turn;
* an effort-only system message (``content: []``) had no block to mark, so the
  request carried no messages breakpoint at all and nothing was cached.

These go through ``PromptCacheWrapper.create`` with a fake client, so the
request shape asserted is the one that would have been sent.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from cost_optimizer import PromptCacheWrapper
from cost_optimizer.cache_wrapper import (
    MAX_CACHE_BREAKPOINTS,
    _count_breakpoints,
    _mark_messages_prefix,
)

_EPH = {"type": "ephemeral"}


class _Messages:
    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.sent = kwargs
        return SimpleNamespace(usage=None)


def _send(messages: list[dict[str, Any]], **extra: Any) -> list[dict[str, Any]]:
    client = SimpleNamespace(messages=_Messages())
    wrapper = PromptCacheWrapper(
        client,  # type: ignore[arg-type]
        "claude-opus-5-5",
        cache_segments=extra.pop("segments", ("messages_prefix",)),
    )
    wrapper.create(messages=messages, max_tokens=16, **extra)
    sent: list[dict[str, Any]] = client.messages.sent["messages"]
    return sent


def _history() -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "q2"},
    ]


_Q2_MARKED = [{"type": "text", "text": "q2", "cache_control": _EPH}]

_CLEAR_AT = {"role": "system", "content": "reminder", "clear_at": "next_user_message"}
_EFFORT_ONLY = {"role": "system", "content": [], "output_config": {"effort": "low"}}
_PLAIN_SYSTEM = {"role": "system", "content": "terse mode"}
_BLOCK_SYSTEM = {"role": "system", "content": [{"type": "text", "text": "terse mode"}]}

_TRAILERS = {
    "clear_at": [_CLEAR_AT],
    "effort_only": [_EFFORT_ONLY],
    "plain_text_system": [_PLAIN_SYSTEM],
    "block_system": [_BLOCK_SYSTEM],
    "effort_then_clear_at": [_EFFORT_ONLY, _CLEAR_AT],
    "clear_at_then_plain": [_CLEAR_AT, _PLAIN_SYSTEM],
}


@pytest.mark.parametrize("trailer", list(_TRAILERS.values()), ids=list(_TRAILERS))
def test_marker_goes_on_the_last_user_turn_before_trailing_system_messages(
    trailer: list[dict[str, Any]],
) -> None:
    sent = _send(_history() + copy.deepcopy(trailer))
    assert sent[2] == {"role": "user", "content": _Q2_MARKED}
    # The trailing operator messages go out exactly as the caller wrote them.
    assert sent[3:] == trailer
    # And there is exactly one messages breakpoint: the request is cached.
    assert _count_breakpoints({"messages": sent}) == 1


def test_plain_user_last_control() -> None:
    """No trailer: the last message is the user turn and is marked, as before."""
    sent = _send(_history())
    assert sent[-1] == {"role": "user", "content": _Q2_MARKED}
    assert sent[:2] == _history()[:2]


def test_trailing_assistant_is_skipped_too() -> None:
    sent = _send(_history() + [{"role": "assistant", "content": "prefill"}])
    assert sent[2] == {"role": "user", "content": _Q2_MARKED}
    assert sent[3] == {"role": "assistant", "content": "prefill"}


def test_tool_result_user_turn_marks_its_last_block() -> None:
    """A tool_result turn is a user turn; its last block is the target."""
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "q1"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": "f", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "42"}],
        },
        dict(_CLEAR_AT),
    ]
    sent = _send(messages)
    assert sent[2]["content"][-1]["cache_control"] == _EPH
    assert "cache_control" not in sent[3]
    assert sent[3] == _CLEAR_AT


def test_caller_marker_on_the_user_turn_is_kept_and_costs_no_slot() -> None:
    one_hour = {"type": "ephemeral", "ttl": "1h"}
    messages = _history()
    messages[2] = {
        "role": "user",
        "content": [{"type": "text", "text": "q2", "cache_control": one_hour}],
    }
    sent = _send(messages + [dict(_CLEAR_AT)])
    assert sent[2]["content"][0]["cache_control"] == one_hour
    assert _count_breakpoints({"messages": sent}) == 1


def test_no_user_message_is_left_unmarked() -> None:
    only_assistant = [{"role": "assistant", "content": "hi"}]
    assert _mark_messages_prefix(only_assistant) == only_assistant


def test_budget_full_does_not_mark_and_trailer_is_untouched() -> None:
    """Three caller markers + automatic caching = four: the wrapper adds none.

    The budget counts the marked result, so it has to see the user turn as the
    one it would add, and still leave the trailing system message alone.
    """
    messages: list[dict[str, Any]] = []
    for i in range(3):
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": f"q{i}", "cache_control": dict(_EPH)}],
            }
        )
        messages.append({"role": "assistant", "content": f"a{i}"})
    messages.append({"role": "user", "content": "q3"})
    messages.append(dict(_CLEAR_AT))
    sent = _send(copy.deepcopy(messages), cache_control=dict(_EPH))
    assert sent == messages
    assert _count_breakpoints({"messages": sent, "cache_control": _EPH}) == MAX_CACHE_BREAKPOINTS


def test_budget_with_room_marks_the_user_turn() -> None:
    """Automatic caching + two caller markers = three: the wrapper's one fits."""
    messages: list[dict[str, Any]] = []
    for i in range(2):
        messages.append(
            {
                "role": "user",
                "content": [{"type": "text", "text": f"q{i}", "cache_control": dict(_EPH)}],
            }
        )
        messages.append({"role": "assistant", "content": f"a{i}"})
    messages.append({"role": "user", "content": "q2"})
    messages.append(dict(_CLEAR_AT))
    sent = _send(messages, cache_control=dict(_EPH))
    assert sent[4] == {"role": "user", "content": _Q2_MARKED}
    assert sent[5] == _CLEAR_AT
    assert _count_breakpoints({"messages": sent, "cache_control": _EPH}) == MAX_CACHE_BREAKPOINTS
