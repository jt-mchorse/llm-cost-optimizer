"""`PromptCacheWrapper` never sends more than four cache breakpoints (#293).

The Messages API accepts at most four ``cache_control`` breakpoints per request,
and top-level automatic caching uses one of the slots; more is a 400. The
wrapper added up to three markers of its own without counting the caller's, so
the usual incremental multi-turn pattern -- automatic caching plus a marker on
an earlier turn -- went out with five.

The sweep below crosses every caller-marker shape with every segment subset and
asserts the budget, that no caller marker is dropped or rewritten, and that the
wrapper still marks everything it can when there is room.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from cost_optimizer.cache_wrapper import (
    MAX_CACHE_BREAKPOINTS,
    PromptCacheWrapper,
    _count_breakpoints,
)

_EPH = {"type": "ephemeral"}
_SEGMENTS = ("system", "tools", "messages_prefix")


def _wrapper(segments: tuple[str, ...]) -> PromptCacheWrapper:
    return PromptCacheWrapper(client=None, model="claude-haiku-4-5", cache_segments=segments)  # type: ignore[arg-type]


def _request(*, top: bool, n_history_marks: int, system_marked: bool) -> dict[str, Any]:
    """A five-turn conversation with `n_history_marks` caller markers on earlier user turns."""
    messages: list[dict[str, Any]] = []
    for i in range(3):
        block: dict[str, Any] = {"type": "text", "text": f"q{i}"}
        if i < n_history_marks:
            block["cache_control"] = dict(_EPH)
        messages.append({"role": "user", "content": [block]})
        if i < 2:
            messages.append({"role": "assistant", "content": f"a{i}"})
    # The last user turn is the wrapper's target; keep it unmarked by the caller.
    messages[-1]["content"][0].pop("cache_control", None)
    system: Any = "S"
    if system_marked:
        system = [{"type": "text", "text": "S", "cache_control": dict(_EPH)}]
    req: dict[str, Any] = {
        "system": system,
        "tools": [{"name": "t", "input_schema": {"type": "object"}}],
        "messages": messages,
    }
    if top:
        req["cache_control"] = dict(_EPH)
    return req


def _caller_marker_paths(req: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if req.get("cache_control"):
        out["<top>"] = req["cache_control"]
    if isinstance(req["system"], list):
        for i, b in enumerate(req["system"]):
            if b.get("cache_control"):
                out[f"system[{i}]"] = b["cache_control"]
    for mi, m in enumerate(req["messages"]):
        if isinstance(m["content"], list):
            for bi, b in enumerate(m["content"]):
                if b.get("cache_control"):
                    out[f"messages[{mi}][{bi}]"] = b["cache_control"]
    return out


def test_the_reported_request_is_cut_to_four_keeping_the_latest() -> None:
    req = _request(top=True, n_history_marks=1, system_marked=False)
    sent = _wrapper(_SEGMENTS)._apply_cache_control(req)
    assert _count_breakpoints(req) == 2
    assert _count_breakpoints(sent) == MAX_CACHE_BREAKPOINTS
    # The last user turn and the system prompt are marked; tools, the earliest
    # render position, is the one left out.
    assert sent["messages"][-1]["content"][-1]["cache_control"] == _EPH
    assert sent["system"][-1]["cache_control"] == _EPH
    assert "cache_control" not in sent["tools"][-1]


@pytest.mark.parametrize("top", [False, True])
@pytest.mark.parametrize("n_history_marks", [0, 1, 2, 3])
@pytest.mark.parametrize("system_marked", [False, True])
@pytest.mark.parametrize(
    "segments",
    [c for r in range(1, 4) for c in itertools.combinations(_SEGMENTS, r)],
    ids=lambda c: "+".join(c),
)
def test_never_more_than_four_and_caller_markers_are_untouched(
    top: bool, n_history_marks: int, system_marked: bool, segments: tuple[str, ...]
) -> None:
    req = _request(top=top, n_history_marks=n_history_marks, system_marked=system_marked)
    caller = _count_breakpoints(req)
    sent = _wrapper(segments)._apply_cache_control(req)
    if caller <= MAX_CACHE_BREAKPOINTS:
        assert _count_breakpoints(sent) <= MAX_CACHE_BREAKPOINTS
    else:
        # Over budget before the wrapper touched it: the wrapper adds nothing.
        assert _count_breakpoints(sent) == caller
    before = _caller_marker_paths(req)
    after = _caller_marker_paths(sent)
    assert {k: after[k] for k in before} == before


@pytest.mark.parametrize(
    "segments",
    [c for r in range(1, 4) for c in itertools.combinations(_SEGMENTS, r)],
    ids=lambda c: "+".join(c),
)
def test_with_room_every_requested_segment_is_still_marked(segments: tuple[str, ...]) -> None:
    # Control: no caller markers, so every segment fits and nothing changes.
    req = _request(top=False, n_history_marks=0, system_marked=False)
    sent = _wrapper(segments)._apply_cache_control(req)
    assert _count_breakpoints(sent) == len(segments)
