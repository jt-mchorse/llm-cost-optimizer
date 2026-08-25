"""The router panel must show a broken signal (#190).

`RouterStats.per_signal_errors` shipped in #184 with a stated purpose:

    It exists so that conversion doesn't merely trade a loud failure for a
    silent one: a signal that abstains by raising looks identical in
    `RouterDecision.signal_values` to one that abstains by returning
    `value=None`, and only this counter separates "the judge couldn't measure
    this response" from "the judge is broken and every route is running
    unmeasured".

The counter is populated, serialized by `to_dict`, and present in
`docs/savings.json` -- and had **no consumer**. `dashboard/app.py`'s
`_router_panel_rows` built its row set from
`set(per_signal_trips) | set(per_signal_measured)`, and a signal that only ever
raised is in neither, so it was not a row at all. That panel's caption calls it
"the only way to debug a router that's escalating either too much or not
enough".

Measured on `main` over 100 routes with `entropy` (raises on 40% of calls, trips
on 20%) and `judge` (raises on every call)::

    producer   per_signal_trips     {'entropy': 20}
               per_signal_measured  {'entropy': 60}
               per_signal_errors    {'judge': 100, 'entropy': 40}

    panel               trips  measured  trip_rate
              entropy      20        60   0.333333

One row. A judge that failed on every single route was invisible, and
`entropy`'s `20/60 = 0.333` read as "fires on a third of what it sees" when the
truth is 20 trips out of 100 attempts, 40 of which errored.

Every test here that asserts panel content drives the panel from a **real
`UncertaintyRouter`**, not a hand-authored stats dict. The fixture in the test
this replaced (`{"per_signal_trips": {"unreached": 0}, "per_signal_measured":
{"unreached": 0}}`) was a shape the producer cannot emit -- both dicts are
written with `.get(name, 0) + 1`, so a key never exists with the value 0.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from cost_optimizer.router import SignalReading, UncertaintyRouter

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _panel_rows(stats: dict[str, Any]) -> list[dict[str, Any]]:
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    from dashboard.app import _router_panel_rows

    return _router_panel_rows(stats)


class _Adapter:
    """Minimal `CheapAdapter` — the router only needs *something* to inspect."""

    def call_cheap(self, request: Any) -> dict[str, str]:
        return {"text": "ok"}


class _AlwaysRaises:
    """A permanently broken signal — the exact scenario #184's docstring names."""

    def __init__(self, name: str = "judge") -> None:
        self.name = name

    def measure(self, response: Any) -> SignalReading:
        raise RuntimeError("judge API returned 500")


class _FlakyEntropy:
    """Raises on 40% of calls, trips on 20%, abstains-by-value on the rest."""

    def __init__(self) -> None:
        self.name = "entropy"
        self.i = 0

    def measure(self, response: Any) -> SignalReading:
        self.i += 1
        if self.i % 5 == 0:
            return SignalReading(value=1.9, trip=True)
        if self.i % 2 == 0:
            raise ValueError("logprobs missing from response")
        return SignalReading(value=0.4, trip=False)


def _route_n(signals: list[Any], n: int = 100) -> dict[str, Any]:
    router = UncertaintyRouter(
        cheap_model="claude-haiku-4-5-20251001",
        strong_model="claude-opus-5",
        cheap_adapter=_Adapter(),
        signals=signals,
    )
    for i in range(n):
        router.route({"prompt": f"q{i}"})
    return router.stats.to_dict()


# ----------------------------------------------------------------------
# The headline defect
# ----------------------------------------------------------------------


def test_a_signal_that_only_ever_raised_is_a_row() -> None:
    """On `main` the panel had one row and `judge` was not in it."""
    stats = _route_n([_FlakyEntropy(), _AlwaysRaises()])
    # Precondition: the producer really does put `judge` in errors only.
    assert stats["per_signal_errors"]["judge"] == 100
    assert "judge" not in stats["per_signal_trips"]
    assert "judge" not in stats["per_signal_measured"]

    rows = _panel_rows(stats)
    assert [r["signal"] for r in rows] == ["entropy", "judge"]


def test_a_permanently_broken_signal_reads_as_broken_not_as_never_firing() -> None:
    """`trip_rate` blank, `error_rate` 1.0, `errors` equal to every attempt.

    `trip_rate = 0.0` would be the single most misleading cell the panel could
    produce here: the bottom of the range, identical to a healthy signal that
    never happened to trip.
    """
    rows = _panel_rows(_route_n([_FlakyEntropy(), _AlwaysRaises()]))
    judge = next(r for r in rows if r["signal"] == "judge")
    assert judge["trips"] == 0
    assert judge["measured"] == 0
    assert judge["errors"] == 100
    assert judge["attempts"] == 100
    assert judge["trip_rate"] is None
    assert judge["error_rate"] == 1.0


def test_a_partially_broken_signals_denominator_is_visible() -> None:
    """`20/60 = 0.333` is unchanged and correct; `attempts`/`errors` are what
    make it readable as "broken 40% of the time" rather than "healthy"."""
    rows = _panel_rows(_route_n([_FlakyEntropy(), _AlwaysRaises()]))
    entropy = next(r for r in rows if r["signal"] == "entropy")
    assert entropy["trips"] == 20
    assert entropy["measured"] == 60
    assert entropy["errors"] == 40
    assert entropy["attempts"] == 100
    assert entropy["trip_rate"] == pytest.approx(20 / 60)
    assert entropy["error_rate"] == pytest.approx(0.4)


# ----------------------------------------------------------------------
# The `None`-vs-`0.0` rule, and its limits
# ----------------------------------------------------------------------


def test_a_measured_zero_trip_rate_is_still_zero_not_none() -> None:
    """The rule is about an *absent* denominator, not an absent numerator.

    A signal measured 100 times that never tripped genuinely has a trip rate of
    0.0, and must not be blanked out — that would trade one wrong reading for
    another.
    """

    class _NeverTrips:
        name = "quiet"

        def measure(self, response: Any) -> SignalReading:
            return SignalReading(value=0.1, trip=False)

    rows = _panel_rows(_route_n([_NeverTrips()]))
    quiet = next(r for r in rows if r["signal"] == "quiet")
    assert quiet["measured"] == 100
    assert quiet["errors"] == 0
    assert quiet["trips"] == 0
    assert quiet["trip_rate"] == 0.0
    assert quiet["error_rate"] == 0.0


def test_a_healthy_router_panel_is_unchanged_in_the_columns_it_had() -> None:
    """No errors anywhere: the three original columns keep their old values, so
    this is an addition to the panel and not a re-interpretation of it."""

    class _SometimesTrips:
        def __init__(self) -> None:
            self.name = "entropy"
            self.i = 0

        def measure(self, response: Any) -> SignalReading:
            self.i += 1
            return SignalReading(value=1.9 if self.i % 4 == 0 else 0.3, trip=self.i % 4 == 0)

    rows = _panel_rows(_route_n([_SometimesTrips()]))
    assert rows == [
        {
            "signal": "entropy",
            "trips": 25,
            "measured": 100,
            "errors": 0,
            "attempts": 100,
            "trip_rate": 0.25,
            "error_rate": 0.0,
        }
    ]


# ----------------------------------------------------------------------
# Shape / robustness
# ----------------------------------------------------------------------


def test_missing_counter_dicts_do_not_raise() -> None:
    """A hand-rolled or pre-#184 artifact has no `per_signal_errors` key."""
    rows = _panel_rows({"per_signal_trips": {"entropy": 5}, "per_signal_measured": {"entropy": 50}})
    assert rows == [
        {
            "signal": "entropy",
            "trips": 5,
            "measured": 50,
            "errors": 0,
            "attempts": 50,
            "trip_rate": 0.1,
            "error_rate": 0.0,
        }
    ]


def test_empty_stats_yields_no_rows() -> None:
    assert _panel_rows({}) == []


def test_committed_savings_artifact_still_renders() -> None:
    """The shipped `docs/savings.json` must go through the new panel unchanged
    in its existing columns — `per_signal_errors` is `{}` there."""
    import json

    payload = json.loads((_REPO_ROOT / "docs" / "savings.json").read_text(encoding="utf-8"))
    router_row = next(s for s in payload["strategies"] if s.get("router_stats") is not None)
    rows = _panel_rows(router_row["router_stats"])
    entropy = next(r for r in rows if r["signal"] == "entropy")
    assert entropy["trips"] == 50
    assert entropy["measured"] == 500
    assert entropy["errors"] == 0
    assert entropy["trip_rate"] == 0.1
    assert entropy["error_rate"] == 0.0


def test_every_row_has_the_same_key_set() -> None:
    """A dataframe built from ragged dicts silently fills the gaps with NaN, so
    a per-row key difference would look like a data difference in the UI."""
    rows = _panel_rows(_route_n([_FlakyEntropy(), _AlwaysRaises()]))
    assert len({frozenset(r) for r in rows}) == 1
