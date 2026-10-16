"""A signal that returns something other than a reading abstains, counted (#321).

#184 made a signal whose `measure()` *raises* abstain (`value=None,
trip=False`) and count in `per_signal_errors`, because `call_cheap` has already
run and been paid for. A signal that *returned* a non-reading was not covered:
`.value`/`.trip` were read after the `try`, so on main

    measure() -> 0.9   =>  AttributeError out of route(), paid response lost,
                           stats {'total_routes': 0, 'per_signal_measured': {'good': 1}}
"""

from __future__ import annotations

from collections import namedtuple
from typing import Any

import pytest

from cost_optimizer.router import SignalReading, UncertaintyRouter


class _Adapter:
    def call_cheap(self, request: Any) -> dict[str, str]:
        return {"text": "paid response"}


class _Fixed:
    def __init__(self, name: str, result: object) -> None:
        self.name = name
        self._result = result

    def measure(self, response: Any) -> Any:
        return self._result


class _ForgotReturn:
    name = "forgot-return"

    def measure(self, response: Any) -> None:
        SignalReading(value=0.9, trip=True)  # built, never returned


NON_READINGS = {
    "float": 0.9,
    "none": None,
    "str": "0.9",
    "dict": {"value": 0.9, "trip": True},
    "tuple": (0.9, True),
}


def _router(*signals: Any) -> UncertaintyRouter:
    return UncertaintyRouter("cheap", "strong", _Adapter(), signals=list(signals))


def _invariants_hold(r: UncertaintyRouter) -> None:
    s = r.stats
    assert s.cheap_only + s.escalations == s.total_routes
    for name, n in s.per_signal_measured.items():
        assert n <= s.total_routes, name
    for name, n in s.per_signal_trips.items():
        assert n <= s.per_signal_measured.get(name, 0), name


@pytest.mark.parametrize("bad", list(NON_READINGS.values()), ids=list(NON_READINGS))
def test_a_non_reading_return_abstains_and_keeps_the_paid_response(bad: object) -> None:
    good = _Fixed("good", SignalReading(value=0.5, trip=False))
    r = _router(good, _Fixed("bad", bad))
    decision = r.route({})
    assert decision.cheap_response == {"text": "paid response"}
    assert decision.model_id == "cheap"
    assert decision.signal_values == {"good": 0.5, "bad": None}
    assert r.stats.per_signal_errors == {"bad": 1}
    assert r.stats.total_routes == 1
    _invariants_hold(r)


def test_a_measure_that_forgot_to_return_abstains() -> None:
    r = _router(_ForgotReturn())
    decision = r.route({})
    assert decision.signal_values == {"forgot-return": None}
    assert decision.triggered_signal is None
    assert r.stats.per_signal_errors == {"forgot-return": 1}
    _invariants_hold(r)


def test_a_non_reading_does_not_hide_a_later_signals_trip() -> None:
    trips = _Fixed("trips", SignalReading(value=0.99, trip=True))
    r = _router(_Fixed("bad", 0.9), trips)
    decision = r.route({})
    assert decision.model_id == "strong"
    assert decision.triggered_signal == "trips"
    _invariants_hold(r)


Reading = namedtuple("Reading", ["value", "trip"])


def test_a_duck_typed_reading_still_measures_and_trips() -> None:
    r = _router(_Fixed("duck", Reading(0.99, True)))
    decision = r.route({})
    assert decision.signal_values == {"duck": 0.99}
    assert decision.triggered_signal == "duck"
    assert r.stats.per_signal_errors == {}
    _invariants_hold(r)


def test_a_duck_typed_unmeasured_trip_is_refused_not_escalated() -> None:
    # `SignalReading(value=None, trip=True)` raises by design; a duck type used
    # to slip past that invariant and escalate on a reading that measured
    # nothing.
    r = _router(_Fixed("duck", Reading(None, True)))
    decision = r.route({})
    assert decision.model_id == "cheap"
    assert r.stats.per_signal_errors == {"duck": 1}
    _invariants_hold(r)


def test_a_real_reading_is_unchanged() -> None:
    r = _router(_Fixed("real", SignalReading(value=0.2, trip=False)))
    decision = r.route({})
    assert decision.signal_values == {"real": 0.2}
    assert r.stats.per_signal_measured == {"real": 1}
    assert r.stats.per_signal_errors == {}
