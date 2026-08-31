"""Tests for `scripts/tune_threshold.py`.

The plot rendering is matplotlib-optional; the JSON sweep is the
deterministic surface we assert against.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# One spelling, repo-root-relative (#201). This file used to put `scripts/` on
# `sys.path` and import `tune_threshold` bare while `tests/test_atomic_write.py`
# imported `scripts.tune_threshold` — two names for one file, which Python makes
# two separate module objects. `test_main_plot_write_oserror_exits_two` below
# `monkeypatch.setattr`s the module it imported, and that patch is invisible to
# the other copy; nothing was failing only because each test happened to patch
# and call the same one.
from scripts.tune_threshold import (
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


def test_main_bad_thresholds_exits_two(tmp_path: Path, capsys) -> None:
    """A non-numeric `--thresholds` token is operator misconfig: instead of a raw
    `ValueError` traceback at exit 1, it must surface as a clean stderr line + exit
    2 (same contract as the `--no-dry` guard; bad-input sibling of the write-seam
    guard)."""
    out_stem = tmp_path / "should-not-be-written"
    rc = main(["--dry", "--out", str(out_stem), "--thresholds", "0.5,notanumber"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "--thresholds must be comma-separated numbers" in captured.err
    assert not out_stem.with_suffix(".json").exists()


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--cheap-dollars", "nan"),
        ("--cheap-dollars", "inf"),
        ("--cheap-dollars", "-inf"),
        ("--cheap-dollars", "-0.5"),
        ("--strong-dollars", "nan"),
        ("--strong-dollars", "inf"),
        ("--strong-dollars", "-0.01"),
    ],
)
def test_main_non_finite_or_negative_dollars_exits_two(
    tmp_path: Path, capsys, flag: str, value: str
) -> None:
    """`--cheap-dollars`/`--strong-dollars` only get argparse `float` coercion,
    which parses nan/inf/negative. Such a value would write a bare `NaN`/`Infinity`
    (invalid JSON) or a fabricated negative cost into the committed
    `docs/threshold_demo.json`. It must surface as a clean exit-2 operator error
    with no artifact written — same contract as `--thresholds`/`--out`."""
    out_stem = tmp_path / "should-not-be-written"
    # `--flag=value` form so argparse assigns the value (a bare `-inf` token is
    # otherwise mistaken for an option); this is how an operator would pass it.
    rc = main(["--dry", "--out", str(out_stem), f"{flag}={value}"])
    captured = capsys.readouterr()
    assert rc == 2
    assert f"{flag} must be a finite number >= 0" in captured.err
    assert not out_stem.with_suffix(".json").exists()


def test_main_finite_non_negative_dollars_still_written(tmp_path: Path) -> None:
    """The guard must not regress the happy path: finite non-negative dollars
    write a valid, strictly-parseable JSON artifact (no bare NaN/Infinity)."""
    out_stem = tmp_path / "out"
    rc = main(
        ["--dry", "--out", str(out_stem), "--cheap-dollars", "0.001", "--strong-dollars", "0.02"]
    )
    assert rc == 0
    text = (tmp_path / "out.json").read_text()
    payload = json.loads(
        text, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"non-finite {c}"))
    )
    assert payload["cheap_dollars_per_request"] == 0.001
    assert payload["strong_dollars_per_request"] == 0.02


def test_main_unwritable_out_exits_two(tmp_path: Path, capsys) -> None:
    """An unwritable `--out` (a path component that is a file) makes
    `atomic_write_text` raise OSError; instead of a raw traceback at exit 1 it must
    surface as a clean stderr line + exit 2 (portfolio write-seam contract, sibling
    of leh#158/#159, pyasync#84)."""
    blocker = tmp_path / "afile"
    blocker.write_text("not a dir", encoding="utf-8")
    rc = main(["--dry", "--out", str(blocker / "sweep"), "--thresholds", "0.5,1.5"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "could not write sweep artifacts" in captured.err


def test_main_plot_write_oserror_exits_two(tmp_path: Path, capsys, monkeypatch) -> None:
    """The PNG plot is the sibling write seam of the JSON write: with matplotlib
    installed but the `.png` path unwritable, `_try_save_plot`'s `mkdir`/`savefig`
    raise OSError. It must surface as a clean stderr line + exit 2 (not escape
    `main` as a raw traceback at exit 1). Sibling of the #156/#157 JSON-seam guard,
    which left `_try_save_plot` bare. Monkeypatching the plot fn keeps the lock
    matplotlib-free (it is absent from the `dev` extra / CI)."""
    from scripts import tune_threshold

    def _raise_oserror(rows, out_png):
        raise OSError("read-only file system")

    monkeypatch.setattr(tune_threshold, "_try_save_plot", _raise_oserror)
    out_stem = tmp_path / "sweep"
    rc = main(["--dry", "--out", str(out_stem), "--thresholds", "0.5,1.5"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "could not write sweep plot" in captured.err
    # The JSON artifact wrote successfully *before* the plot seam failed — this is
    # the divergent-failure path the wholesale-unwritable `--out` above can't reach
    # (there the JSON write fails first).
    assert (tmp_path / "sweep.json").exists()


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


# ----- stemless --out (#174) -------------------------------------------------
#
# Same gap as bench_savings, one script over: `Path.with_suffix` raised
# ValueError outside the write-seam try. Worse here, because it fired *after*
# `sweep` had already run — the operator waited for the whole sweep to be told
# the flag was wrong.


# Exactly the stems whose `.name` is empty — the set `with_suffix` rejects.
# `..` is deliberately NOT here: its name is `".."`, so `with_suffix` yields
# `...json` and writing that file is a coherent, if odd, operator request.
@pytest.mark.parametrize("bad_out", ["", ".", "/"])
def test_main_stemless_out_exits_two(bad_out: str, capsys) -> None:
    rc = main(["--dry", "--out", bad_out])
    captured = capsys.readouterr()
    assert rc == 2
    assert "--out must be a path stem with a filename component" in captured.err
    assert "Traceback" not in captured.err


def test_main_stemless_out_fails_before_the_sweep(capsys) -> None:
    rc = main(["--dry", "--out", "", "--thresholds", "0.1,0.2,0.3,0.4,0.5"])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""


def test_main_ordinary_stem_is_unaffected(tmp_path: Path, capsys) -> None:
    stem = tmp_path / "nested" / "threshold"
    rc = main(["--dry", "--out", str(stem)])
    _ = capsys.readouterr()
    assert rc == 0
    assert stem.with_suffix(".json").exists()
