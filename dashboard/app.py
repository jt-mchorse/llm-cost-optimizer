"""Savings-dashboard Streamlit page.

Reads the JSON written by ``scripts/bench_savings.py`` and renders:

- A per-strategy bar chart of dollars saved vs. baseline.
- A cumulative-savings line chart (one line per strategy, x = row index).
- A quality column that flags whether mean quality stayed ≥ baseline.
- The workload-mix sanity panel + the raw strategy table.

The dashboard does *no* recomputation — it reads the JSON the bench
script produced and renders it. That means re-running the dashboard
against an old artifact shows the old numbers, which is the right
default: the operator opens the dashboard *after* committing the
refreshed benchmark, and the two are kept in sync by the file on
disk, not by background computation.

The Streamlit dep is optional (the package's `[dashboard]` extra).
This file imports `streamlit` at module load; only the dashboard
entry-point pays that cost.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

DEFAULT_JSON = Path("docs/savings.json")


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        st.error(
            f"Savings artifact not found at `{path}`. Run "
            f"`python scripts/bench_savings.py --dry --out docs/savings` "
            f"in the repo root to produce it."
        )
        st.stop()
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    # Streamlit forwards everything after `--` to the script; pull our
    # own flag out without consuming Streamlit's own args.
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--json", default=str(DEFAULT_JSON))
    known, _ = p.parse_known_args(argv)
    return known


def main() -> None:
    args = _parse_args(sys.argv[1:])
    payload = _load(Path(args.json))

    st.set_page_config(page_title="llm-cost-optimizer · savings", layout="wide")
    st.title("LLM cost optimizer — savings dashboard")
    st.caption(
        f"Mode: **{payload['mode']}** · rows: {payload['n_rows']:,} · "
        f"cheap: `{payload['cheap_model']}` · strong: `{payload['strong_model']}`"
    )

    # ----- top row: workload mix + total spend -----
    mix = payload["workload_mix"]
    col_mix, col_spend = st.columns([1, 2])
    with col_mix:
        st.subheader("Workload mix")
        st.dataframe(
            pd.DataFrame([{"class": k, "rows": v} for k, v in mix.items()]).set_index("class"),
            width="content",
        )
        st.caption(f"Total prompt tokens: {payload['total_prompt_tokens']:,}")

    baseline_total = payload["strategies"][0]["total_usd"]
    baseline_quality = payload["strategies"][0]["mean_quality"]

    with col_spend:
        st.subheader("Dollars saved vs. baseline")
        # Skip the baseline (it's 0 saved by construction).
        savings_df = pd.DataFrame(
            [
                {
                    "strategy": s["strategy"],
                    "saved_usd": s["saved_usd"],
                    "saved_pct": s["saved_pct"],
                }
                for s in payload["strategies"][1:]
            ]
        )
        st.bar_chart(savings_df.set_index("strategy")["saved_usd"])
        st.caption(
            "Negative values are legitimate — the uncertainty router "
            "trades dollars for quality on hard rows, so it shows a "
            "cost increase against the cheap-on-everything baseline."
        )

    # ----- cumulative savings line -----
    st.subheader("Cumulative $ saved per row")
    cum_by = payload["cumulative_savings_by_strategy"]
    long_rows: list[dict[str, Any]] = []
    for strategy_key, series in cum_by.items():
        for entry in series:
            long_rows.append(
                {
                    "row_index": entry["row_index"],
                    "strategy": strategy_key,
                    "cumulative_saved_usd": entry["cumulative_saved_usd"],
                }
            )
    if long_rows:
        cum_df = (
            pd.DataFrame(long_rows)
            .pivot(index="row_index", columns="strategy", values="cumulative_saved_usd")
            .sort_index()
        )
        st.line_chart(cum_df)
    else:
        st.info("No cumulative series in the artifact.")

    # ----- quality maintained -----
    st.subheader("Quality maintained?")
    quality_rows: list[dict[str, Any]] = []
    for s in payload["strategies"]:
        delta = s["mean_quality"] - baseline_quality
        verdict = "yes" if delta >= -0.01 else "regression"
        quality_rows.append(
            {
                "strategy": s["strategy"],
                "mean_quality": s["mean_quality"],
                "delta_vs_baseline": round(delta, 4),
                "verdict": verdict,
            }
        )
    st.dataframe(pd.DataFrame(quality_rows).set_index("strategy"), width="stretch")
    st.caption(
        "`delta_vs_baseline` tolerates a 0.01 drift (rounding + tied "
        "sampling); a larger drop is flagged so the operator inspects "
        "before publishing."
    )

    # ----- strategy table -----
    st.subheader("Per-strategy details")
    rows_for_table: list[dict[str, Any]] = []
    for s in payload["strategies"]:
        row = {
            "strategy": s["strategy"],
            "n_rows": s["n_rows"],
            "total_usd": s["total_usd"],
            "baseline_usd": s["baseline_usd"],
            "saved_usd": s["saved_usd"],
            "saved_pct": s["saved_pct"],
            "mean_quality": s["mean_quality"],
        }
        row.update({f"extra.{k}": v for k, v in s.get("extra", {}).items()})
        rows_for_table.append(row)
    st.dataframe(pd.DataFrame(rows_for_table).set_index("strategy"), width="stretch")

    with st.expander("Raw JSON"):
        st.json(payload)

    st.caption(
        f"Baseline = {payload['strategies'][0]['strategy']} "
        f"(${baseline_total:.4f}). Numbers refresh by re-running "
        "`scripts/bench_savings.py`."
    )


if __name__ == "__main__":
    main()
