"""Tests for `scripts/bench_savings.py`.

Goals:
1. The bench is deterministic — re-running with the same seed yields
   identical numbers.
2. The workload-mix invariants hold (60/30/10 split).
3. Every strategy's math is internally consistent: saved == baseline -
   total, saved_pct == saved / baseline, etc.
4. The two cache strategies show positive savings on a workload with
   redundancy (a regression test against a future refactor that
   accidentally disables caching).
5. The cumulative series ends at the same total the strategy summary
   reports (cross-check between the two derivations).
6. The Streamlit dashboard module imports cleanly when the extra is
   installed (when it isn't, the test skips rather than failing).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_savings import (  # noqa: E402
    BATCH_DISCOUNT_FACTOR,
    CHEAP_MODEL,
    STRONG_MODEL,
    StrategyResult,
    _build_workload,
    _cumulative_savings,
    _format_markdown,
    main,
    run_bench,
)


def test_bench_is_deterministic_across_two_calls() -> None:
    a = run_bench(n=200, seed=0xC057)
    b = run_bench(n=200, seed=0xC057)
    # The full payload must be byte-identical across two calls with the
    # same seed. JSON round-trip ignores dataclass identity.
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_workload_mix_matches_documented_60_30_10_split() -> None:
    payload = run_bench(n=500)
    mix = payload["workload_mix"]
    assert mix["redundant"] == 300
    assert mix["easy"] == 150
    assert mix["hard"] == 50
    assert mix["redundant"] + mix["easy"] + mix["hard"] == payload["n_rows"]


def test_each_strategy_math_is_internally_consistent() -> None:
    payload = run_bench(n=500)
    baseline_total = payload["strategies"][0]["total_usd"]
    for s in payload["strategies"]:
        # saved == baseline_usd - total_usd (within float-rounding tolerance)
        expected_saved = round(s["baseline_usd"] - s["total_usd"], 6)
        assert s["saved_usd"] == pytest.approx(expected_saved, abs=1e-6)
        # saved_pct == saved_usd / baseline_usd (when baseline > 0)
        if s["baseline_usd"] > 0:
            expected_pct = round(s["saved_usd"] / s["baseline_usd"], 4)
            assert s["saved_pct"] == pytest.approx(expected_pct, abs=1e-4)
        # baseline reference is consistent across rows
        assert s["baseline_usd"] == baseline_total


def test_prompt_cache_strategy_saves_money_on_redundant_workload() -> None:
    """Regression guard: prompt caching should produce positive savings.

    If a future refactor breaks the cache_write/cache_read pricing math,
    this number flips sign and the test catches it.
    """
    payload = run_bench(n=500)
    prompt_cache = next(s for s in payload["strategies"] if "prompt caching" in s["strategy"])
    assert prompt_cache["saved_usd"] > 0
    # With one stable system prefix across 500 rows, savings should be
    # at least 50% of baseline (1 write + 499 reads × 0.10).
    assert prompt_cache["saved_pct"] >= 0.5
    assert prompt_cache["extra"]["cache_writes"] == 1
    assert prompt_cache["extra"]["cache_reads"] == 499


def test_semantic_cache_strategy_saves_money_and_reports_hits() -> None:
    payload = run_bench(n=500)
    semantic = next(s for s in payload["strategies"] if "semantic cache" in s["strategy"])
    assert semantic["saved_usd"] > 0
    # Redundant rows are 60% of the workload. After the first occurrence
    # of each (template × paraphrase × class_tag) bucket, later rows hit.
    # Lower bound: at least 100 hits.
    assert semantic["extra"]["hits"] >= 100
    assert semantic["extra"]["hit_rate"] >= 0.2


def test_router_increases_cost_but_improves_quality_on_hard_rows() -> None:
    """The router *should not* save money on this workload; it should
    improve quality by spending more on hard rows. This is the documented
    honest behavior, not a bug.
    """
    payload = run_bench(n=500)
    baseline = payload["strategies"][0]
    router = next(s for s in payload["strategies"] if "router" in s["strategy"])
    # The router pays cheap on every row + strong on hard rows.
    assert router["total_usd"] > baseline["total_usd"]
    # Mean quality should be strictly better than baseline because hard
    # rows now get the strong model's higher quality score.
    assert router["mean_quality"] > baseline["mean_quality"]
    # Escalation rate matches the workload's 10% hard split.
    assert router["extra"]["escalated"] == 50
    assert router["extra"]["escalation_rate"] == 0.1


def test_batch_strategy_saves_exactly_one_minus_discount() -> None:
    payload = run_bench(n=500)
    baseline = payload["strategies"][0]
    batch = next(s for s in payload["strategies"] if "batch API" in s["strategy"])
    expected_total = round(baseline["total_usd"] * BATCH_DISCOUNT_FACTOR, 6)
    assert batch["total_usd"] == pytest.approx(expected_total, abs=1e-6)
    assert batch["saved_pct"] == pytest.approx(1 - BATCH_DISCOUNT_FACTOR, abs=1e-4)


@pytest.mark.parametrize(
    "strategy_key",
    ["prompt_cache", "semantic_cache", "router", "batch"],
)
def test_cumulative_series_ends_at_strategy_total(strategy_key: str) -> None:
    """The last row of each cumulative series must reconcile against the
    strategy summary's total — they're independent derivations of the
    same number, so the cross-check guards against drift.
    """
    payload = run_bench(n=500)
    series = payload["cumulative_savings_by_strategy"][strategy_key]
    assert series, "cumulative series must be non-empty"
    final = series[-1]
    assert final["row_index"] == payload["n_rows"]
    # Match by substring because the strategy name carries config detail
    # (threshold, discount factor, etc.).
    needle = {
        "prompt_cache": "prompt caching",
        "semantic_cache": "semantic cache",
        "router": "router",
        "batch": "batch API",
    }[strategy_key]
    strategy = next(s for s in payload["strategies"] if needle in s["strategy"])
    assert final["strategy_total_usd"] == pytest.approx(strategy["total_usd"], abs=1e-5)
    assert final["baseline_total_usd"] == pytest.approx(strategy["baseline_usd"], abs=1e-5)


def test_cumulative_series_is_monotone_in_row_index() -> None:
    payload = run_bench(n=200)
    for series in payload["cumulative_savings_by_strategy"].values():
        indices = [row["row_index"] for row in series]
        assert indices == sorted(indices)
        # Both totals are monotonically non-decreasing (each row adds
        # non-negative cost on both sides).
        totals = [row["baseline_total_usd"] for row in series]
        assert totals == sorted(totals)


def test_pricing_models_match_the_pricing_table() -> None:
    """Belt-and-braces: the constants the bench uses for cheap/strong are
    real entries in `cost_optimizer.pricing` — not invented strings."""
    from cost_optimizer.pricing import get_pricing

    cheap = get_pricing(CHEAP_MODEL)
    strong = get_pricing(STRONG_MODEL)
    # Sanity: strong is meaningfully more expensive than cheap. If a
    # future Anthropic pricing update inverts this, the dashboard's
    # quality-vs-cost narrative is wrong and the operator should notice.
    assert strong.input_per_mtok > cheap.input_per_mtok


def test_workload_is_stable_under_repeated_build() -> None:
    a = _build_workload(n=200, seed=0xC057)
    b = _build_workload(n=200, seed=0xC057)
    assert [r.row_id for r in a] == [r.row_id for r in b]
    assert [r.prompt_tokens for r in a] == [r.prompt_tokens for r in b]


def test_cumulative_savings_strategy_unknown_raises() -> None:
    workload = _build_workload(n=10)
    with pytest.raises(ValueError, match="unknown strategy"):
        _cumulative_savings(workload, "not-a-strategy")


def test_markdown_renders_every_strategy_row() -> None:
    payload = run_bench(n=100)
    md = _format_markdown(payload)
    assert "| Strategy | Rows |" in md
    for s in payload["strategies"]:
        # Strategy name (or its prefix before the first parenthesis) is in the table.
        # Embedded backticks etc. round-trip fine for the substring check.
        assert s["strategy"].split(" (")[0] in md


def test_main_writes_artifacts_and_returns_zero(tmp_path: Path) -> None:
    stem = tmp_path / "out" / "savings"
    rc = main(["--dry", "--out", str(stem), "--n", "100"])
    assert rc == 0
    j = stem.with_suffix(".json")
    m = stem.with_suffix(".md")
    w = stem.parent / "savings_workload.json"
    assert j.exists()
    assert m.exists()
    assert w.exists()
    payload = json.loads(j.read_text(encoding="utf-8"))
    assert payload["n_rows"] == 100
    assert payload["schema_version"] == 1
    assert {s["strategy"] for s in payload["strategies"]}.issuperset(
        {
            s
            for s in (
                "baseline (no optimization, cheap model)",
                "prompt caching (system prefix)",
            )
        }
    )


def test_main_real_api_mode_errors_out_until_implemented(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--no-dry` reaches the D-007 real-API-not-implemented guard and exits 2.

    The flag previously couldn't be set to False (action="store_true" with
    default=True made the guard unreachable). It now uses BooleanOptionalAction
    so `--no-dry` actually opts into the real-API branch.
    """
    rc = main(["--no-dry", "--out", str(tmp_path / "should_not_exist")])
    captured = capsys.readouterr()
    assert rc == 2
    assert "real-API bench mode is not implemented" in captured.err
    assert not (tmp_path / "should_not_exist.json").exists()


def test_main_dry_default_path_still_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no flag, the stub path runs to completion and writes the bench artifacts."""
    out_stem = tmp_path / "savings"
    rc = main(["--out", str(out_stem), "--n", "60"])
    _ = capsys.readouterr()
    assert rc == 0
    assert out_stem.with_suffix(".json").exists()
    assert out_stem.with_suffix(".md").exists()


def test_streamlit_dashboard_module_imports_when_extra_installed() -> None:
    if importlib.util.find_spec("streamlit") is None:
        pytest.skip("streamlit not installed (the [dashboard] extra)")
    if importlib.util.find_spec("pandas") is None:
        pytest.skip("pandas not installed (the [dashboard] extra)")
    sys.path.insert(0, str(_REPO_ROOT))
    # `dashboard.app` is the module; importing it should not raise.
    import dashboard.app  # noqa: F401


# ----------------------------------------------------------------------
# #54: StrategyResult.to_dict — explicit field-by-field contract
# (no dataclasses.asdict). Same pattern as #50 (CacheTelemetry.to_dict)
# and #52 (CacheStats.to_dict) at the package level.
# ----------------------------------------------------------------------


def _make_result(extra: dict | None = None) -> StrategyResult:
    return StrategyResult(
        strategy="test",
        n_rows=10,
        total_usd=1.5,
        baseline_usd=2.0,
        saved_usd=0.5,
        saved_pct=25.0,
        mean_quality=0.85,
        extra={"hits": 5} if extra is None else extra,
    )


def test_strategy_result_to_dict_field_set_is_pinned() -> None:
    d = _make_result().to_dict()
    assert sorted(d.keys()) == [
        "baseline_usd",
        "extra",
        "mean_quality",
        "n_rows",
        "saved_pct",
        "saved_usd",
        "strategy",
        "total_usd",
    ]


def test_strategy_result_to_dict_values_round_trip() -> None:
    r = _make_result(extra={})
    assert r.to_dict() == {
        "strategy": "test",
        "n_rows": 10,
        "total_usd": 1.5,
        "baseline_usd": 2.0,
        "saved_usd": 0.5,
        "saved_pct": 25.0,
        "mean_quality": 0.85,
        "extra": {},
    }


def test_strategy_result_to_dict_extra_is_shallow_copied() -> None:
    # The frozen dataclass's extra mapping must not be reachable via
    # the returned dict — caller mutation must not bleed back.
    r = _make_result()
    out = r.to_dict()
    out["extra"]["leaked"] = "yes"
    assert "leaked" not in r.extra


def test_run_bench_payload_strategies_use_to_dict_shape() -> None:
    # Acceptance regression: every row under payload["strategies"]
    # has the same field set the to_dict contract pins. Catches a
    # future drift where the list-comp re-introduces asdict.
    payload = run_bench(n=50, seed=1)
    assert isinstance(payload["strategies"], list)
    assert len(payload["strategies"]) > 0
    for row in payload["strategies"]:
        assert sorted(row.keys()) == [
            "baseline_usd",
            "extra",
            "mean_quality",
            "n_rows",
            "saved_pct",
            "saved_usd",
            "strategy",
            "total_usd",
        ]
