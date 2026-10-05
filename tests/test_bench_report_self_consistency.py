"""The savings report agrees with itself under any `--n` / `--out` (#255).

Two ways it didn't, both invisible on the canonical `--n 500 --out docs/savings`:

* The `.md` prose was two fixed strings -- "Synthetic 500-row workload" and
  "live in `savings.json`" -- so `--n 10 --out d/run_small` wrote a table
  whose every row said 10 under a header saying 500, pointing at a file the
  run never wrote (it wrote `run_small.json`, #231/D-023).
* Every published `saved_usd` was the *rounded* baseline minus the *unrounded*
  total, beside a separately rounded `total_usd`. Over n = 0..120, 40 strategy
  rows published a saving that was not baseline minus spent (n=5 batch:
  0.000289 + 0.000289 against 0.000579), 24 published `-0.0` -- a zero-hit
  semantic cache read as `$-0.0000 | -0.0%` -- and 302 of 2720 cumulative rows
  disagreed with their own two totals.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_savings import main as bench_main  # noqa: E402
from scripts.bench_savings import run_bench  # noqa: E402

_SIZES = range(0, 61)


def _negative_zero(x: object) -> bool:
    return isinstance(x, float) and x == 0.0 and math.copysign(1.0, x) < 0


@pytest.fixture(scope="module")
def payloads() -> dict[int, dict]:
    return {n: run_bench(n=n) for n in _SIZES}


def test_report_prose_names_this_runs_rows_and_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Through `main`, the only caller that knows the JSON's name."""
    assert bench_main(["--dry", "--n", "10", "--out", str(tmp_path / "run_small")]) == 0
    _ = capsys.readouterr()
    md = (tmp_path / "run_small.md").read_text(encoding="utf-8")
    assert "Synthetic 10-row workload" in md
    assert "500-row" not in md
    (footer,) = [line for line in md.splitlines() if line.startswith("Cumulative savings per row")]
    (named,) = re.findall(r"`([^`]+)`", footer)
    # The file the report points at is one this run actually wrote.
    assert named == "run_small.json"
    assert (tmp_path / named).is_file()


def test_every_published_saving_is_baseline_minus_spent(payloads: dict[int, dict]) -> None:
    rows = [(n, s) for n, p in payloads.items() for s in p["strategies"]]
    bad = [
        (n, s["strategy"], s["baseline_usd"], s["total_usd"], s["saved_usd"])
        for n, s in rows
        if s["saved_usd"] != round(s["baseline_usd"] - s["total_usd"], 6)
    ]
    assert bad == []


def test_no_saving_is_published_as_negative_zero(payloads: dict[int, dict]) -> None:
    signed = [
        (n, s["strategy"], field)
        for n, p in payloads.items()
        for s in p["strategies"]
        for field in ("saved_usd", "saved_pct")
        if _negative_zero(s[field])
    ]
    assert signed == []


def test_every_cumulative_row_is_baseline_minus_spent(payloads: dict[int, dict]) -> None:
    bad = [
        (n, strategy, row["row_index"])
        for n, p in payloads.items()
        for strategy, series in p["cumulative_savings_by_strategy"].items()
        for row in series
        if row["cumulative_saved_usd"]
        != round(row["baseline_total_usd"] - row["strategy_total_usd"], 6)
        or _negative_zero(row["cumulative_saved_usd"])
    ]
    assert bad == []


def test_the_sweep_contains_the_cases_the_rules_are_about(payloads: dict[int, dict]) -> None:
    """The three arms above are vacuous unless the population reaches both
    harms -- and a fix that clamped or took `abs` would pass them too, so the
    population must also hold a real loss that has to stay negative."""
    strategies = [s for p in payloads.values() for s in p["strategies"]]
    equal_spend = [
        s
        for s in strategies
        if s["strategy"].startswith("semantic cache")
        and s["n_rows"] > 0
        and s["total_usd"] == s["baseline_usd"]
    ]
    assert equal_spend, "no zero-hit semantic cache in the sweep: the -0.0 case is unexercised"
    assert all(s["saved_usd"] == 0.0 and s["saved_pct"] == 0.0 for s in equal_spend)
    losses = [s for s in strategies if s["strategy"].startswith("uncertainty router")]
    assert any(s["saved_usd"] < 0 and s["saved_pct"] < 0 for s in losses), (
        "a real loss must stay negative"
    )
