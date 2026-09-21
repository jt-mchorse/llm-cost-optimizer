"""An empty class has no mean — `null`, not the floor of the quality range (#221, D-018).

`sweep()` reported `mean_quality_cheap` / `mean_quality_escalated` as `0.0`
when the corresponding class had no rows. Quality here is a judge score on
`[0, 1]`, so `0.0` is not a neutral sentinel: it is the worst value the metric
can take, and it landed in `docs/threshold_demo.json` — the artifact the
README's documented command writes — on 3 of its 8 rows.

Both ends of a threshold sweep empty a class *by construction*: at `0.0` every
row escalates so the cheap class is empty, and at a high threshold nothing
escalates so the escalated class is. The endpoints of every sweep this script
produces were therefore the fabricated ones.

The separating arm in this module is `test_a_measured_zero_is_still_published_as_zero`.
`sum([0.0]) / 1` is `0.0`, which is falsy — so the plausible neighbouring fix
`(sum(q) / len(q)) or None` passes every empty-class assertion here while
silently laundering a *real* worst-case score into `null`. That neighbour is
what this module exists to reject.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.tune_threshold import (
    ThresholdSweepRow,
    _build_sample_items,
    main,
    sweep,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED = _REPO_ROOT / "docs" / "threshold_demo.json"

# The default sweep `main()` runs with no flags — the README's documented
# command (README:191) and the stem that writes the committed artifact.
DEFAULT_THRESHOLDS = [0.0, 0.5, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
DEFAULT_CHEAP_DOLLARS = 0.0008
DEFAULT_STRONG_DOLLARS = 0.015


def _default_sweep() -> list[ThresholdSweepRow]:
    return sweep(
        _build_sample_items(),
        DEFAULT_THRESHOLDS,
        cheap_dollars=DEFAULT_CHEAP_DOLLARS,
        strong_dollars=DEFAULT_STRONG_DOLLARS,
    )


# ----------------------------------------------------------------------
# The empty class abstains
# ----------------------------------------------------------------------


def test_everything_escalates_so_the_cheap_class_has_no_mean() -> None:
    # threshold 0.0: entropy >= 0 always, so every row escalates and no row
    # stays cheap. Pre-fix this row published `mean_quality_cheap: 0.0`.
    row = sweep(_build_sample_items(), [0.0], cheap_dollars=0.001, strong_dollars=0.01)[0]
    assert row.escalation_rate == 1.0
    assert row.mean_quality_cheap is None
    # The escalated class *is* populated here, so it keeps its measurement.
    assert row.mean_quality_escalated == pytest.approx(0.924)


def test_nothing_escalates_so_the_escalated_class_has_no_mean() -> None:
    row = sweep(_build_sample_items(), [100.0], cheap_dollars=0.001, strong_dollars=0.01)[0]
    assert row.escalation_rate == 0.0
    assert row.mean_quality_escalated is None
    assert row.mean_quality_cheap == pytest.approx(0.7)


def test_mean_is_none_exactly_when_the_class_is_empty() -> None:
    """The biconditional, over the whole default sweep.

    Asserting `is None` on two hand-picked rows would also pass a fix that
    nulls the field more often than it should. `escalation_rate` gives the
    class sizes exactly (`n_escalated == round(rate * n)`), so the emptiness
    of each class is derivable and can be checked against the abstention.
    """
    rows = _default_sweep()
    seen_empty_cheap = 0
    seen_empty_escalated = 0
    for row in rows:
        n_escalated = round(row.escalation_rate * row.n)
        n_cheap = row.n - n_escalated
        assert (row.mean_quality_cheap is None) == (n_cheap == 0), (
            f"t={row.threshold}: cheap class has {n_cheap} rows but "
            f"mean_quality_cheap is {row.mean_quality_cheap!r}"
        )
        assert (row.mean_quality_escalated is None) == (n_escalated == 0), (
            f"t={row.threshold}: escalated class has {n_escalated} rows but "
            f"mean_quality_escalated is {row.mean_quality_escalated!r}"
        )
        seen_empty_cheap += n_cheap == 0
        seen_empty_escalated += n_escalated == 0
    # Anti-vacuous: the biconditional is only meaningful if both sides of it
    # actually occur in this sweep. They do — that is the whole defect.
    assert seen_empty_cheap == 1, "the default sweep must contain an empty cheap class"
    assert seen_empty_escalated == 2, "the default sweep must contain empty escalated classes"


def test_mean_quality_overall_is_never_none() -> None:
    """It is computed over the whole population, so it is always a measurement.

    `overall_total / n` divides by the row count, not the class count, so it
    does not have the empty-denominator problem and must not acquire one.
    """
    for row in _default_sweep():
        assert row.mean_quality_overall is not None
        assert isinstance(row.mean_quality_overall, float)


# ----------------------------------------------------------------------
# The separating arm: a measured 0.0 is a measurement
# ----------------------------------------------------------------------


def _with_quality(key: str, value: float) -> list[dict[str, Any]]:
    return [dict(item, **{key: value}) for item in _build_sample_items()]


def test_a_measured_zero_is_still_published_as_zero() -> None:
    """A judge score of 0.0 is legal, and `None` must not absorb it.

    `sum([0.0, 0.0]) / 2` is `0.0` — falsy. So `(sum(q) / len(q)) or None`,
    the obvious one-token neighbour of this fix, turns a real worst-case
    measurement into `null` while passing every assertion above. Abstention
    must key off the class being *empty*, never off the value being zero.
    """
    # Nothing escalates at threshold 100.0, so the cheap class holds all five
    # rows — and every one of them genuinely scored 0.0.
    row = sweep(
        _with_quality("cheap_quality", 0.0),
        [100.0],
        cheap_dollars=0.001,
        strong_dollars=0.01,
    )[0]
    assert row.escalation_rate == 0.0
    assert row.mean_quality_cheap == 0.0
    assert row.mean_quality_cheap is not None
    assert row.mean_quality_overall == 0.0
    # Through the serializer as well, not only the dataclass. A neighbouring
    # fix that leaves `sweep` alone and writes `self.mean_quality_cheap or
    # None` in `to_dict` launders the measurement at egress — which is the
    # surface an external consumer actually reads.
    assert row.to_dict()["mean_quality_cheap"] == 0.0
    assert json.loads(json.dumps(row.to_dict()))["mean_quality_cheap"] == 0.0


def test_a_measured_zero_on_the_escalated_side_is_still_published_as_zero() -> None:
    # The mirror. Everything escalates at threshold 0.0 and every escalated
    # row scored 0.0; the escalated class is full, so it reports 0.0.
    row = sweep(
        _with_quality("strong_quality", 0.0),
        [0.0],
        cheap_dollars=0.001,
        strong_dollars=0.01,
    )[0]
    assert row.escalation_rate == 1.0
    assert row.mean_quality_escalated == 0.0
    assert row.mean_quality_escalated is not None
    # And the cheap class really is empty on this same row, so the two
    # outcomes are distinguishable side by side in one payload — a measured
    # 0.0 and an abstention, in the same serialized row.
    assert row.mean_quality_cheap is None
    serialized = json.loads(json.dumps(row.to_dict()))
    assert serialized["mean_quality_escalated"] == 0.0
    assert serialized["mean_quality_cheap"] is None


# ----------------------------------------------------------------------
# The fix must not move a number that was correctly measured
# ----------------------------------------------------------------------

# Captured from the committed artifact *before* this change. Every field here
# was already a real measurement, so all of them must survive byte-identical;
# only the three fabricated quality cells may move.
PRE_FIX_UNCHANGED_COLUMNS = [
    (0.0, 1.0, 0.015799999999999998, 0.924),
    (0.5, 0.6, 0.009799999999999998, 0.9199999999999999),
    (1.0, 0.4, 0.006799999999999999, 0.876),
    (1.2, 0.4, 0.006799999999999999, 0.876),
    (1.4, 0.4, 0.006799999999999999, 0.876),
    (1.6, 0.4, 0.006799999999999999, 0.876),
    (1.8, 0.0, 0.0008, 0.7),
    (2.0, 0.0, 0.0008, 0.7),
]


def test_the_correctly_measured_columns_did_not_move() -> None:
    rows = _default_sweep()
    actual = [
        (r.threshold, r.escalation_rate, r.dollars_per_request, r.mean_quality_overall)
        for r in rows
    ]
    assert actual == PRE_FIX_UNCHANGED_COLUMNS


def test_the_quality_columns_that_were_measured_did_not_move() -> None:
    """The five rows whose classes were both populated are untouched."""
    by_threshold = {r.threshold: r for r in _default_sweep()}
    # Cheap-class means, for every row whose cheap class is non-empty.
    assert by_threshold[0.5].mean_quality_cheap == 0.975
    for t in (1.0, 1.2, 1.4, 1.6):
        assert by_threshold[t].mean_quality_cheap == 0.8833333333333333
    for t in (1.8, 2.0):
        assert by_threshold[t].mean_quality_cheap == 0.7
    # Escalated-class means, for every row whose escalated class is non-empty.
    assert by_threshold[0.0].mean_quality_escalated == 0.924
    assert by_threshold[0.5].mean_quality_escalated == 0.8833333333333333
    for t in (1.0, 1.2, 1.4, 1.6):
        assert by_threshold[t].mean_quality_escalated == 0.865


# ----------------------------------------------------------------------
# JSON egress, and the committed artifact
# ----------------------------------------------------------------------


def test_the_abstention_reaches_the_json_as_a_literal_null(tmp_path: Path) -> None:
    """End-to-end through `main()`, not just the dataclass.

    A `None` that `json.dumps` never sees is not a fix; what an external
    consumer reads is the file.
    """
    out_stem = tmp_path / "sweep"
    rc = main(["--out", str(out_stem), "--thresholds=0.0,100.0"])
    assert rc == 0
    raw = out_stem.with_suffix(".json").read_text(encoding="utf-8")
    assert '"mean_quality_cheap": null' in raw
    assert '"mean_quality_escalated": null' in raw
    payload = json.loads(raw)
    first, last = payload["rows"]
    assert first["mean_quality_cheap"] is None
    assert first["mean_quality_escalated"] is not None
    assert last["mean_quality_escalated"] is None
    assert last["mean_quality_cheap"] is not None


def test_the_committed_artifact_carries_no_fabricated_zero() -> None:
    """`docs/threshold_demo.json` is what the README's command produces.

    Pinned by *location*, not just by count: the three abstentions are the two
    endpoints of the sweep, which is where a class empties by construction.
    """
    payload = json.loads(COMMITTED.read_text(encoding="utf-8"))
    rows = payload["rows"]
    assert len(rows) == 8
    abstentions = {
        (r["threshold"], field)
        for r in rows
        for field in ("mean_quality_cheap", "mean_quality_escalated")
        if r[field] is None
    }
    assert abstentions == {
        (0.0, "mean_quality_cheap"),
        (1.8, "mean_quality_escalated"),
        (2.0, "mean_quality_escalated"),
    }
    # And no surviving cell reports the floor of the range.
    for r in rows:
        for field in ("mean_quality_cheap", "mean_quality_escalated"):
            assert r[field] is None or r[field] > 0.0


def test_the_committed_artifact_still_matches_the_documented_command() -> None:
    """Regenerating it with the default flags must be a no-op.

    This is the check that keeps the artifact honest after the schema change:
    `python scripts/tune_threshold.py --out docs/threshold_demo`.
    """
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    rows = _default_sweep()
    regenerated = {
        "cheap_dollars_per_request": DEFAULT_CHEAP_DOLLARS,
        "mode": "dry",
        "rows": [r.to_dict() for r in rows],
        "strong_dollars_per_request": DEFAULT_STRONG_DOLLARS,
    }
    assert regenerated == committed


# ----------------------------------------------------------------------
# The seven-field contract (#54) survives the type change
# ----------------------------------------------------------------------


def test_to_dict_keeps_seven_fields_with_an_abstaining_row() -> None:
    r = ThresholdSweepRow(
        threshold=0.0,
        escalation_rate=1.0,
        mean_quality_cheap=None,
        mean_quality_escalated=0.9,
        mean_quality_overall=0.9,
        dollars_per_request=0.0158,
        n=5,
    )
    d = r.to_dict()
    assert sorted(d.keys()) == [
        "dollars_per_request",
        "escalation_rate",
        "mean_quality_cheap",
        "mean_quality_escalated",
        "mean_quality_overall",
        "n",
        "threshold",
    ]
    assert d["mean_quality_cheap"] is None
    # `None` must survive serialization rather than being dropped by a
    # skip-null encoder somewhere downstream.
    assert json.loads(json.dumps(d))["mean_quality_cheap"] is None
