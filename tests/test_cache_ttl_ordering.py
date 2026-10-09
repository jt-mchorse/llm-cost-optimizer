"""The wrapper never puts a 5-minute marker ahead of a caller's 1-hour one (#283).

Anthropic's prompt-caching rule for mixed TTLs: a 1-hour cache entry must appear
before any 5-minute entry, in render order ``tools`` -> ``system`` ->
``messages``, with top-level automatic caching landing after all three. #271 made
the wrapper keep a caller's ``ttl: "1h"`` marker, but it still added its own
5-minute default to every unmarked segment -- including segments rendered ahead
of that marker. Measured on ``main``:

    default ("system",),            caller top-level 1h  -> system 5m, top-level 1h
    ("tools", "system"),            caller system 1h     -> tools 5m, system 1h
    ("system", "messages_prefix"),  caller messages 1h   -> system 5m, messages 1h

The wrapper's own marker now takes ``ttl: "1h"`` exactly when a caller 1h marker
renders after it, and keeps the 5-minute default everywhere else.
"""

from __future__ import annotations

import itertools
import types
from typing import Any

import pytest

from cost_optimizer.cache_wrapper import PromptCacheWrapper

ONE_HOUR = {"type": "ephemeral", "ttl": "1h"}
MODEL = "claude-opus-4-8"
_PLACES = ("tools", "system", "messages", "top_level")
_SEGMENT_SETS = [
    combo
    for r in (1, 2, 3)
    for combo in itertools.combinations(("tools", "system", "messages_prefix"), r)
]


class _Recording:
    def __init__(self) -> None:
        self.sent: dict[str, Any] = {}
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.sent = kwargs
        return types.SimpleNamespace(
            usage=types.SimpleNamespace(cache_creation_input_tokens=0, cache_read_input_tokens=0)
        )


def _request(caller_1h_at: str | None) -> dict[str, Any]:
    """A request with every segment present and a caller 1h marker at one place."""
    tool: dict[str, Any] = {"name": "t", "input_schema": {}}
    system: list[dict[str, Any]] = [{"type": "text", "text": "policy"}]
    block: dict[str, Any] = {"type": "text", "text": "q"}
    kwargs: dict[str, Any] = {
        "tools": [tool],
        "system": system,
        "messages": [{"role": "user", "content": [block]}],
        "max_tokens": 8,
    }
    if caller_1h_at == "tools":
        tool["cache_control"] = ONE_HOUR
    elif caller_1h_at == "system":
        system[0]["cache_control"] = ONE_HOUR
    elif caller_1h_at == "messages":
        block["cache_control"] = ONE_HOUR
    elif caller_1h_at == "top_level":
        kwargs["cache_control"] = ONE_HOUR
    return kwargs


def _breakpoints(sent: dict[str, Any]) -> list[tuple[str, str]]:
    """``(place, ttl)`` for every marker in the emitted request, in render order."""
    out: list[tuple[str, str]] = []
    for place, blocks in (
        ("tools", sent.get("tools") or []),
        ("system", sent.get("system") if isinstance(sent.get("system"), list) else []),
        ("messages", [b for m in sent.get("messages") or [] for b in m["content"]]),
    ):
        for b in blocks:
            if b.get("cache_control"):
                out.append((place, b["cache_control"].get("ttl", "5m")))
    if sent.get("cache_control"):
        out.append(("top_level", sent["cache_control"].get("ttl", "5m")))
    return out


def _send(segments: tuple[str, ...], caller_1h_at: str | None) -> list[tuple[str, str]]:
    client = _Recording()
    PromptCacheWrapper(client, MODEL, cache_segments=segments).create(**_request(caller_1h_at))
    return _breakpoints(client.sent)


@pytest.mark.parametrize(
    ("segments", "caller_1h_at", "expected"),
    [
        (("system",), "top_level", [("system", "1h"), ("top_level", "1h")]),
        (("tools", "system"), "system", [("tools", "1h"), ("system", "1h")]),
        (("system", "messages_prefix"), "messages", [("system", "1h"), ("messages", "1h")]),
    ],
)
def test_the_three_reported_configurations(
    segments: tuple[str, ...], caller_1h_at: str, expected: list[tuple[str, str]]
) -> None:
    assert _send(segments, caller_1h_at) == expected


@pytest.mark.parametrize("caller_1h_at", [None, *_PLACES])
@pytest.mark.parametrize("segments", _SEGMENT_SETS)
def test_no_5m_marker_precedes_a_1h_marker(
    segments: tuple[str, ...], caller_1h_at: str | None
) -> None:
    ttls = [ttl for _, ttl in _send(segments, caller_1h_at)]
    first_5m = ttls.index("5m") if "5m" in ttls else len(ttls)
    assert "1h" not in ttls[first_5m:], ttls


@pytest.mark.parametrize("segments", _SEGMENT_SETS)
def test_without_a_caller_1h_marker_every_wrapper_marker_is_the_default(
    segments: tuple[str, ...],
) -> None:
    # Control: no caller asked for 1h, so nobody pays the 2x write.
    points = _send(segments, None)
    assert points
    assert all(ttl == "5m" for _, ttl in points)


def test_a_caller_1h_marker_earlier_in_render_order_leaves_later_defaults_alone() -> None:
    # 1h before 5m is the allowed order, so the later marker stays 5-minute.
    points = _send(("tools", "system", "messages_prefix"), "tools")
    assert points == [("tools", "1h"), ("system", "5m"), ("messages", "5m")]


def test_a_string_system_prompt_is_promoted_with_the_1h_ttl_when_needed() -> None:
    client = _Recording()
    PromptCacheWrapper(client, MODEL).create(
        system="policy", messages=[{"role": "user", "content": "q"}], cache_control=ONE_HOUR
    )
    assert client.sent["system"] == [{"type": "text", "text": "policy", "cache_control": ONE_HOUR}]


def test_a_5m_or_malformed_top_level_marker_does_not_upgrade() -> None:
    for marker in ({"type": "ephemeral"}, {"type": "ephemeral", "ttl": "5m"}, "1h", None):
        client = _Recording()
        PromptCacheWrapper(client, MODEL).create(
            system="policy", messages=[{"role": "user", "content": "q"}], cache_control=marker
        )
        assert client.sent["system"][0]["cache_control"] == {"type": "ephemeral"}, marker
