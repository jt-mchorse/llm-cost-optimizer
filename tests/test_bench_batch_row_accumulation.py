"""The batch row agrees exactly with its own series and with the baseline (#281).

`_run_batch` totalled its dollars and its quality with the builtin `sum()`, while
`_run_baseline` and `_cumulative_savings` accumulate with a `+=` loop. Since
Python 3.12 `sum()` over floats is compensated (Neumaier), so the two differ in
the last bit; on an odd baseline-token count the batch total is a
half-micro-dollar and that bit decides which way `round(., 6)` breaks the tie.
On 3.12, 42 of n = 1..200 published a batch `total_usd` one micro-dollar away
from the last row of its own cumulative series, and n=8 published a batch
`mean_quality` of 0.8763 beside the baseline's 0.8762 -- for a strategy whose
answers come from the same model. The existing reconciliation tests compare
with `abs=1e-5`, which is wider than the disagreement, so the checks here are
exact.

On Python 3.11 `sum()` is the plain loop, so this file cannot go red there
against the old code; CI's 3.12 job is the one that sees a regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_savings import run_bench  # noqa: E402

_SIZES = range(1, 201)


@pytest.fixture(scope="module")
def payloads() -> dict[int, dict]:
    return {n: run_bench(n=n) for n in _SIZES}


def _batch_row(payload: dict) -> dict:
    return next(s for s in payload["strategies"] if s["strategy"].startswith("batch API"))


def test_batch_row_equals_the_end_of_its_own_cumulative_series(
    payloads: dict[int, dict],
) -> None:
    mismatches = []
    for n, payload in payloads.items():
        row = _batch_row(payload)
        end = payload["cumulative_savings_by_strategy"]["batch"][-1]
        published = (row["total_usd"], row["saved_usd"])
        series = (end["strategy_total_usd"], end["cumulative_saved_usd"])
        if published != series:
            mismatches.append((n, published, series))
    assert mismatches == []


def test_batch_row_quality_is_exactly_the_baseline_quality(payloads: dict[int, dict]) -> None:
    mismatches = [
        (n, _batch_row(p)["mean_quality"], p["strategies"][0]["mean_quality"])
        for n, p in payloads.items()
        if _batch_row(p)["mean_quality"] != p["strategies"][0]["mean_quality"]
    ]
    assert mismatches == []


def test_the_sweep_contains_the_tie_case_the_rule_is_about(payloads: dict[int, dict]) -> None:
    # n=7 at the default seed is not guaranteed to be a tie; what matters is that
    # the sweep includes odd baseline-token totals, where the batch total is an
    # exact half-micro-dollar before rounding.
    odd = [n for n, p in payloads.items() if p["total_prompt_tokens"] % 2 == 1]
    assert len(odd) >= 50
