"""Tests for `scripts/tune_threshold.py`.

The plot rendering is matplotlib-optional; the JSON sweep is the
deterministic surface we assert against.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make `scripts/` importable so we can test `sweep()` directly without
# spawning a subprocess.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from tune_threshold import (  # noqa: E402 - sys.path tweak above
    ThresholdSweepRow,
    _build_sample_items,
    main,
    sweep,
)


def test_sweep_returns_one_row_per_threshold() -> None:
    items = _build_sample_items()
    rows = sweep(items, [0.5, 1.0, 1.5], cheap_dollars=0.001, strong_dollars=0.01)
    assert len(rows) == 3
    assert all(isinstance(r, ThresholdSweepRow) for r in rows)
    assert [r.threshold for r in rows] == [0.5, 1.0, 1.5]


def test_sweep_escalation_rate_monotone_non_increasing_in_threshold() -> None:
    # Raising the entropy threshold should never *increase* the
    # escalation rate (it's a more restrictive condition).
    items = _build_sample_items()
    rows = sweep(items, [0.0, 0.5, 1.0, 1.5, 2.0, 5.0], cheap_dollars=0.001, strong_dollars=0.01)
    rates = [r.escalation_rate for r in rows]
    for a, b in zip(rates, rates[1:], strict=False):
        assert a >= b


def test_sweep_dollars_match_per_request_arithmetic() -> None:
    items = _build_sample_items()
    rows = sweep(items, [0.0], cheap_dollars=0.001, strong_dollars=0.01)
    # threshold=0.0 means every row escalates (entropy >= 0 always).
    # Cost per request = cheap + strong dollars, regardless of n_items.
    assert rows[0].escalation_rate == pytest.approx(1.0)
    assert rows[0].dollars_per_request == pytest.approx(0.011, rel=1e-6)


def test_sweep_at_very_high_threshold_never_escalates() -> None:
    items = _build_sample_items()
    rows = sweep(items, [100.0], cheap_dollars=0.001, strong_dollars=0.01)
    assert rows[0].escalation_rate == 0.0
    assert rows[0].dollars_per_request == pytest.approx(0.001, rel=1e-6)
    # When nothing escalates, escalated-mean is 0 (no rows), overall ==
    # cheap mean.
    assert rows[0].mean_quality_escalated == 0.0
    assert rows[0].mean_quality_overall == rows[0].mean_quality_cheap


def test_main_dry_writes_json(tmp_path: Path) -> None:
    out_stem = tmp_path / "out"
    rc = main(["--out", str(out_stem), "--thresholds", "0.5,1.5"])
    assert rc == 0
    payload = json.loads((tmp_path / "out.json").read_text())
    assert payload["mode"] == "dry"
    assert len(payload["rows"]) == 2
    assert {r["threshold"] for r in payload["rows"]} == {0.5, 1.5}


def test_main_non_dry_mode_exits_with_documented_error(tmp_path: Path, capsys) -> None:
    """`--no-dry` reaches the D-007 real-API-not-implemented guard and exits 2.

    The flag previously couldn't be set to False (action="store_true" with
    default=True made the guard unreachable). It now uses BooleanOptionalAction
    so `--no-dry` actually opts into the real-API branch.
    """
    out_stem = tmp_path / "should-not-be-written"
    rc = main(["--no-dry", "--out", str(out_stem), "--thresholds", "0.5"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "real-API tune mode is not implemented" in captured.err
    assert not out_stem.with_suffix(".json").exists()


# ----------------------------------------------------------------------
# #54: ThresholdSweepRow.to_dict — explicit field-by-field contract.
# Mirrors StrategyResult.to_dict in scripts/bench_savings.py.
# ----------------------------------------------------------------------


def test_threshold_sweep_row_to_dict_field_set_is_pinned() -> None:
    r = ThresholdSweepRow(
        threshold=0.95,
        escalation_rate=0.10,
        mean_quality_cheap=0.80,
        mean_quality_escalated=0.95,
        mean_quality_overall=0.85,
        dollars_per_request=0.0001,
        n=100,
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


def test_threshold_sweep_row_to_dict_values_round_trip() -> None:
    r = ThresholdSweepRow(
        threshold=0.5,
        escalation_rate=0.25,
        mean_quality_cheap=0.7,
        mean_quality_escalated=0.9,
        mean_quality_overall=0.75,
        dollars_per_request=0.0002,
        n=50,
    )
    assert r.to_dict() == {
        "threshold": 0.5,
        "escalation_rate": 0.25,
        "mean_quality_cheap": 0.7,
        "mean_quality_escalated": 0.9,
        "mean_quality_overall": 0.75,
        "dollars_per_request": 0.0002,
        "n": 50,
    }


def test_main_dry_payload_rows_use_to_dict_shape(tmp_path: Path) -> None:
    # Acceptance regression: every row under payload["rows"] has the
    # exact field set the to_dict contract pins. Catches a future drift
    # where the list-comp re-introduces asdict.
    out = tmp_path / "sweep.json"
    rc = main(["--dry", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(payload["rows"], list)
    assert len(payload["rows"]) > 0
    for row in payload["rows"]:
        assert sorted(row.keys()) == [
            "dollars_per_request",
            "escalation_rate",
            "mean_quality_cheap",
            "mean_quality_escalated",
            "mean_quality_overall",
            "n",
            "threshold",
        ]
