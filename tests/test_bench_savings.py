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
# #54 + #64: StrategyResult.to_dict — explicit field-by-field contract
# (no dataclasses.asdict). Same pattern as #50 (CacheTelemetry.to_dict)
# and #52 (CacheStats.to_dict) at the package level. Nine fields after
# #64 added the optional `router_stats` snapshot.
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
        "router_stats",
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
        # #64: router_stats defaults to None for non-router strategies.
        "router_stats": None,
    }


def test_strategy_result_to_dict_extra_is_shallow_copied() -> None:
    # The frozen dataclass's extra mapping must not be reachable via
    # the returned dict — caller mutation must not bleed back.
    r = _make_result()
    out = r.to_dict()
    out["extra"]["leaked"] = "yes"
    assert "leaked" not in r.extra


def test_strategy_result_to_dict_router_stats_is_deep_copied_when_present() -> None:
    # #64: router_stats contains nested dicts (per_signal_trips,
    # per_signal_measured). Caller mutation of those nested dicts must
    # not bleed back into the frozen dataclass — the to_dict path
    # rebuilds the nested dicts as fresh objects.
    inner_trips = {"entropy": 3}
    r = StrategyResult(
        strategy="router-test",
        n_rows=10,
        total_usd=1.0,
        baseline_usd=2.0,
        saved_usd=1.0,
        saved_pct=50.0,
        mean_quality=0.9,
        router_stats={
            "total_routes": 10,
            "escalations": 3,
            "cheap_only": 7,
            "per_signal_trips": inner_trips,
            "per_signal_measured": {"entropy": 10},
            "escalation_rate": 0.3,
        },
    )
    out = r.to_dict()
    out["router_stats"]["per_signal_trips"]["leaked"] = 99
    out["router_stats"]["escalations"] = 999
    # Original frozen value untouched; nested dict identity also fresh.
    assert "leaked" not in inner_trips
    assert r.router_stats is not None
    assert r.router_stats["escalations"] == 3


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
            "router_stats",
            "saved_pct",
            "saved_usd",
            "strategy",
            "total_usd",
        ]


def test_run_bench_payload_router_row_carries_router_stats() -> None:
    # #64: the router strategy's row must carry a `router_stats` dict
    # snapshotted from `UncertaintyRouter.stats.to_dict()` (PR #63 / issue #62).
    # The four non-router rows must have `router_stats=None` so the
    # field is unambiguously a router-only annotation.
    payload = run_bench(n=500, seed=0xC057)
    router_rows = [s for s in payload["strategies"] if "router" in s["strategy"]]
    assert len(router_rows) == 1, "expected exactly one router strategy row"
    rs = router_rows[0]["router_stats"]
    assert isinstance(rs, dict), "router row must carry a router_stats dict"

    assert set(rs) == {
        "total_routes",
        "escalations",
        "cheap_only",
        "per_signal_trips",
        "per_signal_measured",
        "escalation_rate",
    }, f"router_stats keys drifted: {sorted(rs)}"

    # Cross-check against the manually-tracked extra counters that the
    # bench has emitted since before #64 — same physical quantity, two
    # names. If these ever disagree, RouterStats and the manual counter
    # have drifted and one of them is wrong.
    extra = router_rows[0]["extra"]
    assert rs["escalations"] == extra["escalated"], (
        f"RouterStats.escalations ({rs['escalations']}) disagrees with manual "
        f"extra.escalated ({extra['escalated']}). One of the two derivations "
        f"is wrong; route() side-effect accounting and the bench loop counter "
        f"must agree."
    )
    assert rs["escalation_rate"] == pytest.approx(extra["escalation_rate"]), (
        f"RouterStats.escalation_rate ({rs['escalation_rate']}) disagrees with "
        f"manual extra.escalation_rate ({extra['escalation_rate']})."
    )
    assert rs["total_routes"] == 500, "router was called once per row in the workload"
    assert rs["cheap_only"] + rs["escalations"] == rs["total_routes"]

    # Single-signal config today: every `route()` measures `entropy`,
    # and only `entropy` ever trips. Lock that explicitly so a future
    # multi-signal change has to update this assertion deliberately.
    assert rs["per_signal_measured"].get("entropy") == 500
    assert rs["per_signal_trips"].get("entropy") == extra["escalated"]


def test_run_bench_payload_non_router_rows_have_null_router_stats() -> None:
    # #64: the four non-router strategies must have `router_stats=None`
    # so a downstream consumer (e.g., the dashboard) can identify the
    # router row unambiguously by `router_stats is not None` without a
    # string-substring check on the strategy label.
    payload = run_bench(n=50, seed=1)
    non_router = [s for s in payload["strategies"] if "router" not in s["strategy"]]
    assert len(non_router) == 4, "expected 4 non-router strategies (baseline + 3)"
    for row in non_router:
        assert row["router_stats"] is None, (
            f"strategy {row['strategy']!r} unexpectedly carries router_stats "
            f"({row['router_stats']!r}); only the uncertainty-router strategy "
            f"should populate this field."
        )


# ----------------------------------------------------------------------
# #66: dashboard panel helpers — `_pick_router_row`, `_router_panel_rows`
# ----------------------------------------------------------------------


def _make_synthetic_payload(*, with_router: bool, router_stats: dict | None = None) -> dict:
    strategies: list[dict] = [
        {"strategy": "baseline (strong on everything)", "router_stats": None},
        {"strategy": "uncertainty router (entropy threshold 1.5)", "router_stats": None},
        {"strategy": "prompt-cache wrapper", "router_stats": None},
    ]
    if with_router:
        strategies[1]["router_stats"] = router_stats or {
            "total_routes": 100,
            "cheap_only": 90,
            "escalations": 10,
            "escalation_rate": 0.1,
            "per_signal_trips": {"entropy": 10},
            "per_signal_measured": {"entropy": 100},
        }
    return {"strategies": strategies}


def test_pick_router_row_finds_row_with_router_stats() -> None:
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    sys.path.insert(0, str(_REPO_ROOT))
    from dashboard.app import _pick_router_row  # noqa: E402

    payload = _make_synthetic_payload(with_router=True)
    row = _pick_router_row(payload)
    assert row is not None
    assert row["router_stats"]["total_routes"] == 100


def test_pick_router_row_returns_none_when_no_router_stats_present() -> None:
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    sys.path.insert(0, str(_REPO_ROOT))
    from dashboard.app import _pick_router_row  # noqa: E402

    payload = _make_synthetic_payload(with_router=False)
    assert _pick_router_row(payload) is None


def test_pick_router_row_does_not_substring_match_strategy_label() -> None:
    """A row whose `strategy` happens to contain `"router"` but whose
    `router_stats` is None must not be picked. The picker is structural,
    not lexical — relabeling the bench's router won't break the panel."""
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    sys.path.insert(0, str(_REPO_ROOT))
    from dashboard.app import _pick_router_row  # noqa: E402

    payload = {
        "strategies": [
            {"strategy": "router-shaped label without stats", "router_stats": None},
        ]
    }
    assert _pick_router_row(payload) is None


def test_router_panel_rows_emits_one_row_per_signal_in_sorted_order() -> None:
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    sys.path.insert(0, str(_REPO_ROOT))
    from dashboard.app import _router_panel_rows  # noqa: E402

    router_stats = {
        "per_signal_trips": {"entropy": 8, "logprob": 2},
        "per_signal_measured": {"entropy": 100, "logprob": 92},
    }
    rows = _router_panel_rows(router_stats)
    assert [r["signal"] for r in rows] == ["entropy", "logprob"]
    e = next(r for r in rows if r["signal"] == "entropy")
    assert e["trips"] == 8
    assert e["measured"] == 100
    assert e["trip_rate"] == 0.08


def test_router_panel_rows_trip_rate_defaults_to_zero_when_measured_is_zero() -> None:
    """A signal that's wired up but never reached (earlier signal short-
    circuited every row) shows measured=0; trip_rate must be 0.0, not a
    ZeroDivisionError."""
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    sys.path.insert(0, str(_REPO_ROOT))
    from dashboard.app import _router_panel_rows  # noqa: E402

    router_stats = {
        "per_signal_trips": {"unreached": 0},
        "per_signal_measured": {"unreached": 0},
    }
    rows = _router_panel_rows(router_stats)
    assert rows == [{"signal": "unreached", "trips": 0, "measured": 0, "trip_rate": 0.0}]


def test_router_panel_rows_handles_signal_in_only_one_dict() -> None:
    """`per_signal_trips` and `per_signal_measured` can independently
    list a signal — the union (sorted) is the row set, missing values
    default to 0."""
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    sys.path.insert(0, str(_REPO_ROOT))
    from dashboard.app import _router_panel_rows  # noqa: E402

    rows = _router_panel_rows(
        {
            "per_signal_trips": {"entropy": 5},
            "per_signal_measured": {"logprob": 10},
        }
    )
    assert [r["signal"] for r in rows] == ["entropy", "logprob"]
    e = next(r for r in rows if r["signal"] == "entropy")
    lp = next(r for r in rows if r["signal"] == "logprob")
    assert e["measured"] == 0
    assert e["trip_rate"] == 0.0
    assert lp["trips"] == 0
    assert lp["trip_rate"] == 0.0


def test_router_panel_rows_on_real_savings_json_produces_expected_entropy_row() -> None:
    """Cross-check against the committed `docs/savings.json` artifact."""
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    sys.path.insert(0, str(_REPO_ROOT))
    from dashboard.app import _pick_router_row, _router_panel_rows  # noqa: E402

    payload = json.loads((_REPO_ROOT / "docs" / "savings.json").read_text(encoding="utf-8"))
    router = _pick_router_row(payload)
    assert router is not None
    rows = _router_panel_rows(router["router_stats"])
    # The committed bench uses only the entropy signal today; lock that.
    assert [r["signal"] for r in rows] == ["entropy"]
    entropy = rows[0]
    assert entropy["measured"] == router["router_stats"]["per_signal_measured"]["entropy"]
    assert entropy["trips"] == router["router_stats"]["per_signal_trips"]["entropy"]
    # And the trip rate matches escalation_rate (single-signal lock).
    assert abs(entropy["trip_rate"] - router["router_stats"]["escalation_rate"]) < 1e-9
