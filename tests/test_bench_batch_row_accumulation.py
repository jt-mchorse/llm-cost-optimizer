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

The sweep calls the four functions the batch row and its series are made of,
not `run_bench` (#312). A full `run_bench` per size spent its time in the
semantic-cache and router strategies, which the batch row never reads: 200 of
them were 768s of `test (3.11)` on CI, against ~1 ms for the batch path at
n=200. `test_the_sweep_path_is_what_run_bench_publishes` keeps the shortcut
honest by comparing it with one real `run_bench`.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cost_optimizer.batch import BATCH_DISCOUNT_FACTOR  # noqa: E402
from cost_optimizer.pricing import get_pricing  # noqa: E402
from scripts.bench_savings import (  # noqa: E402
    CHEAP_MODEL,
    _build_workload,
    _cumulative_savings,
    _run_baseline,
    _run_batch,
    run_bench,
)

_SEED = 0xC057
_SIZES = range(1, 201)
# The size where both `total_usd` and `mean_quality` disagreed on 3.12.
_PIPELINE_N = 8


def _batch_path(n: int) -> dict:
    """The slice of `run_bench(n=n, seed=_SEED)` the batch row is built from."""
    workload = _build_workload(n=n, seed=_SEED)
    baseline = _run_baseline(workload)
    return {
        "workload": workload,
        "baseline": baseline.to_dict(),
        "batch": _run_batch(workload, baseline).to_dict(),
        "series": _cumulative_savings(workload, "batch"),
    }


@pytest.fixture(scope="module")
def paths() -> dict[int, dict]:
    return {n: _batch_path(n) for n in _SIZES}


def test_the_sweep_path_is_what_run_bench_publishes(paths: dict[int, dict]) -> None:
    payload = run_bench(n=_PIPELINE_N, seed=_SEED)
    path = paths[_PIPELINE_N]
    by_name = {s["strategy"]: s for s in payload["strategies"]}
    assert by_name[path["batch"]["strategy"]] == path["batch"]
    assert payload["strategies"][0] == path["baseline"]
    assert payload["cumulative_savings_by_strategy"]["batch"] == path["series"]


def test_batch_row_equals_the_end_of_its_own_cumulative_series(
    paths: dict[int, dict],
) -> None:
    mismatches = []
    for n, path in paths.items():
        row = path["batch"]
        end = path["series"][-1]
        published = (row["total_usd"], row["saved_usd"])
        series = (end["strategy_total_usd"], end["cumulative_saved_usd"])
        if published != series:
            mismatches.append((n, published, series))
    assert mismatches == []


def test_batch_row_quality_is_exactly_the_baseline_quality(paths: dict[int, dict]) -> None:
    mismatches = [
        (n, p["batch"]["mean_quality"], p["baseline"]["mean_quality"])
        for n, p in paths.items()
        if p["batch"]["mean_quality"] != p["baseline"]["mean_quality"]
    ]
    assert mismatches == []


def test_the_sweep_contains_the_tie_case_the_rule_is_about(paths: dict[int, dict]) -> None:
    # n=7 at the default seed is not guaranteed to be a tie; what matters is that
    # the sweep includes odd baseline-token totals, where the batch total is an
    # exact half-micro-dollar before rounding.
    odd = [n for n, p in paths.items() if sum(r.prompt_tokens for r in p["workload"]) % 2 == 1]
    assert len(odd) >= 50


def test_the_sweep_contains_sizes_where_the_summation_order_decides(
    paths: dict[int, dict],
) -> None:
    # The red arms above only fire on 3.12+, where `sum()` is compensated. This
    # one holds on any interpreter: `math.fsum` stands in for a compensated sum,
    # so if a workload or pricing change ever left no size where the two
    # accumulations round apart, the sweep would stop guarding #281 and this
    # says so, on 3.11 as well.
    rate = get_pricing(CHEAP_MODEL).input_per_mtok / 1_000_000
    deciding_total = []
    deciding_quality = []
    for n, p in paths.items():
        costs = [r.prompt_tokens * rate * BATCH_DISCOUNT_FACTOR for r in p["workload"]]
        qualities = [r.cheap_quality for r in p["workload"]]
        loop_total = 0.0
        for c in costs:
            loop_total += c
        loop_quality = 0.0
        for q in qualities:
            loop_quality += q
        if round(loop_total, 6) != round(math.fsum(costs), 6):
            deciding_total.append(n)
        if round(loop_quality / n, 4) != round(math.fsum(qualities) / n, 4):
            deciding_quality.append(n)
    assert len(deciding_total) >= 10
    assert deciding_quality != []
