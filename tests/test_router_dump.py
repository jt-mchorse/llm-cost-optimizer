"""Tests for ``RouterStats.to_dict`` and ``UncertaintyRouter.dump_stats_json`` (#62).

The runtime layer was missing a serialization affordance: aggregate
router activity rolled up across every `route()` call, but no
JSON-stable dict for an observability sink or a tail-able file. These
tests lock the two new surfaces:

- ``RouterStats.to_dict`` returns the raw counter fields plus the
  derived ``escalation_rate`` (key-set lock catches a field added
  without a serializer update or vice versa). The dict round-trips
  through ``json.dumps`` losslessly.
- ``UncertaintyRouter.dump_stats_json`` writes the current stats to
  ``path`` via the package-level atomic-write helper (no half-written
  files on SIGINT / disk-full / OOM). The on-disk shape is sorted-keys
  JSON with a trailing newline.

Sibling to ``test_cache_wrapper_dump.py`` (#50) and
``test_semantic_cache_dump.py`` (#52); same recipe applied to the
last runtime layer that was missing the observability surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cost_optimizer import RouterStats, UncertaintyRouter
from cost_optimizer.router import SignalReading

# ----------------------------------------------------------------------
# Test doubles (kept local; the existing test_router.py doubles aren't
# importable from a sibling test module).
# ----------------------------------------------------------------------


@dataclass
class _FakeResponse:
    text: str = ""


@dataclass
class _StubAdapter:
    response: _FakeResponse
    calls: list[Any] = field(default_factory=list)

    def call_cheap(self, request: Any) -> Any:
        self.calls.append(request)
        return self.response


@dataclass
class _ConstantSignal:
    name: str
    reading: SignalReading

    def measure(self, response: Any) -> SignalReading:  # noqa: ARG002
        return self.reading


def _build_router(*signals: _ConstantSignal) -> UncertaintyRouter:
    return UncertaintyRouter(
        cheap_model="claude-haiku-4-5",
        strong_model="claude-sonnet-4-6",
        cheap_adapter=_StubAdapter(_FakeResponse(text="ok")),
        signals=list(signals),
    )


# --- RouterStats.to_dict --------------------------------------------------


def test_to_dict_returns_full_field_set_plus_derived() -> None:
    """Key set must match the dataclass fields plus the derived
    ``escalation_rate``. If a new field lands on ``RouterStats``
    without ``to_dict`` learning about it the dict would silently
    drop the new value."""
    s = RouterStats(
        total_routes=10,
        escalations=3,
        cheap_only=7,
        per_signal_trips={"entropy": 2, "judge": 1},
        per_signal_measured={"entropy": 9, "judge": 8},
    )
    payload = s.to_dict()
    assert set(payload) == {
        "total_routes",
        "escalations",
        "cheap_only",
        "per_signal_trips",
        "per_signal_measured",
        "escalation_rate",
    }
    assert payload["total_routes"] == 10
    assert payload["escalations"] == 3
    assert payload["cheap_only"] == 7
    assert payload["per_signal_trips"] == {"entropy": 2, "judge": 1}
    assert payload["per_signal_measured"] == {"entropy": 9, "judge": 8}
    assert payload["escalation_rate"] == 0.3


def test_to_dict_round_trips_through_json_dumps() -> None:
    """Round-trip safety — every value the dataclass carries must
    survive a ``json.dumps`` / ``json.loads`` cycle. Anything else
    would silently lose precision or fail at the sink."""
    s = RouterStats(
        total_routes=4,
        escalations=1,
        cheap_only=3,
        per_signal_trips={"entropy": 1},
        per_signal_measured={"entropy": 4},
    )
    serialized = json.dumps(s.to_dict(), sort_keys=True)
    parsed = json.loads(serialized)
    assert parsed == s.to_dict()


def test_to_dict_on_zero_stats_is_full_shape_with_zero_rate() -> None:
    """Cold-start case: a fresh router has zeroed stats. The dict
    must still carry every key so a consumer scanning
    ``payload["escalation_rate"]`` doesn't KeyError on the first
    observation; rate must be 0.0 (not NaN) on an empty divisor."""
    payload = RouterStats().to_dict()
    assert payload == {
        "total_routes": 0,
        "escalations": 0,
        "cheap_only": 0,
        "per_signal_trips": {},
        "per_signal_measured": {},
        "escalation_rate": 0.0,
    }


# --- UncertaintyRouter accumulation ---------------------------------------


def test_route_increments_total_and_cheap_only_on_no_trip() -> None:
    """A signal that doesn't trip on any call keeps the router on
    cheap and increments the no-escalation counter."""
    sig = _ConstantSignal("entropy", SignalReading(value=0.1, trip=False))
    r = _build_router(sig)
    r.route(object())
    r.route(object())
    assert r.stats.total_routes == 2
    assert r.stats.escalations == 0
    assert r.stats.cheap_only == 2
    # Signal returned a non-None reading both times, so per_signal_measured counts it.
    assert r.stats.per_signal_measured == {"entropy": 2}
    assert r.stats.per_signal_trips == {}
    assert r.stats.escalation_rate == 0.0


def test_route_attributes_first_trip_per_signal() -> None:
    """Each escalation is attributed to the *first* signal that
    tripped on that call — same first-trip-wins semantics
    ``RouterDecision.triggered_signal`` already commits to."""
    first = _ConstantSignal("entropy", SignalReading(value=2.5, trip=True))
    second = _ConstantSignal("judge", SignalReading(value=0.1, trip=True))
    r = _build_router(first, second)
    r.route(object())
    r.route(object())
    assert r.stats.total_routes == 2
    assert r.stats.escalations == 2
    assert r.stats.cheap_only == 0
    # First-trip-wins: only ``entropy`` is credited; ``judge`` still
    # ran (per the first-trip-wins comment that "measuring all signals
    # so telemetry sees every value" is the contract) so it shows up
    # in per_signal_measured but not per_signal_trips.
    assert r.stats.per_signal_trips == {"entropy": 2}
    assert r.stats.per_signal_measured == {"entropy": 2, "judge": 2}
    assert r.stats.escalation_rate == 1.0


def test_route_distinguishes_couldnt_measure_from_didnt_trip() -> None:
    """When a signal returns ``value=None`` (couldn't measure) it must
    NOT increment ``per_signal_measured`` — the dict carries the
    ``didn't trip`` vs ``couldn't measure`` distinction that
    ``RouterDecision.signal_values`` already preserves at the per-call
    layer."""
    measurable = _ConstantSignal("judge", SignalReading(value=0.4, trip=False))
    unmeasurable = _ConstantSignal("entropy", SignalReading(value=None, trip=False))
    r = _build_router(measurable, unmeasurable)
    r.route(object())
    r.route(object())
    assert r.stats.per_signal_measured == {"judge": 2}
    assert r.stats.per_signal_trips == {}


# --- UncertaintyRouter.dump_stats_json -------------------------------------


def test_dump_stats_json_writes_file_with_stats_shape(tmp_path: Path) -> None:
    """Writer produces the dict shape on disk with sorted keys and a
    trailing newline. The file is a self-contained JSON document a
    log-tailer can parse."""
    sig = _ConstantSignal("entropy", SignalReading(value=2.5, trip=True))
    r = _build_router(sig)
    r.route(object())
    r.route(object())

    out = tmp_path / "router-stats.json"
    r.dump_stats_json(out)
    body = out.read_text(encoding="utf-8")
    assert body.endswith("\n"), "must end with a trailing newline"
    payload = json.loads(body)
    assert set(payload) == {
        "total_routes",
        "escalations",
        "cheap_only",
        "per_signal_trips",
        "per_signal_measured",
        "escalation_rate",
    }
    assert payload["total_routes"] == 2
    assert payload["escalations"] == 2
    assert payload["per_signal_trips"] == {"entropy": 2}
    assert payload["escalation_rate"] == 1.0


def test_dump_stats_json_creates_parent_dirs(tmp_path: Path) -> None:
    """``atomic_write_text`` does ``parent.mkdir(parents=True)``;
    confirm the writer inherits that behavior so callers don't have
    to pre-create a nested observability directory."""
    sig = _ConstantSignal("entropy", SignalReading(value=0.0, trip=False))
    r = _build_router(sig)
    out = tmp_path / "nested" / "sink" / "router-stats.json"
    r.dump_stats_json(out)
    assert out.exists()
    assert out.parent.is_dir()


def test_dump_stats_json_overwrites_atomically(tmp_path: Path) -> None:
    """Two successive dumps to the same path leave the second payload —
    not the concatenation, not a half-written file. ``os.replace``
    semantics make this atomic on POSIX.
    """
    sig = _ConstantSignal("entropy", SignalReading(value=2.5, trip=True))
    r = _build_router(sig)
    out = tmp_path / "router-stats.json"
    r.dump_stats_json(out)
    body1 = out.read_text(encoding="utf-8")
    r.route(object())
    r.dump_stats_json(out)
    body2 = out.read_text(encoding="utf-8")
    assert body1 != body2
    # No tempfiles left behind under the destination's parent.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], leftovers
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".router-stats.json.")]
    assert leftovers == [], leftovers


def test_dump_stats_json_zero_stats_writes_empty_shape(tmp_path: Path) -> None:
    """A router that's never been called still produces a valid JSON
    document — useful for canary-mode observability checks."""
    sig = _ConstantSignal("entropy", SignalReading(value=0.0, trip=False))
    r = _build_router(sig)
    out = tmp_path / "router-stats.json"
    r.dump_stats_json(out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload == {
        "total_routes": 0,
        "escalations": 0,
        "cheap_only": 0,
        "per_signal_trips": {},
        "per_signal_measured": {},
        "escalation_rate": 0.0,
    }
