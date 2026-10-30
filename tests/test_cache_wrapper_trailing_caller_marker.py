"""A caller's marker after the last user turn keeps the #283 TTL order (#375).

#374 moved the ``messages_prefix`` target from ``messages[-1]`` to the last
``role == "user"`` message. ``ttl_for`` treats all of ``messages`` as one render
position, so a caller's 1h marker on a trailing system message or assistant
prefill never upgraded the wrapper's marker, and the request went out with the
wrapper's 5m breakpoint on the user turn ahead of the caller's 1h one. Anthropic
requires a 1-hour entry to come before any 5-minute entry.

The sweep crosses caller marker {none, 5m, 1h} with where it sits {the target
user turn, a trailing system message, a trailing assistant prefill, an earlier
message}, and with the ``system`` and ``tools`` segments on or off. Every case
checks the request in render order (tools -> system -> messages -> top-level):
no 5m breakpoint before a 1h one, and at most four breakpoints. When the
caller's marker is on the last message, the result must equal what the
pre-#374 ``_mark_messages_prefix`` (target ``messages[-1]``) produced. That
version is copied below as the oracle.
"""

from __future__ import annotations

import copy
import itertools
from typing import Any

import pytest

from cost_optimizer import cache_wrapper
from cost_optimizer.cache_wrapper import (
    MAX_CACHE_BREAKPOINTS,
    PromptCacheWrapper,
    _ephemeral_cache_control,
    _mark_messages_prefix,
    _walk_blocks,
    _with_cache_control,
)
from cost_optimizer.shapes import is_block_sequence

_5M = {"type": "ephemeral"}
_1H = {"type": "ephemeral", "ttl": "1h"}
_MARKERS = {"none": None, "5m": _5M, "1h": _1H}
_POSITIONS = ("target", "trailing_system", "trailing_assistant", "earlier")
_ON_LAST_MESSAGE = ("target", "trailing_system", "trailing_assistant")
_EXTRA_SEGMENTS = ((), ("system",), ("tools",), ("tools", "system"))


def _pre_374_mark_messages_prefix(messages: list[Any], *, ttl: str | None = None) -> list[Any]:
    """``_mark_messages_prefix`` as it was before #374: the target is ``messages[-1]``."""
    if not messages:
        return messages
    new = [dict(m) for m in messages]
    target = new[-1]
    content = target.get("content")
    if isinstance(content, str):
        target["content"] = [
            {"type": "text", "text": content, "cache_control": _ephemeral_cache_control(ttl)}
        ]
    elif is_block_sequence(content) and content:
        new_content = [dict(b) for b in content]
        new_content[-1] = _with_cache_control(new_content[-1], ttl)
        target["content"] = new_content
    new[-1] = target
    return new


def _block(text: str, marker: dict[str, str] | None) -> list[dict[str, Any]]:
    b: dict[str, Any] = {"type": "text", "text": text}
    if marker is not None:
        b["cache_control"] = dict(marker)
    return [b]


def _request(marker: dict[str, str] | None, position: str) -> dict[str, Any]:
    """``[user q1, assistant ok, user q2]`` plus the trailer ``position`` names.

    The caller's marker (if any) goes on the last block of the message
    ``position`` names: ``earlier`` is ``user q1``.
    """
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": _block("q1", marker if position == "earlier" else None)},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": _block("q2", marker if position == "target" else None)},
    ]
    if position == "trailing_system":
        messages.append({"role": "system", "content": _block("terse mode", marker)})
    elif position == "trailing_assistant":
        messages.append({"role": "assistant", "content": _block("Sure,", marker)})
    return {
        "tools": [{"name": "t", "input_schema": {"type": "object"}}],
        "system": "S",
        "messages": messages,
        "max_tokens": 16,
    }


def _wrapper(segments: tuple[str, ...]) -> PromptCacheWrapper:
    return PromptCacheWrapper(client=None, model="claude-opus-5-5", cache_segments=segments)  # type: ignore[arg-type]


def _rendered_ttls(req: dict[str, Any]) -> list[str]:
    """Every breakpoint's TTL in render order: tools, system, messages, top-level."""
    blocks: list[Any] = list(_walk_blocks(req.get("tools")))
    blocks += list(_walk_blocks(req.get("system")))
    for m in req["messages"]:
        blocks += list(_walk_blocks(m.get("content")))
    markers = [b["cache_control"] for b in blocks if b.get("cache_control")]
    if req.get("cache_control"):
        markers.append(req["cache_control"])
    return ["1h" if m.get("ttl") == "1h" else "5m" for m in markers]


_CASES = [
    pytest.param(marker, position, extra, id=f"{marker}-{position}-{'+'.join(extra) or 'msgs'}")
    for marker, position, extra in itertools.product(_MARKERS, _POSITIONS, _EXTRA_SEGMENTS)
]


@pytest.mark.parametrize(("marker", "position", "extra"), _CASES)
def test_no_5m_breakpoint_renders_before_a_1h_one_and_at_most_four(
    marker: str, position: str, extra: tuple[str, ...]
) -> None:
    out = _wrapper(("messages_prefix", *extra))._apply_cache_control(
        _request(_MARKERS[marker], position)
    )
    ttls = _rendered_ttls(out)
    first_5m = ttls.index("5m") if "5m" in ttls else len(ttls)
    assert "1h" not in ttls[first_5m:], ttls
    assert len(ttls) <= MAX_CACHE_BREAKPOINTS


@pytest.mark.parametrize(
    ("marker", "position", "extra"),
    [p for p in _CASES if p.values[0] != "none" and p.values[1] in _ON_LAST_MESSAGE],
)
def test_caller_marker_on_the_last_message_matches_pre_374(
    marker: str, position: str, extra: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    segments = ("messages_prefix", *extra)
    req = _request(_MARKERS[marker], position)
    out = _wrapper(segments)._apply_cache_control(copy.deepcopy(req))
    monkeypatch.setattr(cache_wrapper, "_mark_messages_prefix", _pre_374_mark_messages_prefix)
    expected = _wrapper(segments)._apply_cache_control(copy.deepcopy(req))
    assert out == expected
    # Concretely: the caller's marker is the only messages breakpoint, so the
    # messages go out exactly as written.
    assert out["messages"] == req["messages"]


def test_issue_repro_trailing_system_1h() -> None:
    """The #375 report: a trailing system message with the caller's 1h marker."""
    req = _request(_1H, "trailing_system")
    out = _wrapper(("messages_prefix",))._apply_cache_control(copy.deepcopy(req))
    assert out["messages"] == req["messages"]
    assert _rendered_ttls(out) == ["1h"]


def test_trailing_marker_with_system_segment_upgrades_system_to_1h() -> None:
    """The system marker still renders ahead of the caller's 1h, so it is 1h too (#283)."""
    out = _wrapper(("messages_prefix", "system", "tools"))._apply_cache_control(
        _request(_1H, "trailing_assistant")
    )
    assert _rendered_ttls(out) == ["1h", "1h", "1h"]


def test_caller_marker_before_the_target_still_gets_the_wrapper_marker() -> None:
    """Control: a marker on an earlier turn does not cover the newest prefix, so we still mark."""
    out = _wrapper(("messages_prefix",))._apply_cache_control(_request(_1H, "earlier"))
    assert out["messages"][2]["content"][-1]["cache_control"] == _5M
    assert _rendered_ttls(out) == ["1h", "5m"]


def test_unmarked_trailer_still_marks_the_user_turn() -> None:
    """Control: #374's behaviour stands when nothing after the target is marked."""
    out = _mark_messages_prefix(_request(None, "trailing_system")["messages"])
    assert out[2]["content"][-1]["cache_control"] == _5M
    assert "cache_control" not in out[3]["content"][-1]


def test_nested_caller_marker_after_the_target_counts() -> None:
    """A marker inside a trailing message's nested content blocks is a breakpoint too (#299)."""
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "q"},
        {
            "role": "system",
            "content": [
                {
                    "type": "search_result",
                    "source": "s",
                    "title": "t",
                    "content": [{"type": "text", "text": "x", "cache_control": dict(_1H)}],
                }
            ],
        },
    ]
    assert _mark_messages_prefix(copy.deepcopy(messages)) == messages
