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

import pytest

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
        "per_signal_errors",
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
        "per_signal_errors": {},
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
        "per_signal_errors",
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
        "per_signal_errors": {},
        "escalation_rate": 0.0,
    }


# ----------------------------------------------------------------------
# A signal that RAISES is isolated from the paid cheap call (#184)
#
# #94/#106/#95/#112/#118 each taught one signal to abstain on a malformed
# return *value*. None covered the signal raising, which is the half the
# documented BYO extension points actually produce: `EscalationSignal` is a
# public Protocol, and `JudgeConfidenceSignal.judge` is the
# `eval_harness.Judge` seam (D-002), whose `.score()` raises `JudgeParseError`
# in three places.
#
# Measured pre-fix, from the probe in #184:
#
#   judge RAISES JudgeParseError -> JudgeParseError escapes route()
#                                   cheap calls paid for = 1
#                                   total_routes recorded = 0
#   BYO signal raises            -> RuntimeError escapes route()
#                                   cheap calls paid for = 1
#                                   total_routes recorded = 0
#
# Both assertions below are anchored to that: the response was already paid
# for, and the stats block sat after the loop so the failed route never
# reached the escalation_rate denominator.
# ----------------------------------------------------------------------


@dataclass
class _RaisingSignal:
    name: str
    exc: BaseException

    def measure(self, response: Any) -> SignalReading:  # noqa: ARG002
        raise self.exc


class _JudgeParseError(ValueError):
    """Shape-identical to ``eval_harness.judge.JudgeParseError``.

    Constructed locally rather than imported: llm-eval-harness is not a
    dependency of this repo (the judge is duck-typed by design, D-002), so
    the test pins the *shape* the seam raises, not the class identity.
    """


def test_raising_signal_does_not_destroy_the_paid_cheap_response() -> None:
    # Pre-fix this raised straight out of route(), discarding a completed,
    # billed cheap-model response and handing the caller nothing.
    sig = _RaisingSignal("judge", _JudgeParseError("missing SCORE: line in judge output"))
    adapter = _StubAdapter(_FakeResponse(text="the cheap model's completed answer"))
    r = UncertaintyRouter(
        cheap_model="claude-haiku-4-5",
        strong_model="claude-sonnet-4-6",
        cheap_adapter=adapter,
        signals=[sig],
    )
    decision = r.route(object())

    # The paid response survives and reaches the caller.
    assert len(adapter.calls) == 1, "the cheap call was made and billed"
    assert decision.cheap_response.text == "the cheap model's completed answer"
    # A signal that couldn't measure must not escalate — same contract the
    # None/non-finite/non-numeric branches inside the signals honor.
    assert decision.model_id == "claude-haiku-4-5"
    assert decision.triggered_signal is None
    assert decision.signal_values == {"judge": None}


def test_raising_signal_still_counts_toward_the_escalation_rate_denominator() -> None:
    # Pre-fix: total_routes == 0 after a raising route, so every failed route
    # was missing from escalation_rate's *denominator* and the rate reported
    # to dump_stats_json / docs/savings.json / the dashboard was computed over
    # a silently truncated sample — a router whose judge fails intermittently
    # reported a better-looking escalation rate than reality.
    sig = _RaisingSignal("judge", _JudgeParseError("unparseable score"))
    r = _build_router(sig)
    r.route(object())
    r.route(object())

    assert r.stats.total_routes == 2
    assert r.stats.cheap_only == 2
    assert r.stats.escalations == 0
    assert r.stats.escalation_rate == 0.0


def test_raising_signal_is_recorded_not_silently_swallowed() -> None:
    # The counter is the reason this fix isn't just "trade a loud failure for
    # a silent one": `signal_values` shows None whether the signal abstained
    # or exploded, so without per_signal_errors a permanently broken judge is
    # indistinguishable from one that legitimately couldn't measure.
    broken = _RaisingSignal("judge", RuntimeError("judge backend unreachable"))
    quiet = _ConstantSignal("entropy", SignalReading(value=None, trip=False))
    r = _build_router(broken, quiet)
    r.route(object())
    r.route(object())

    assert r.stats.per_signal_errors == {"judge": 2}
    # The signal that abstained *by returning None* must NOT be counted as an
    # error — that is exactly the distinction the counter exists to draw.
    assert "entropy" not in r.stats.per_signal_errors
    # And neither one measured anything, so per_signal_measured stays empty.
    assert r.stats.per_signal_measured == {}


def test_a_raising_signal_does_not_suppress_a_later_signal_that_trips() -> None:
    # Isolation is per-signal, not per-route: the loop must keep going so a
    # working signal still escalates. Pre-fix the first signal's exception
    # aborted the whole loop, so a broken judge silently disabled every
    # signal declared after it.
    broken = _RaisingSignal("judge", RuntimeError("boom"))
    tripping = _ConstantSignal("entropy", SignalReading(value=2.0, trip=True))
    r = _build_router(broken, tripping)
    decision = r.route(object())

    assert decision.model_id == "claude-sonnet-4-6"
    assert decision.triggered_signal == "entropy"
    assert decision.signal_values == {"judge": None, "entropy": 2.0}
    assert r.stats.per_signal_errors == {"judge": 1}
    assert r.stats.per_signal_trips == {"entropy": 1}
    assert r.stats.escalations == 1
    assert r.stats.escalation_rate == 1.0


def test_keyboard_interrupt_and_system_exit_still_propagate() -> None:
    # `except Exception`, not `except BaseException`. A Ctrl-C inside a signal
    # must still bring the process down rather than be laundered into
    # "couldn't measure" — otherwise the operator cannot stop a run whose
    # judge is hanging.
    for exc in (KeyboardInterrupt(), SystemExit(1)):
        r = _build_router(_RaisingSignal("judge", exc))
        with pytest.raises(type(exc)):
            r.route(object())
        # And nothing was recorded, because the process is going down.
        assert r.stats.total_routes == 0
        assert r.stats.per_signal_errors == {}


def test_per_signal_errors_round_trips_through_dump_stats_json(tmp_path: Path) -> None:
    # The counter is useless if it stops at the in-process dict: the whole
    # point is that an operator tailing router-stats.json can see a judge
    # that is failing.
    r = _build_router(_RaisingSignal("judge", RuntimeError("boom")))
    r.route(object())
    out = tmp_path / "router-stats.json"
    r.dump_stats_json(out)
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert payload["per_signal_errors"] == {"judge": 1}
    assert payload["total_routes"] == 1
    # Nested dict is copied, not aliased — same contract as the other two.
    payload["per_signal_errors"]["judge"] = 999
    assert r.stats.per_signal_errors == {"judge": 1}
