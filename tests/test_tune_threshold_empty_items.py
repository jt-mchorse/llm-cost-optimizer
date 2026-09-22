"""An empty *population* is refused here and abstained in the sibling script (#224, D-020).

`bench_savings.run_bench(n=0)` completes and abstains: every ratio is `null`,
every sum keeps its real `0` (D-019). `tune_threshold.sweep([], ...)` raised a
bare `ZeroDivisionError` on `n_escalated / n`. Both scripts advertise their
function as *the* pure-function entry point, so a caller reading one and then
the other had no way to predict which they would get.

D-020 resolves it as a refusal rather than an abstention, and the reason is the
shape of what each one would publish. `run_bench(n=0)` still reports real
measurements — `n_rows: 0`, `total_usd: 0.0`, `saved_usd: 0.0` — so nulling its
four ratios leaves a payload that is still *about* a run. `ThresholdSweepRow`
has seven fields, of which `threshold` echoes the input and `n` counts the
empty population; the other five are all ratios or means over that population.
The abstaining variant was built and run: it emits one row per threshold whose
every measured field is `null`.

And `main()` had already answered the question for this script's *other* empty
input population — `--thresholds` exits 2 rather than let "an empty sweep ...
overwrite the artifact with zero rows at exit 0". Both empty inputs reach the
same committed `docs/threshold_demo.json`. Only one was guarded.

Two arms in this module are green on both trees and are the ones that reject
the wrong neighbours:

- `test_a_sweep_measuring_all_zeros_is_still_a_sweep` rejects a guard that
  keys off the *measurements* being zero instead of the *population* being
  empty — the same distinction `_ratio_or_none` draws in `bench_savings`.
- `test_bench_savings_still_abstains_rather_than_refusing` pins the divergence
  as deliberate, so a later "make the two scripts consistent" sweep that moves
  `bench_savings` to refusing goes red instead of looking like tidying.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.bench_savings import run_bench
from scripts.tune_threshold import (
    _build_sample_items,
    main,
    sweep,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
COMMITTED = _REPO_ROOT / "docs" / "threshold_demo.json"

DEFAULT_THRESHOLDS = [0.0, 0.5, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
DEFAULT_CHEAP_DOLLARS = 0.0008
DEFAULT_STRONG_DOLLARS = 0.015


def _one_item(*, cheap_quality: float, strong_quality: float) -> dict[str, Any]:
    """A single sweep row with pinned logprobs, so entropy is deterministic."""
    return {
        "prompt": "only row",
        "cheap_text": "only-answer",
        # Near-certain first token -> low entropy -> stays cheap at any
        # threshold above ~0.35, escalates at 0.0.
        "cheap_logprobs": [-0.05129329438755058, -3.0, -3.0],
        "cheap_quality": cheap_quality,
        "strong_quality": strong_quality,
    }


# ----------------------------------------------------------------------
# The refusal itself
# ----------------------------------------------------------------------


def test_an_empty_items_list_is_refused_not_swept() -> None:
    """The headline: `ZeroDivisionError` on `n_escalated / n` becomes a contract error.

    Pre-fix this raised `ZeroDivisionError` — an arithmetic accident leaking
    out of a loop body, naming neither the argument at fault nor the rule.
    """
    with pytest.raises(ValueError, match="requires at least one item"):
        sweep(
            [],
            DEFAULT_THRESHOLDS,
            cheap_dollars=DEFAULT_CHEAP_DOLLARS,
            strong_dollars=DEFAULT_STRONG_DOLLARS,
        )


def test_the_refusal_names_the_sibling_scripts_opposite_posture() -> None:
    """A caller who hits this must be able to find out why the two differ.

    The whole defect in #224 was that a reader of one module could not predict
    the other's behaviour. A message that says only "empty" reproduces that.
    """
    with pytest.raises(ValueError, match="requires at least one item") as excinfo:
        sweep([], [0.5], cheap_dollars=0.001, strong_dollars=0.01)
    message = str(excinfo.value)
    assert "bench_savings" in message
    assert "D-020" in message


def test_an_empty_items_list_is_refused_even_with_no_thresholds() -> None:
    """The guard is on the input population, not on the loop happening to run.

    `sweep([], [])` never enters the threshold loop, so it never divides:
    pre-fix it returned `[]` at no error, silently answering "no rows" to a
    question asked about an empty dataset. Checking `items` *ahead* of the
    loop is what makes the contract independent of the second argument.
    """
    with pytest.raises(ValueError, match="requires at least one item"):
        sweep([], [], cheap_dollars=0.001, strong_dollars=0.01)


# ----------------------------------------------------------------------
# What the refusal must NOT swallow
# ----------------------------------------------------------------------


def test_a_single_item_still_sweeps() -> None:
    """Non-vacuity floor: the guard fires on empty, not on small.

    A neighbour demanding "enough rows to be meaningful" (>= 2, >= 5) would
    pass every arm above and break the one-row case, which is a legitimate
    sweep.
    """
    rows = sweep(
        [_one_item(cheap_quality=0.7, strong_quality=0.9)],
        [0.0, 1.0],
        cheap_dollars=0.001,
        strong_dollars=0.01,
    )
    assert len(rows) == 2
    assert all(r.n == 1 for r in rows)
    # At t=0.0 everything escalates; at t=1.0 this low-entropy row stays cheap.
    by_threshold = {r.threshold: r for r in rows}
    assert by_threshold[0.0].escalation_rate == 1.0
    assert by_threshold[1.0].escalation_rate == 0.0


def test_a_sweep_measuring_all_zeros_is_still_a_sweep() -> None:
    """Keyed off the population being empty, never off the numbers being zero.

    This is the separating arm. A one-row sweep whose judge scores are a real
    `0.0` and whose per-request dollars are a real `0.0` is a *measurement*:
    every quantity here came out at the bottom of its range because that is
    what was measured, not because nothing was. A neighbour that refuses (or
    nulls) on falsy output rather than on an empty input passes every arm
    above and destroys this one — the same laundering `_ratio_or_none` in
    `bench_savings` is written to avoid.
    """
    rows = sweep(
        [_one_item(cheap_quality=0.0, strong_quality=0.0)],
        [1.0],
        cheap_dollars=0.0,
        strong_dollars=0.0,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.n == 1
    assert row.escalation_rate == 0.0
    assert row.mean_quality_cheap == 0.0
    assert row.mean_quality_overall == 0.0
    assert row.dollars_per_request == 0.0
    # And the empty *class* still abstains, which is D-018 and is untouched.
    assert row.mean_quality_escalated is None


def test_bench_savings_still_abstains_rather_than_refusing() -> None:
    """The divergence is deliberate — pin it so a later "cleanup" cannot erase it.

    D-020 decides that these two scripts answer the degenerate case
    differently *on purpose*. Without this arm, a future sweep applying the
    refusal "consistently" to `bench_savings` would look like tidying and pass
    the whole suite. `run_bench(n=0)` must keep completing, and its sums must
    keep their real zeros (D-019).
    """
    payload = run_bench(n=0)
    assert payload["n_rows"] == 0
    assert payload["total_prompt_tokens"] == 0
    baseline = payload["strategies"][0]
    # Sums are facts about a run that happened.
    assert baseline["n_rows"] == 0
    assert baseline["total_usd"] == 0.0
    assert baseline["saved_usd"] == 0.0
    # Ratios abstain.
    assert baseline["saved_pct"] is None
    assert baseline["mean_quality"] is None


# ----------------------------------------------------------------------
# Reachability and the committed artifact
# ----------------------------------------------------------------------


def test_the_cli_population_is_non_empty_so_the_guard_is_unreachable_there() -> None:
    """Pins the reachability claim that keeps this `priority:low`.

    `main` sweeps `_build_sample_items()`, five hardcoded rows with no flag to
    replace them. If a later change makes the dataset operator-supplied, this
    arm is the reminder that `main` then needs the exit-2 translation the
    `--thresholds` guard already has.
    """
    assert len(_build_sample_items()) == 5


def test_the_committed_artifact_still_regenerates_byte_identically(tmp_path: Path) -> None:
    """Green on both trees by design: no published number may move.

    This change adds a refusal on a path the CLI cannot reach. The arm that
    proves it stayed that way is the artifact, and it is also what rejects an
    over-broad neighbour that pushes the guard up into `main`.
    """
    stem = tmp_path / "threshold_demo"
    assert main(["--dry", "--out", str(stem)]) == 0
    produced = json.loads(stem.with_suffix(".json").read_text())
    committed = json.loads(COMMITTED.read_text())
    assert produced == committed
    assert len(committed["rows"]) == len(DEFAULT_THRESHOLDS)
