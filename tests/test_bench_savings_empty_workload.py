"""A ratio over an empty population abstains; a sum over one is a real zero (#223, D-019).

D-018 ruled, for `scripts/tune_threshold.py`, that the mean of an empty class is
`null` rather than `0.0`: a judge score lives on `[0, 1]`, so `0.0` is the floor
of the metric's own range — the worst *measurable* outcome, indistinguishable in
kind from a real measurement. A default at an extreme of a comparison does not
abstain, it ranks.

`scripts/bench_savings.py` had the identical shape in five places, and an
existing test that pinned it as correct — `hit_rate == 0.0` and
`escalation_rate == 0.0`, under a comment calling the `mean_quality` divisions
"already guarded". They were guarded against *crashing*, by substituting the
floor.

Two things this module is careful about, because both are easy to get wrong:

1. **The abstention keys off the denominator, never off the value.** A workload
   that genuinely scores `0.0` must publish `0.0`. `round(0.0, 4)` is falsy, so
   a `... or None` at the call site or at egress launders a real worst-case
   measurement into `null` — the neighbour D-018 explicitly rejected.
   `test_a_real_zero_measurement_is_not_laundered_into_none` is that arm.
2. **A guard on the computation says nothing about the presentation.** Both
   human sinks formatted these with `:.1%` / `:.3f`, which raise `TypeError` on
   `None`. Making the computation honest without touching them would have
   converted a fabricated number into a crash.

Reachability, stated plainly: `main()` refuses `--n < 1` with exit 2 (#157), so
the committed `docs/savings.json` and `docs/savings.md` cannot carry this. It is
reachable through `run_bench()`, the documented library entry point, which is
how the pre-existing zero-row test calls it. `test_the_shipped_artifacts_do_not_
move` pins that no published number changed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_savings import (  # noqa: E402
    StrategyResult,
    _fmt_ratio,
    _format_markdown,
    _ratio_or_none,
    _saved_pct_or_none,
    main,
    run_bench,
)

# Every ratio the payload publishes, as (container, key). The point of listing
# them together is that the rule is one rule: "no population, no ratio".
_STRATEGY_RATIOS = ("saved_pct", "mean_quality")
_EXTRA_RATIOS = {
    "semantic cache": "hit_rate",
    "uncertainty router": "escalation_rate",
    "batch API": "compare_savings_pct_with_outputs",
}
# Sums, not ratios. A zero here is a measurement: nothing was spent, nothing ran.
_STRATEGY_SUMS = ("n_rows", "total_usd", "saved_usd")


def _by_name(payload: dict) -> dict[str, dict]:
    return {s["strategy"]: s for s in payload["strategies"]}


# ----------------------------------------------------------------------
# The rule
# ----------------------------------------------------------------------


def test_every_published_ratio_abstains_on_an_empty_workload() -> None:
    payload = run_bench(n=0)
    assert payload["n_rows"] == 0

    for row in payload["strategies"]:
        for key in _STRATEGY_RATIOS:
            assert row[key] is None, f"{row['strategy']}.{key} = {row[key]!r}, expected None"

    rows = _by_name(payload)
    for fragment, key in _EXTRA_RATIOS.items():
        row = next(r for name, r in rows.items() if fragment in name)
        assert row["extra"][key] is None, f"{fragment}.extra[{key}] = {row['extra'][key]!r}"


def test_the_sums_keep_their_real_zero() -> None:
    """The other half of the rule, and the one a blanket `None` sweep would break.

    Nothing was spent on an empty workload, and that is a fact about the run,
    not an absence of one. A fix that nulled every zero would destroy it.
    """
    payload = run_bench(n=0)
    for row in payload["strategies"]:
        for key in _STRATEGY_SUMS:
            assert row[key] == 0, f"{row['strategy']}.{key} = {row[key]!r}, expected 0"
        # Counts behind the absent rates are real too.
        for key, value in row["extra"].items():
            if key in _EXTRA_RATIOS.values() or key == "discount_factor":
                continue
            assert value == 0, f"{row['strategy']}.extra[{key}] = {value!r}"


def test_a_real_zero_measurement_is_not_laundered_into_none() -> None:
    """The arm that rejects the `or None` neighbour.

    `round(0.0, 4)` is falsy, so any abstention keyed off the *value* turns a
    genuine all-worst-case result into `null` — claiming nothing was measured
    when everything was, and in the most alarming way. D-018 rejected exactly
    this neighbour for `tune_threshold.py`; the same trap is here.
    """
    assert _ratio_or_none(0.0, 5) == 0.0
    assert _ratio_or_none(0.0, 5) is not None
    assert _ratio_or_none(0.0, 0) is None
    # A hit rate of zero over real lookups, and a mean quality of zero over real
    # rows, are both publishable measurements.
    assert _ratio_or_none(0, 500) == 0.0
    # ...and the denominator is what decides, not the numerator's truthiness.
    assert _ratio_or_none(3.0, 0) is None


def test_saved_pct_abstains_only_when_there_was_no_spend() -> None:
    baseline = StrategyResult(
        strategy="baseline",
        n_rows=500,
        total_usd=1.25,
        baseline_usd=1.25,
        saved_usd=0.0,
        saved_pct=0.0,
        mean_quality=0.8,
    )
    assert _saved_pct_or_none(0.25, baseline) == 0.2
    # A strategy that genuinely saved nothing against a real baseline publishes
    # 0.0 — an unflattering measurement, not an abstention.
    assert _saved_pct_or_none(0.0, baseline) == 0.0

    empty = StrategyResult(
        strategy="baseline",
        n_rows=0,
        total_usd=0.0,
        baseline_usd=0.0,
        saved_usd=0.0,
        saved_pct=None,
        mean_quality=None,
    )
    assert _saved_pct_or_none(0.0, empty) is None


def test_the_baseline_row_does_not_claim_zero_percent_beside_an_absent_quality() -> None:
    """The baseline is graded against itself, so `0.0%` is definitional...

    ...whenever there is a population. On an empty workload it is not, and a row
    reading `0.0%` next to a `None` mean quality would be incoherent about
    whether anything ran at all.
    """
    full = _by_name(run_bench(n=50))
    baseline_full = next(r for name, r in full.items() if "baseline" in name)
    assert baseline_full["saved_pct"] == 0.0
    assert baseline_full["mean_quality"] is not None

    empty = _by_name(run_bench(n=0))
    baseline_empty = next(r for name, r in empty.items() if "baseline" in name)
    assert baseline_empty["saved_pct"] is None
    assert baseline_empty["mean_quality"] is None


# ----------------------------------------------------------------------
# The presentation half
# ----------------------------------------------------------------------


def test_both_human_sinks_render_an_absent_ratio_instead_of_raising() -> None:
    """A guard on the computation says nothing about the presentation.

    `:.1%` and `:.3f` both raise `TypeError` on `None`, so the honest
    computation would have been a crash at every sink without this.
    """
    payload = run_bench(n=0)

    md = _format_markdown(payload)  # would raise TypeError before the fix
    assert "None" not in md
    for line in md.splitlines():
        if line.startswith("| baseline"):
            assert line.endswith("| 0 | $0.0000 | $0.0000 | — | — | — |")
            break
    else:  # pragma: no cover - the table always has a baseline row
        pytest.fail("no baseline row in the markdown table")


def test_the_stdout_summary_renders_an_absent_ratio(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`main` refuses `--n 0`, so the summary line is exercised directly.

    Pinning it matters anyway: the two sinks format the same fields, and a fix
    applied to one and not the other is the failure mode this repo keeps
    finding. `_fmt_ratio` is shared so they cannot drift.
    """
    assert _fmt_ratio(None, ".1%", absent="n/a") == "n/a"
    assert _fmt_ratio(None, ".3f", absent="—") == "—"
    assert _fmt_ratio(0.0, ".3f", absent="—") == "0.000"
    assert _fmt_ratio(0.2, ".1%", absent="n/a") == "20.0%"

    # And the real path still prints numbers on a real workload.
    rc = main(["--dry", "--n", "5", "--out", str(tmp_path / "s")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "n/a" not in out
    assert "q=" in out


def test_extra_renders_an_absent_rate_as_a_dash_not_the_string_none() -> None:
    md = _format_markdown(run_bench(n=0))
    assert "hit_rate=—" in md
    assert "hit_rate=None" not in md
    assert "escalation_rate=—" in md


# ----------------------------------------------------------------------
# Nothing shipped may move
# ----------------------------------------------------------------------


def test_the_shipped_artifacts_do_not_move(tmp_path: Path) -> None:
    """The whole change must be invisible at the committed `n=500`.

    `--n < 1` exits 2 (#157), so no published artifact can contain an absent
    ratio; this pins that the honest branch never fires on the real workload.
    """
    rc = main(["--dry", "--n", "500", "--out", str(tmp_path / "savings")])
    assert rc == 0

    regenerated = json.loads((tmp_path / "savings.json").read_text())
    committed = json.loads((_REPO_ROOT / "docs" / "savings.json").read_text())
    assert regenerated == committed

    assert (tmp_path / "savings.md").read_text() == (_REPO_ROOT / "docs" / "savings.md").read_text()

    for row in regenerated["strategies"]:
        for key in _STRATEGY_RATIOS:
            assert row[key] is not None, f"{row['strategy']}.{key} went absent at n=500"


def test_the_cli_still_refuses_an_empty_workload(capsys: pytest.CaptureFixture[str]) -> None:
    """This fix does not reopen the path #157 closed.

    Abstaining is better than fabricating, but refusing is better than either
    when the operator asked for a benchmark: a zero-row run is a misconfigured
    invocation, not a result worth writing.
    """
    rc = main(["--dry", "--n", "0", "--out", "/tmp/should-not-be-written"])
    assert rc == 2
    assert "--n must be a positive integer" in capsys.readouterr().err
    assert not Path("/tmp/should-not-be-written.json").exists()
