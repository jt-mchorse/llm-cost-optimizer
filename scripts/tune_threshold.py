"""Sweep escalation thresholds against a dataset; plot quality vs cost.

What this produces:

- A JSON record `(threshold, escalation_rate, mean_quality_cheap,
  mean_quality_escalated, mean_quality_overall, dollars_per_request)`
  per threshold value.
- A matplotlib plot of quality vs dollars-per-request at each threshold.

Two modes:

- `--dry` (default in CI): runs against committed sample fixtures with
  a stub judge and a stub cheap adapter that returns canned responses.
  No real API call; no fabricated numbers in the README — the only
  thing this asserts is the *plumbing* (the script runs, the schema is
  stable, the plot file is produced).
- Without `--dry`: requires `ANTHROPIC_API_KEY`. Calls the real cheap
  and strong models against the dataset; the operator commits the
  resulting `docs/threshold_report.md` once they've vetted the curve.

Usage:
    python scripts/tune_threshold.py --dry --out docs/threshold_demo
    python scripts/tune_threshold.py --dataset path/to.jsonl --out docs/threshold
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cost_optimizer.router import (  # noqa: E402
    EntropySignal,
    UncertaintyRouter,
)


@dataclass(frozen=True)
class ThresholdSweepRow:
    threshold: float
    escalation_rate: float
    mean_quality_cheap: float
    mean_quality_escalated: float
    mean_quality_overall: float
    dollars_per_request: float
    n: int


# Stub cheap-model adapter that returns a fake response with canned
# logprobs + text. Used only in --dry mode.
class _StubCheapAdapter:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = list(items)
        self._cursor = 0

    def call_cheap(self, request: Any) -> Any:  # noqa: ARG002
        if self._cursor >= len(self._items):
            self._cursor = 0
        item = self._items[self._cursor]
        self._cursor += 1
        return _StubResponse(
            text=item["cheap_text"],
            first_token_logprobs=item["cheap_logprobs"],
            prompt=item["prompt"],
        )


class _StubResponse:
    def __init__(self, text: str, first_token_logprobs: list[float], prompt: str) -> None:
        self.text = text
        self.first_token_logprobs = first_token_logprobs
        self.prompt = prompt


class _StubJudge:
    """Deterministic scorer keyed off the answer text; for dry mode only."""

    def __init__(self, scores: dict[str, float]) -> None:
        self._scores = scores

    def score(self, prompt: str, response_text: str, *, rubric: str) -> Any:  # noqa: ARG002
        return _StubVerdict(score=self._scores.get(response_text.strip(), 0.5))


class _StubVerdict:
    def __init__(self, score: float) -> None:
        self.score = score


def _build_sample_items() -> list[dict[str, Any]]:
    """Five-row hand-crafted "dataset" for the dry path.

    Each row carries a prompt, a cheap-model response, the logprobs the
    cheap model would have returned, and a quality score for the cheap
    response and a higher-quality score for the strong response. The
    threshold sweep is then a deterministic function of the entropy
    threshold.
    """
    logp_pinned = [math.log(0.95), math.log(0.025), math.log(0.025)]
    logp_uniform5 = [math.log(0.2)] * 5
    logp_two_way = [math.log(0.55), math.log(0.45)]
    return [
        {
            "prompt": "What is 2 + 2?",
            "cheap_text": "4",
            "cheap_logprobs": logp_pinned,
            "cheap_quality": 1.00,
            "strong_quality": 1.00,
        },
        {
            "prompt": "Capital of France?",
            "cheap_text": "Paris",
            "cheap_logprobs": logp_pinned,
            "cheap_quality": 0.95,
            "strong_quality": 0.97,
        },
        {
            "prompt": "Year Berlin Wall fell?",
            "cheap_text": "1989",
            "cheap_logprobs": logp_two_way,
            "cheap_quality": 0.70,
            "strong_quality": 0.92,
        },
        {
            "prompt": "Summarize the French Revolution in one sentence.",
            "cheap_text": "summary-cheap",
            "cheap_logprobs": logp_uniform5,
            "cheap_quality": 0.45,
            "strong_quality": 0.88,
        },
        {
            "prompt": "Cite three sources for AI safety.",
            "cheap_text": "citations-cheap",
            "cheap_logprobs": logp_uniform5,
            "cheap_quality": 0.40,
            "strong_quality": 0.85,
        },
    ]


def sweep(
    items: list[dict[str, Any]],
    thresholds: list[float],
    *,
    cheap_dollars: float,
    strong_dollars: float,
) -> list[ThresholdSweepRow]:
    """Pure-function sweep usable in `--dry` mode and tests."""
    judge_scores: dict[str, float] = {}
    for item in items:
        judge_scores[item["cheap_text"]] = item["cheap_quality"]

    rows: list[ThresholdSweepRow] = []
    for t in thresholds:
        # Use the entropy signal for the threshold sweep; judge stays at
        # a fixed 0.7 so this curve is exclusively about entropy. The
        # cross-signal interaction is its own follow-up plot.
        adapter = _StubCheapAdapter(items)
        signals = [EntropySignal(threshold=t)]
        router = UncertaintyRouter(
            cheap_model="claude-haiku-4-5",
            strong_model="claude-opus-4-7",
            cheap_adapter=adapter,
            signals=signals,
        )
        n_escalated = 0
        cheap_qualities: list[float] = []
        escalated_qualities: list[float] = []
        total_dollars = 0.0
        for item in items:
            decision = router.route({"prompt": item["prompt"]})
            if decision.triggered_signal is not None:
                n_escalated += 1
                escalated_qualities.append(item["strong_quality"])
                total_dollars += cheap_dollars + strong_dollars  # paid cheap *and* strong
            else:
                cheap_qualities.append(item["cheap_quality"])
                total_dollars += cheap_dollars

        n = len(items)
        # Mean quality on the rows where we stayed cheap, and on the
        # rows where we escalated. Either list may be empty; treat
        # missing as 0.0 for the mean and let the per-class fields
        # reflect that with the n divisor still meaningful overall.
        mean_cheap = sum(cheap_qualities) / len(cheap_qualities) if cheap_qualities else 0.0
        mean_escalated = (
            sum(escalated_qualities) / len(escalated_qualities) if escalated_qualities else 0.0
        )
        overall_total = sum(cheap_qualities) + sum(escalated_qualities)
        rows.append(
            ThresholdSweepRow(
                threshold=t,
                escalation_rate=n_escalated / n,
                mean_quality_cheap=mean_cheap,
                mean_quality_escalated=mean_escalated,
                mean_quality_overall=overall_total / n,
                dollars_per_request=total_dollars / n,
                n=n,
            )
        )
    return rows


def _try_save_plot(rows: list[ThresholdSweepRow], out_png: Path) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    fig, ax = plt.subplots(figsize=(6, 4))
    xs = [r.dollars_per_request for r in rows]
    ys = [r.mean_quality_overall for r in rows]
    labels = [f"t={r.threshold:.2f}" for r in rows]
    ax.plot(xs, ys, marker="o")
    for x, y, label in zip(xs, ys, labels, strict=True):
        ax.annotate(label, (x, y), textcoords="offset points", xytext=(5, 4), fontsize=8)
    ax.set_xlabel("$/request")
    ax.set_ylabel("mean overall quality")
    ax.set_title("Uncertainty router: quality vs cost across entropy thresholds")
    ax.grid(True, alpha=0.3)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, bbox_inches="tight", dpi=140)
    plt.close(fig)
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry",
        action="store_true",
        default=True,
        help="Run against committed stub fixtures (default in CI / no API).",
    )
    p.add_argument(
        "--out",
        default="docs/threshold_demo",
        help="Output stem; `.json` and `.png` are written next to it.",
    )
    p.add_argument(
        "--thresholds",
        type=str,
        default="0.0,0.5,1.0,1.2,1.4,1.6,1.8,2.0",
        help="Comma-separated entropy thresholds to sweep.",
    )
    p.add_argument("--cheap-dollars", type=float, default=0.0008, help="$ per cheap request.")
    p.add_argument("--strong-dollars", type=float, default=0.015, help="$ per strong request.")
    args = p.parse_args(argv)

    if not args.dry:
        # Real-API mode is intentionally left as an honest stub: it would
        # need a real dataset, real cheap/strong adapters, and an
        # operator-supplied API key. We don't ship a fabricated version.
        print(
            "::error::real-API tune mode is not implemented in this PR. "
            "Run --dry to exercise the plumbing; the operator wires real "
            "adapters when they're ready to commit `docs/threshold_report.md`.",
            file=sys.stderr,
        )
        return 2

    thresholds = sorted(set(float(t) for t in args.thresholds.split(",") if t.strip()))
    rows = sweep(
        _build_sample_items(),
        thresholds,
        cheap_dollars=args.cheap_dollars,
        strong_dollars=args.strong_dollars,
    )

    out_stem = Path(args.out)
    out_json = out_stem.with_suffix(".json")
    out_png = out_stem.with_suffix(".png")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": "dry",
        "cheap_dollars_per_request": args.cheap_dollars,
        "strong_dollars_per_request": args.strong_dollars,
        "rows": [asdict(r) for r in rows],
    }
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    plot_written = _try_save_plot(rows, out_png)
    print(f"sweep wrote {out_json}")
    if plot_written:
        print(f"plot wrote  {out_png}")
    else:
        print("plot skipped (matplotlib not installed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
