"""A `cache_control` nested inside a content block counts like any other (#299).

#293 counted the caller's breakpoints and #283 read their TTLs, but both looked
only at the blocks directly in ``tools``/``system``/message ``content``. The API
also takes ``cache_control`` on blocks inside ``tool_result.content``,
``search_result.content`` and a ``document``'s ``source.content``. Measured on
``main``, four marked tool results plus the default ``("system",)`` segment sent:

    caller markers 4, sent 5             (the 400 #293 prevents)
    system 5m ahead of a nested 1h       (the ordering #283 keeps)

The oracle here walks the whole request, so it does not share the wrapper's
notion of where a block can be.
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from cost_optimizer.cache_wrapper import MAX_CACHE_BREAKPOINTS, PromptCacheWrapper

_EPH = {"type": "ephemeral"}
_ONE_HOUR = {"type": "ephemeral", "ttl": "1h"}
_SEGMENT_SETS = [
    combo
    for r in (1, 2, 3)
    for combo in itertools.combinations(("tools", "system", "messages_prefix"), r)
]


def _all_markers(obj: Any, path: str = "") -> list[tuple[str, Any]]:
    """Every `cache_control` anywhere in `obj`, with its path."""
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        if obj.get("cache_control"):
            out.append((path or "<top>", obj["cache_control"]))
        for k, v in obj.items():
            out += _all_markers(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _all_markers(v, f"{path}[{i}]")
    return out


def _text(text: str, marker: dict[str, str] | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if marker is not None:
        block["cache_control"] = dict(marker)
    return block


def _tool_result(i: int, inner: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": f"t{i}", "content": inner}


def _nested(kind: str, marker: dict[str, str]) -> dict[str, Any]:
    """One user-turn content block carrying `marker` one or two levels down."""
    if kind == "tool_result":
        return _tool_result(0, [_text("r", marker)])
    if kind == "search_result":
        search = {"type": "search_result", "source": "s", "title": "t", "content": []}
        search["content"] = [_text("r", marker)]
        return _tool_result(0, [search])
    if kind == "document":
        return {
            "type": "document",
            "source": {"type": "content", "content": [_text("r", marker)]},
        }
    raise AssertionError(kind)


_KINDS = ("tool_result", "search_result", "document")


def _conversation(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`turns` as user messages after an opening one, the last a plain question."""
    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    for i, block in enumerate(turns):
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": f"t{i}", "name": "f", "input": {}}],
            }
        )
        messages.append({"role": "user", "content": [block]})
    messages.append({"role": "assistant", "content": "ok"})
    messages.append({"role": "user", "content": "next"})
    return messages


def _send(segments: tuple[str, ...], request: dict[str, Any]) -> dict[str, Any]:
    wrapper = PromptCacheWrapper(client=None, model="claude-haiku-4-5", cache_segments=segments)  # type: ignore[arg-type]
    return wrapper._apply_cache_control(request)


def _request(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "system": "S",
        "tools": [{"name": "f", "input_schema": {"type": "object"}}],
        "messages": messages,
    }


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("segments", _SEGMENT_SETS, ids="+".join)
def test_four_nested_caller_markers_leave_no_room(kind: str, segments: tuple[str, ...]) -> None:
    request = _request(_conversation([_nested(kind, _EPH) for _ in range(4)]))
    caller = _all_markers(request)
    assert len(caller) == MAX_CACHE_BREAKPOINTS

    sent = _all_markers(_send(segments, request))
    assert sent == caller


@pytest.mark.parametrize("n_nested", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("segments", _SEGMENT_SETS, ids="+".join)
def test_never_more_than_four_and_room_is_still_used(
    n_nested: int, segments: tuple[str, ...]
) -> None:
    request = _request(_conversation([_nested("tool_result", _EPH) for _ in range(n_nested)]))
    sent = _all_markers(_send(segments, request))
    assert len(sent) <= MAX_CACHE_BREAKPOINTS
    # Each segment the wrapper adds is one new marker; it adds as many as fit.
    assert len(sent) == min(MAX_CACHE_BREAKPOINTS, n_nested + len(segments))


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize(
    "segments", [s for s in _SEGMENT_SETS if "messages_prefix" not in s], ids="+".join
)
def test_a_nested_1h_marker_keeps_earlier_wrapper_markers_at_1h(
    kind: str, segments: tuple[str, ...]
) -> None:
    sent = _send(segments, _request(_conversation([_nested(kind, _ONE_HOUR)])))
    ttls = {path.split("[")[0]: marker.get("ttl") for path, marker in _all_markers(sent)}
    for segment in segments:
        # `system`/`tools` render ahead of the caller's 1h entry in `messages`.
        assert ttls[f".{segment}"] == "1h", ttls


def test_a_cache_control_key_in_tool_use_input_is_data_not_a_breakpoint() -> None:
    # `tool_use.input` is the caller's arbitrary JSON; it is not walked.
    messages = _conversation([])
    messages[1:1] = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "t9",
                    "name": "f",
                    "input": {"content": [_text("x", _EPH)], "cache_control": _EPH},
                }
            ],
        },
        {"role": "user", "content": [_tool_result(9, [_text("r")])]},
    ]
    sent = _send(("system", "tools", "messages_prefix"), _request(messages))
    added = [p for p, _ in _all_markers(sent) if ".input" not in p]
    assert len(added) == 3, added
