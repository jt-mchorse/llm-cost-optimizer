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
- `--no-dry`: reserved for real-API mode, which is **not implemented**.
  It exits 2 with a message rather than shipping a fabricated version;
  wiring it needs a real dataset, real cheap/strong adapters, and an
  operator-supplied `ANTHROPIC_API_KEY` (D-007's posture, §10's rule).
  The operator commits the resulting `docs/threshold_report.md` once
  that lands and they've vetted the curve.

The dry path's dataset is `_build_sample_items()` — five hand-crafted
rows, hardcoded. There is deliberately no flag for supplying your own;
it arrives with the real-API adapters, not before.

Usage:
    python scripts/tune_threshold.py --dry --out docs/threshold_demo
    python scripts/tune_threshold.py --dry --out docs/threshold_demo --thresholds 0.2,0.5,0.8
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cost_optimizer.router import (  # noqa: E402
    EntropySignal,
    EscalationSignal,
    UncertaintyRouter,
)
from scripts._io import atomic_write_text, resolve_out_stem  # noqa: E402


@dataclass(frozen=True)
class ThresholdSweepRow:
    threshold: float
    escalation_rate: float
    # `None` means "this class had no rows at this threshold", not "quality was
    # zero". Quality is a judge score on [0, 1], so 0.0 is the floor of the
    # metric's own range — the worst measurable outcome, not an abstention.
    # Both ends of a sweep empty a class by construction (nothing escalates at a
    # high threshold; everything escalates at 0.0), so the endpoints of every
    # sweep are where this fires. See D-018 and `sweep` below.
    mean_quality_cheap: float | None
    mean_quality_escalated: float | None
    # Not Optional: computed over the whole population (`overall_total / n`), so
    # it is a real measurement in every row regardless of how the classes split.
    mean_quality_overall: float
    dollars_per_request: float
    n: int

    def to_dict(self) -> dict[str, Any]:
        # Seven-field contract (#54) — replaces `asdict(r)` in the
        # _build_payload list-comp so a future internal-only field on
        # ThresholdSweepRow can't silently leak into the sweep JSON
        # consumers (docs / dashboard / external analysis).
        return {
            "threshold": self.threshold,
            "escalation_rate": self.escalation_rate,
            "mean_quality_cheap": self.mean_quality_cheap,
            "mean_quality_escalated": self.mean_quality_escalated,
            "mean_quality_overall": self.mean_quality_overall,
            "dollars_per_request": self.dollars_per_request,
            "n": self.n,
        }


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
    """Pure-function sweep usable in `--dry` mode and tests.

    Raises:
        ValueError: when `items` is empty. An empty population is **refused**
            here, where the sibling script's `run_bench(n=0)` *abstains*
            (D-019). That divergence is deliberate; D-020 records it and
            `docs/architecture.md` states the rule. The short version is the
            shape of what each one would publish:

            `run_bench(n=0)` still reports real measurements -- `n_rows: 0`,
            `total_usd: 0.0`, `saved_usd: 0.0`, `total_prompt_tokens: 0` --
            so nulling its four *ratios* leaves a payload that is still about
            a run that happened. `ThresholdSweepRow` has seven fields, of
            which `threshold` echoes the caller's input and `n` counts the
            empty population; the other **five are all ratios or means over
            that same population**. Abstaining here would emit one row per
            threshold whose every measured field is `null` -- an artifact
            that looks like an eight-threshold sweep and measures nothing.
            Measured, not assumed: the abstaining variant was built and run,
            and it produces exactly that.

            `main()` already refuses this script's *other* empty input
            population, and for this reason: the `--thresholds` guard below
            exits 2 rather than let "an empty sweep ... overwrite the
            artifact with zero rows at exit 0". Both empty inputs reach the
            same committed `docs/threshold_demo.json`; only one of the two
            was guarded.

    The guard keys off **the input population being empty**, never off the
    measurements coming out at zero -- a one-row sweep whose qualities and
    dollars are all a real `0.0` is a measurement and is returned as one, the
    same distinction `_ratio_or_none` draws in `bench_savings`.

    It is checked ahead of the threshold loop, so it does not depend on
    `thresholds` being non-empty to fire: `sweep([], [])` never enters the
    loop, never divides, and previously returned `[]` -- silently answering
    "no rows" to a question about an empty dataset.

    Unreachable from the CLI by construction: `main` calls this with
    `_build_sample_items()`, five hardcoded rows. This is a library contract
    (#224), which is why `main` grows no handler for it.
    """
    if not items:
        raise ValueError(
            "sweep() requires at least one item; got an empty items list. "
            "Every measured field of a sweep row is a ratio or a mean over "
            "this population, so an empty one has no sweep to report "
            "(D-020; the sibling scripts/bench_savings.py abstains instead "
            "because its payload keeps real sums -- see docs/architecture.md)."
        )

    judge_scores: dict[str, float] = {}
    for item in items:
        judge_scores[item["cheap_text"]] = item["cheap_quality"]

    rows: list[ThresholdSweepRow] = []
    for t in thresholds:
        # Use the entropy signal for the threshold sweep; judge stays at
        # a fixed 0.7 so this curve is exclusively about entropy. The
        # cross-signal interaction is its own follow-up plot.
        adapter = _StubCheapAdapter(items)
        # Annotated as the protocol, not inferred as the concrete class:
        # `UncertaintyRouter.signals` is `list[EscalationSignal]` and `list` is
        # invariant, so a `list[EntropySignal]` binding is rejected. The
        # equivalent call in `bench_savings._cumulative_savings` passes the
        # literal inline, where mypy infers from the parameter's type instead —
        # which is why only this one surfaced once `scripts/` entered the gate.
        signals: list[EscalationSignal] = [EntropySignal(threshold=t)]
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
        # Mean quality on the rows where we stayed cheap, and on the rows where
        # we escalated. Either list may be empty, and an empty class has no
        # mean — so report `None`, which reaches the JSON as `null`.
        #
        # This previously used `... if cheap_qualities else 0.0`, with a comment
        # claiming 0.0 let "the per-class fields reflect that". It did not:
        # these are judge scores on [0, 1], so 0.0 is the *floor of the metric's
        # own range*, indistinguishable in kind from a real worst-case
        # measurement and pulling any plot or aggregate of the series to the
        # bottom. `main()` already refuses a fabricated dollar at JSON egress
        # (the --cheap-dollars/--strong-dollars finite/non-negative guard); this
        # applies the same rule to the quality fields in the same payload.
        # `docs/savings.json` is the in-repo precedent for the shape: it
        # publishes `"router_stats": null` for the strategies where no router
        # ran. (#221, D-018)
        mean_cheap = sum(cheap_qualities) / len(cheap_qualities) if cheap_qualities else None
        mean_escalated = (
            sum(escalated_qualities) / len(escalated_qualities) if escalated_qualities else None
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


#: Decimal places the chart annotations have always used, and still use whenever
#: two are enough to tell every threshold in the sweep apart. The shipped
#: default sweep (`0.0,0.5,1.0,1.2,1.4,1.6,1.8,2.0`) is separable at two, so the
#: ordinary chart is byte-identical.
_LABEL_PLACES = 2

#: Ceiling on widening. A double round-trips in at most 17 significant digits,
#: and `--thresholds` is range-checked to finite values `>= 0` — but `set()`
#: only guarantees the thresholds are distinct *as doubles*, and two distinct
#: doubles at small magnitude (`1e-300` vs `2e-300`) render identically at any
#: fixed number of decimal places. That is what `repr` below is for.
_LABEL_MAX_PLACES = 17


def _distinct_labels(values: list[float], *, places: int = _LABEL_PLACES) -> list[str]:
    """Render every threshold in one sweep so no two annotations collide.

    `main` builds the sweep as ``sorted(set(float(t) for t in ...))``, so the
    thresholds are **guaranteed distinct**. Rendering them at a fixed two places
    published a chart that disagreed with that guarantee (#227)::

        thresholds: [0.85, 0.851, 1.25, 1.253]
        labels:     ['t=0.85', 't=0.85', 't=1.25', 't=1.25']

    Four points, two labels — on a chart whose only purpose is to let an
    operator pick a threshold off the quality/cost frontier.

    **This is a set-wide rule, not the pairwise one the six sibling fixes use.**
    `prompt-regression-suite` D-012 and its siblings all render *two numbers in
    one sentence*, so "widen while the two render identically" is the right
    shape there. Here there is no pair: a label is wrong when it collides with
    *any other label in the same chart*. The width is therefore chosen for the
    whole set at once and shared by every label, which also keeps the
    annotations readable as a column.

    Stated precisely, because the obvious weaker version is not obviously
    weaker: on **sorted** input, checking only adjacent pairs is *equivalent* —
    rendering is monotonic, so if every neighbour differs then every pair does.
    That neighbour was built and it passed every arm. The two rules diverge only
    when the input is unsorted (``[0.85, 1.0, 0.8501]`` at two places renders
    ``t=0.85``, ``t=1.00``, ``t=0.85`` — adjacent pairs all differ, the first
    and third do not). `main` sorts the sweep, so this is robustness rather than
    a live defect — but this function takes a list and cannot see an invariant
    its one caller happens to maintain, and a rule that silently depends on a
    caller's ordering is one refactor away from being wrong.

    Widening starts at the current two places rather than jumping to a
    shortest-round-trip rendering, and that is decided by the ordinary case
    rather than by taste: `repr` would close this class just as well and would
    republish every default label from ``t=0.00`` to ``t=0.0``, churning the
    chart everyone actually looks at to fix a chart almost nobody generates.
    """
    if not values:
        return []
    for width in range(places, max(places, _LABEL_MAX_PLACES) + 1):
        rendered = [f"t={v:.{width}f}" for v in values]
        if len(set(rendered)) == len(rendered):
            return rendered
    # Distinct as doubles, unseparable by any fixed width in the budget. `repr`
    # round-trips a float by definition, so these are always distinct — at the
    # cost of a ragged column, which is the right trade against two points
    # a reader cannot tell apart.
    return [f"t={v!r}" for v in values]


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
    # Set-wide, not per-row: the width that separates every threshold in
    # this sweep from every other one (#227).
    labels = _distinct_labels([r.threshold for r in rows])
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


def _build_parser() -> argparse.ArgumentParser:
    """The CLI parser, extracted from `main` so its defaults are readable.

    `main` built this inline, which left the shipped `--thresholds` default
    reachable only by running the tool. #227's byte-identity arm asserts
    against the *shipped* sweep rather than a literal retyped into a test,
    because a retyped default silently stops testing the default the day
    someone changes it. Pure extraction: no flag, default or help string is
    altered.
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Run against committed stub fixtures (default in CI / no API). "
            "`--no-dry` opts into real-API mode, which currently errors out "
            "until the operator wires real adapters per D-007."
        ),
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
    return p


def main(argv: list[str] | None = None) -> int:
    p = _build_parser()
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

    # `--cheap-dollars`/`--strong-dollars` are operator input too (argparse only
    # enforces `float`, which happily parses `nan`/`inf`/negative). A non-finite
    # value flows into `json.dumps` as a bare `NaN`/`Infinity` token — invalid
    # JSON a strict parser rejects — and into `sweep()`'s per-request dollar
    # arithmetic; a negative value fabricates a negative cost. Both would land in
    # the committed `docs/threshold_demo.json`. Reject them with the same clean
    # exit-2 operator-misconfig contract as `--thresholds` below and the
    # portfolio's "no non-finite / no fabricated dollar at JSON egress" rule
    # (ModelPricing/BatchCostQuote.__post_init__).
    for _flag, _value in (
        ("--cheap-dollars", args.cheap_dollars),
        ("--strong-dollars", args.strong_dollars),
    ):
        if not math.isfinite(_value) or _value < 0.0:
            print(
                f"::error::{_flag} must be a finite number >= 0; got {_value!r}",
                file=sys.stderr,
            )
            return 2

    # `--thresholds` is operator input: a non-numeric token made `float(t)` raise
    # a raw `ValueError` traceback at exit 1, breaking the same operator-misconfig
    # exit-2 contract the `--no-dry` guard above honors. Translate it to a clean
    # stderr line + exit 2 (bad-input sibling of the write-seam guard below).
    try:
        thresholds = sorted(set(float(t) for t in args.thresholds.split(",") if t.strip()))
    except ValueError as e:
        print(
            f"::error::--thresholds must be comma-separated numbers; got {args.thresholds!r} ({e})",
            file=sys.stderr,
        )
        return 2

    # The guard above covers a token `float()` REFUSES. It does not cover the
    # tokens `float()` accepts and the sweep cannot use (#186) — and the comment
    # four lines up already names them: "argparse only enforces `float`, which
    # happily parses `nan`/`inf`/negative". That rule was applied to the two
    # dollar flags and not to the flag this script is named after.
    #
    # Measured pre-fix, exit codes captured before any pipe:
    #
    #   --thresholds 'abc'      -> exit 2, clean ::error:: line        (correct)
    #   --thresholds 'nan,0.5'  -> exit 1, raw ValueError traceback
    #   --thresholds 'inf'      -> exit 1, raw ValueError traceback
    #   --thresholds '-1.0,0.5' -> exit 1, raw ValueError traceback
    #   --thresholds '1e400'    -> exit 1, raw ValueError traceback
    #
    # All four reached `EntropySignal.__post_init__`'s own guard (#36) from
    # inside `sweep()`, so a script-level *usage* error surfaced as a
    # library-level exception at the wrong exit code. `1e400` is the one an
    # operator cannot see coming: it is a finite-looking decimal literal that
    # `float()` returns as `inf`.
    for _t in thresholds:
        if not math.isfinite(_t) or _t < 0.0:
            print(
                f"::error::--thresholds values must be finite numbers >= 0; got {_t!r} "
                f"(from {args.thresholds!r})",
                file=sys.stderr,
            )
            return 2

    # An empty list is not a sweep. `--thresholds ''` and `--thresholds ','`
    # both survive `if t.strip()` and left `thresholds == []`, so `sweep()`
    # returned no rows and the script exited **0** having overwritten the
    # committed 8-row `docs/threshold_demo.json` — the default `--out` stem, and
    # the artifact the README's documented command produces — with
    # `"rows": []`. With matplotlib installed `ax.plot([], [])` is legal too, so
    # a blank PNG was written beside it and reported as `plot wrote`.
    #
    # `--thresholds "$THRESHOLDS"` with an unset variable is the ordinary way to
    # reach this from CI or a wrapper script, and a success exit code is exactly
    # what makes it survive review.
    if not thresholds:
        print(
            f"::error::--thresholds must name at least one threshold; got {args.thresholds!r} "
            "(an empty sweep would overwrite the artifact with zero rows at exit 0)",
            file=sys.stderr,
        )
        return 2

    # Resolve `--out` here, alongside the other argument checks and *before*
    # `sweep` runs. `Path.with_suffix` raises ValueError on a stem with no
    # filename component (`''`, `.`, `/`), and it did so below, outside the
    # write-seam try — so a stemless `--out` escaped as a raw traceback at exit
    # 1 (#174), and only after the whole sweep had already completed. Same
    # operator-misconfig contract as the `--thresholds` guard directly above.
    try:
        out_stem = resolve_out_stem(args.out)
    except ValueError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 2

    rows = sweep(
        _build_sample_items(),
        thresholds,
        cheap_dollars=args.cheap_dollars,
        strong_dollars=args.strong_dollars,
    )

    out_json = out_stem.with_suffix(".json")
    out_png = out_stem.with_suffix(".png")
    payload = {
        "mode": "dry",
        "cheap_dollars_per_request": args.cheap_dollars,
        "strong_dollars_per_request": args.strong_dollars,
        "rows": [r.to_dict() for r in rows],
    }
    # The output stem is operator input too: an unwritable `--out` makes
    # `atomic_write_text` raise OSError, which without this guard escaped `main`
    # as a raw traceback at exit 1. Translate it to a clean stderr line + exit 2,
    # matching the `--no-dry` guard above and the portfolio write-seam contract
    # (llm-eval-harness#158/#159, python-async-llm-pipelines#84).
    try:
        atomic_write_text(out_json, json.dumps(payload, indent=2, sort_keys=True))
    except OSError as e:
        print(f"::error::could not write sweep artifacts: {e}", file=sys.stderr)
        return 2
    # The PNG plot is the sibling write seam sharing the same operator `--out`.
    # `_try_save_plot` degrades gracefully when matplotlib is *absent* (ImportError
    # -> returns False), but its `mkdir`/`savefig` raise OSError when the `.png`
    # path itself is unwritable (a dir pre-existing at the `.png` stem, a read-only
    # file, a path component that is a file) — reachable when the `.json` write
    # above succeeds but the `.png` specifically can't be written, with matplotlib
    # installed. Without this guard that OSError escaped `main` as a raw traceback
    # at exit 1, the same contract break #156 fixed for the JSON seam one line up.
    try:
        plot_written = _try_save_plot(rows, out_png)
    except OSError as e:
        print(f"::error::could not write sweep plot: {e}", file=sys.stderr)
        return 2
    print(f"sweep wrote {out_json}")
    if plot_written:
        print(f"plot wrote  {out_png}")
    else:
        print("plot skipped (matplotlib not installed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
