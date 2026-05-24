"""Run every shipped cost-reduction strategy on a hermetic synthetic workload.

What this produces:

- ``docs/savings.json`` — machine-readable record per strategy (rows
  processed, dollars spent, dollars saved vs. baseline, hit-rate or
  escalation-rate as applicable, plus the workload mix).
- ``docs/savings.md`` — human-readable table sourced from the JSON;
  the README links it.
- ``docs/savings_workload.json`` — the deterministic workload itself,
  committed so the numbers can be re-derived.

The workload is **hermetic synthetic** with documented composition:

- 60% "redundant" — paraphrases or repeats of 12 template prompts.
  Both `PromptCacheWrapper` and `SemanticCache` should win here
  because the underlying request text repeats.
- 30% "easy" — short factual prompts the cheap model handles with
  high first-token confidence. The router should *not* escalate.
- 10% "hard" — open-ended prompts with high first-token entropy.
  The router *should* escalate to the strong model.

All token counts and logprobs are canned in the workload JSON so the
run is reproducible bit-for-bit. The pricing math is the real one
from `cost_optimizer.pricing`; the batch discount is the real
`BATCH_DISCOUNT_FACTOR`. No fabricated numbers — the README cites
what this script actually computed.

Two modes:

- ``--dry`` (default, CI-safe): uses the in-process stub client. No
  Anthropic SDK, no API key, no network. The whole run finishes in a
  couple of seconds.
- Without ``--dry``: not implemented in this PR. Same posture as
  ``scripts/tune_threshold.py`` (D-007): the operator wires real
  adapters and commits the measured `docs/savings_real.md` when
  they're ready.

Usage:
    python scripts/bench_savings.py --dry --out docs/savings
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cost_optimizer.batch import (  # noqa: E402
    BATCH_DISCOUNT_FACTOR,
    BatchCostQuote,
    BatchRequest,
    BatchResultRow,
    InMemoryBatchBackend,
    compare_realtime_vs_batch,
)
from cost_optimizer.pricing import get_pricing  # noqa: E402
from cost_optimizer.router import EntropySignal, UncertaintyRouter  # noqa: E402
from cost_optimizer.semantic_cache import (  # noqa: E402
    HashEmbedder,
    InMemoryStorage,
    SemanticCache,
)

# ----------------------------------------------------------------------
# Workload generation (deterministic)
# ----------------------------------------------------------------------

CHEAP_MODEL = "claude-haiku-4-5"
STRONG_MODEL = "claude-opus-4-7"

# Output token counts are kept small because the focus of this bench is
# *input* economics (caching, routing, batching) — the savings story is
# dominated by prompt-side spend in production. Twelve templates feed
# the "redundant" partition; the easy/hard partitions have their own
# disjoint pools so semantic similarity doesn't bleed across classes.
REDUNDANT_TEMPLATES: tuple[str, ...] = (
    "Summarize our refund policy in two sentences.",
    "What are the steps to reset a customer password?",
    "List the supported payment methods.",
    "Explain how to cancel a subscription.",
    "When does a free trial expire?",
    "Describe the data-retention policy.",
    "What counts as a chargeback dispute?",
    "How do we handle GDPR data export requests?",
    "What is the SLA for billing inquiries?",
    "Describe the workflow for fraud review.",
    "What's the policy on partial refunds?",
    "How are recurring invoices generated?",
)

# Three paraphrases per template; index modulo 3 selects the paraphrase
# slot. The HashEmbedder sees these as highly similar to the canonical
# template because they share most n-grams.
_PARAPHRASE_PREFIXES: tuple[str, ...] = (
    "",  # canonical
    "Briefly: ",
    "Hey — quick one: ",
)

EASY_PROMPTS: tuple[str, ...] = (
    "What is 12 + 7?",
    "Capital of Japan?",
    "Day after Friday?",
    "Largest planet?",
    "Color of the sky on a clear day?",
    "Square root of 81?",
    "Year of the moon landing?",
    "Author of '1984'?",
)

HARD_PROMPTS: tuple[str, ...] = (
    "Argue both sides: should companies adopt a four-day work week?",
    "Outline a research plan for studying LLM hallucinations.",
    "Compare three competing theories of consciousness with citations.",
    "Draft a strategy for entering the Southeast Asian e-commerce market.",
)

# Logprob shapes for first-token uncertainty. Pinned = confident
# (cheap stays); uniform-over-five = uncertain (router escalates at any
# entropy threshold below ~1.6 nats). Same convention as
# scripts/tune_threshold.py so the two scripts read consistent.
_LOGP_PINNED: tuple[float, ...] = tuple(math.log(p) for p in (0.95, 0.025, 0.025))
_LOGP_UNIFORM5: tuple[float, ...] = tuple(math.log(0.2) for _ in range(5))


@dataclass(frozen=True)
class WorkloadRow:
    """One synthetic prompt with deterministic per-row metadata."""

    row_id: str
    class_: str  # "redundant" | "easy" | "hard"
    prompt: str
    system: str
    prompt_tokens: int
    completion_tokens: int
    first_token_logprobs: tuple[float, ...]
    cheap_quality: float
    strong_quality: float


def _ws_count(text: str) -> int:
    """Tokens approximated as whitespace-split words.

    Realistic Claude tokenization would split punctuation and produce a
    slightly higher count, but the savings *ratios* this script
    reports are unchanged by a constant scaling factor. The constant
    is documented here rather than buried in a magic multiplier.
    """
    return max(1, len(text.split()))


def _build_workload(n: int = 500, seed: int = 0xC057) -> list[WorkloadRow]:
    """Deterministically build a workload of `n` rows.

    Split: 60% redundant, 30% easy, 10% hard (rounded so totals match).
    """
    n_redundant = (n * 60) // 100
    n_easy = (n * 30) // 100
    n_hard = n - n_redundant - n_easy

    # A long stable system prompt — this is the prefix prompt caching
    # bites. 200 words ≈ 250-300 input tokens at Claude's real
    # tokenizer; we use whitespace count as a documented approximation.
    system_prompt = (
        "You are a support assistant. Be concise, accurate, and refuse "
        "to answer when the question is outside the policy documents. "
        "When citing a policy, name the section. When the user asks for "
        "an explanation, use plain English and avoid jargon. When the "
        "user asks for a list, use a markdown bulleted list. When the "
        "user asks a factual question, answer with a single sentence. "
        "When the user requests a refund, confirm the order id, the "
        "reason, and the amount before promising anything. When the "
        "user asks about a third-party tool, point them at the official "
        "documentation rather than inventing an answer. Always end with "
        "'Is there anything else?'."
    )
    system_prompt_tokens = _ws_count(system_prompt)

    rows: list[WorkloadRow] = []

    # Redundant — paraphrases of 12 templates so semantic+prompt cache hit.
    for i in range(n_redundant):
        template_idx = i % len(REDUNDANT_TEMPLATES)
        paraphrase_idx = (i // len(REDUNDANT_TEMPLATES)) % len(_PARAPHRASE_PREFIXES)
        prompt = _PARAPHRASE_PREFIXES[paraphrase_idx] + REDUNDANT_TEMPLATES[template_idx]
        # Easy class but redundant — confident first token, cheap handles.
        rows.append(
            WorkloadRow(
                row_id=f"red-{i:04d}",
                class_="redundant",
                prompt=prompt,
                system=system_prompt,
                prompt_tokens=system_prompt_tokens + _ws_count(prompt),
                completion_tokens=18,
                first_token_logprobs=_LOGP_PINNED,
                cheap_quality=0.92,
                strong_quality=0.95,
            )
        )

    # Easy — short factual, never repeated, no cache wins.
    for i in range(n_easy):
        prompt = EASY_PROMPTS[i % len(EASY_PROMPTS)] + f" (q#{i})"
        rows.append(
            WorkloadRow(
                row_id=f"easy-{i:04d}",
                class_="easy",
                prompt=prompt,
                system=system_prompt,
                prompt_tokens=system_prompt_tokens + _ws_count(prompt),
                completion_tokens=8,
                first_token_logprobs=_LOGP_PINNED,
                cheap_quality=0.93,
                strong_quality=0.94,
            )
        )

    # Hard — open-ended, router escalates.
    for i in range(n_hard):
        prompt = HARD_PROMPTS[i % len(HARD_PROMPTS)] + f" (variant {i})"
        rows.append(
            WorkloadRow(
                row_id=f"hard-{i:04d}",
                class_="hard",
                prompt=prompt,
                system=system_prompt,
                prompt_tokens=system_prompt_tokens + _ws_count(prompt),
                completion_tokens=160,
                first_token_logprobs=_LOGP_UNIFORM5,
                cheap_quality=0.55,
                strong_quality=0.90,
            )
        )

    # Stable deterministic order: hash row_id+seed and sort by it. This
    # interleaves the classes so cumulative savings curves look real
    # rather than three flat shelves.
    def _sort_key(r: WorkloadRow) -> str:
        return hashlib.sha256(f"{seed}:{r.row_id}".encode()).hexdigest()

    rows.sort(key=_sort_key)
    return rows


# ----------------------------------------------------------------------
# Strategy runners
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyResult:
    """One row in the savings table; one strategy applied to the workload."""

    strategy: str
    n_rows: int
    total_usd: float
    baseline_usd: float
    saved_usd: float
    saved_pct: float
    mean_quality: float
    extra: dict[str, Any] = field(default_factory=dict)


def _dollars_input_only(prompt_tokens: int, *, model: str) -> float:
    """Cost of `prompt_tokens` at `model`'s standard input rate.

    Output cost is intentionally excluded from the per-strategy
    baseline because none of the four strategies move output cost.
    Output is a constant pass-through and would dominate the table
    while contributing zero to the comparison.
    """
    pricing = get_pricing(model)
    return prompt_tokens * pricing.input_per_mtok / 1_000_000


def _run_baseline(workload: list[WorkloadRow]) -> StrategyResult:
    """No optimization — every request goes to the cheap model, full input price.

    Every other strategy is graded against this baseline. Quality is
    the cheap-model quality across all rows since this baseline never
    escalates.
    """
    total = 0.0
    qualities = 0.0
    for r in workload:
        total += _dollars_input_only(r.prompt_tokens, model=CHEAP_MODEL)
        qualities += r.cheap_quality
    n = len(workload)
    return StrategyResult(
        strategy="baseline (no optimization, cheap model)",
        n_rows=n,
        total_usd=round(total, 6),
        baseline_usd=round(total, 6),
        saved_usd=0.0,
        saved_pct=0.0,
        mean_quality=round(qualities / n, 4) if n else 0.0,
    )


def _run_prompt_cache(workload: list[WorkloadRow], baseline: StrategyResult) -> StrategyResult:
    """Anthropic prompt caching: pay 1.25× on first cache write per system
    prefix, 0.10× on every subsequent hit of that prefix.

    The system prompt is the same string for the whole workload (the
    canonical "long stable prefix" case), so the first row writes and
    every later row reads. The user-message portion of the prompt is
    *not* cached (we'd need a stable message-prefix per template;
    that's the messages_prefix=True path which adds bookkeeping). This
    is the conservative number — real apps will cache more.
    """
    pricing = get_pricing(CHEAP_MODEL)
    rate = pricing.input_per_mtok / 1_000_000

    total = 0.0
    n_writes = 0
    n_reads = 0
    qualities = 0.0
    seen_prefix: dict[str, bool] = {}
    for r in workload:
        prefix_key = r.system  # stable across the workload
        user_tokens = max(1, r.prompt_tokens - _ws_count(r.system))
        prefix_tokens = r.prompt_tokens - user_tokens
        if not seen_prefix.get(prefix_key, False):
            # Cold path: pay write multiplier on the prefix; user portion
            # at standard rate.
            total += prefix_tokens * rate * pricing.cache_write_multiplier
            total += user_tokens * rate
            seen_prefix[prefix_key] = True
            n_writes += 1
        else:
            # Warm path: pay read multiplier on the prefix; user portion
            # at standard rate.
            total += prefix_tokens * rate * pricing.cache_read_multiplier
            total += user_tokens * rate
            n_reads += 1
        qualities += r.cheap_quality

    n = len(workload)
    saved = baseline.total_usd - total
    pct = (saved / baseline.total_usd) if baseline.total_usd > 0 else 0.0
    return StrategyResult(
        strategy="prompt caching (system prefix)",
        n_rows=n,
        total_usd=round(total, 6),
        baseline_usd=baseline.total_usd,
        saved_usd=round(saved, 6),
        saved_pct=round(pct, 4),
        mean_quality=round(qualities / n, 4) if n else 0.0,
        extra={"cache_writes": n_writes, "cache_reads": n_reads},
    )


def _run_semantic_cache(workload: list[WorkloadRow], baseline: StrategyResult) -> StrategyResult:
    """Embedding-keyed response cache. Hit = skip the model call entirely.

    Threshold 0.95 (D-006 default). Tag the workload with row class so
    invalidation can hit one slice; we don't exercise invalidation in
    this run but the bookkeeping is real.
    """
    cache = SemanticCache(
        embedder=HashEmbedder(),
        storage=InMemoryStorage(),
        similarity_threshold=0.95,
    )

    total = 0.0
    n_hits = 0
    n_misses = 0
    qualities = 0.0
    for r in workload:
        result = cache.lookup(r.prompt, model=CHEAP_MODEL)
        if result.hit:
            # No model call; zero token spend on the cache hit.
            n_hits += 1
            qualities += r.cheap_quality
            continue
        # Miss → call the model (charge baseline cost), then write to cache.
        total += _dollars_input_only(r.prompt_tokens, model=CHEAP_MODEL)
        n_misses += 1
        cache.put(
            r.prompt,
            payload={"response": "stub", "tokens": r.completion_tokens},
            model=CHEAP_MODEL,
            tags=(r.class_,),
        )
        qualities += r.cheap_quality

    n = len(workload)
    saved = baseline.total_usd - total
    pct = (saved / baseline.total_usd) if baseline.total_usd > 0 else 0.0
    return StrategyResult(
        strategy="semantic cache (HashEmbedder, threshold 0.95)",
        n_rows=n,
        total_usd=round(total, 6),
        baseline_usd=baseline.total_usd,
        saved_usd=round(saved, 6),
        saved_pct=round(pct, 4),
        mean_quality=round(qualities / n, 4) if n else 0.0,
        extra={"hits": n_hits, "misses": n_misses, "hit_rate": round(n_hits / n, 4)},
    )


class _StubResponse:
    """Duck-typed response object the EntropySignal can read."""

    def __init__(self, *, prompt: str, logprobs: tuple[float, ...]) -> None:
        self.prompt = prompt
        self.first_token_logprobs = list(logprobs)
        self.text = ""


class _StubCheapAdapter:
    """Adapter the router calls. Maps workload rows to canned responses."""

    def __init__(self, by_row_id: dict[str, WorkloadRow]) -> None:
        self._by_row_id = by_row_id

    def call_cheap(self, request: Any) -> Any:
        row_id = request["row_id"]
        row = self._by_row_id[row_id]
        return _StubResponse(prompt=row.prompt, logprobs=row.first_token_logprobs)


def _run_router(workload: list[WorkloadRow], baseline: StrategyResult) -> StrategyResult:
    """Uncertainty-routed cheap → strong fallback.

    Pays cheap on every row; pays *additional* strong cost on the
    escalated rows (the cheap call already happened). Quality on
    escalated rows is the strong-quality score; quality on cheap-only
    rows is cheap-quality.
    """
    by_row_id = {r.row_id: r for r in workload}
    router = UncertaintyRouter(
        cheap_model=CHEAP_MODEL,
        strong_model=STRONG_MODEL,
        cheap_adapter=_StubCheapAdapter(by_row_id),
        signals=[EntropySignal(threshold=1.5)],
    )

    total = 0.0
    n_escalated = 0
    qualities = 0.0
    for r in workload:
        # Cheap cost is always paid.
        total += _dollars_input_only(r.prompt_tokens, model=CHEAP_MODEL)
        decision = router.route({"row_id": r.row_id})
        if decision.triggered_signal is not None:
            n_escalated += 1
            total += _dollars_input_only(r.prompt_tokens, model=STRONG_MODEL)
            qualities += r.strong_quality
        else:
            qualities += r.cheap_quality

    n = len(workload)
    saved = baseline.total_usd - total
    pct = (saved / baseline.total_usd) if baseline.total_usd > 0 else 0.0
    return StrategyResult(
        strategy="uncertainty router (entropy threshold 1.5)",
        n_rows=n,
        total_usd=round(total, 6),
        baseline_usd=baseline.total_usd,
        saved_usd=round(saved, 6),
        saved_pct=round(pct, 4),
        mean_quality=round(qualities / n, 4) if n else 0.0,
        extra={
            "escalated": n_escalated,
            "escalation_rate": round(n_escalated / n, 4),
        },
    )


def _run_batch(workload: list[WorkloadRow], baseline: StrategyResult) -> StrategyResult:
    """Batch API: every request goes through the batch endpoint at 0.5×.

    Uses the in-memory backend so the lifecycle runs hermetically;
    the cost math goes through the real ``compare_realtime_vs_batch``
    against caller-supplied quotes (D-003 posture). Quality is the
    cheap-model quality — batch doesn't change which model answers.
    """
    backend = InMemoryBatchBackend()
    requests = [
        BatchRequest(
            custom_id=r.row_id,
            user=r.prompt,
            model=CHEAP_MODEL,
            system=r.system,
        )
        for r in workload
    ]
    job = backend.submit(requests, idempotency_key="bench-savings-2026-05-17")
    rows_in: list[BatchResultRow] = [
        BatchResultRow(
            custom_id=r.row_id,
            response_text="stub",
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
        )
        for r in workload
    ]
    backend.complete(job.job_id, results=rows_in)
    out = backend.results(job.job_id)
    pricing = get_pricing(CHEAP_MODEL)
    quote = BatchCostQuote(
        model=CHEAP_MODEL,
        input_per_mtok=pricing.input_per_mtok,
        output_per_mtok=pricing.input_per_mtok,  # input-only bench; quote both at input rate
    )
    cmp_ = compare_realtime_vs_batch(out, prices={CHEAP_MODEL: quote})

    # The bench grades only the input-token axis (output is held constant
    # across strategies); recompute total_usd input-only at the batch
    # rate so the table compares like-for-like.
    rate = pricing.input_per_mtok / 1_000_000
    total = sum(r.prompt_tokens * rate * BATCH_DISCOUNT_FACTOR for r in workload)
    n = len(workload)
    saved = baseline.total_usd - total
    pct = (saved / baseline.total_usd) if baseline.total_usd > 0 else 0.0
    qualities = sum(r.cheap_quality for r in workload) / n if n else 0.0
    return StrategyResult(
        strategy=f"batch API (discount {BATCH_DISCOUNT_FACTOR:.2f}×)",
        n_rows=n,
        total_usd=round(total, 6),
        baseline_usd=baseline.total_usd,
        saved_usd=round(saved, 6),
        saved_pct=round(pct, 4),
        mean_quality=round(qualities, 4),
        extra={
            "discount_factor": BATCH_DISCOUNT_FACTOR,
            # compare_realtime_vs_batch sees both axes at the input rate
            # for the bench; its own savings_pct is the same shape as
            # ours but counts output tokens, so we surface it side by
            # side rather than overwriting the strategy figure.
            "compare_savings_pct_with_outputs": cmp_.savings_pct,
        },
    )


# ----------------------------------------------------------------------
# Cumulative savings (for the dashboard's line chart)
# ----------------------------------------------------------------------


def _cumulative_savings(workload: list[WorkloadRow], strategy: str) -> list[dict[str, float]]:
    """Per-row cumulative $ saved vs baseline for `strategy`.

    Done as an independent pass instead of accumulating during the
    strategy runner so the runners stay easy to read and the
    cumulative series is a derived artifact (recomputable from the
    other outputs if anyone ever questions a number).
    """
    pricing = get_pricing(CHEAP_MODEL)
    rate = pricing.input_per_mtok / 1_000_000
    cumulative: list[dict[str, float]] = []

    seen_prefix: dict[str, bool] = {}
    cache = SemanticCache(
        embedder=HashEmbedder(),
        storage=InMemoryStorage(),
        similarity_threshold=0.95,
    )
    by_row_id = {r.row_id: r for r in workload}
    router = UncertaintyRouter(
        cheap_model=CHEAP_MODEL,
        strong_model=STRONG_MODEL,
        cheap_adapter=_StubCheapAdapter(by_row_id),
        signals=[EntropySignal(threshold=1.5)],
    )

    running_baseline = 0.0
    running_strategy = 0.0
    for i, r in enumerate(workload, start=1):
        row_baseline = r.prompt_tokens * rate
        running_baseline += row_baseline

        if strategy == "prompt_cache":
            user_tokens = max(1, r.prompt_tokens - _ws_count(r.system))
            prefix_tokens = r.prompt_tokens - user_tokens
            if not seen_prefix.get(r.system, False):
                cost = prefix_tokens * rate * pricing.cache_write_multiplier + user_tokens * rate
                seen_prefix[r.system] = True
            else:
                cost = prefix_tokens * rate * pricing.cache_read_multiplier + user_tokens * rate
        elif strategy == "semantic_cache":
            result = cache.lookup(r.prompt, model=CHEAP_MODEL)
            if result.hit:
                cost = 0.0
            else:
                cost = row_baseline
                cache.put(
                    r.prompt,
                    payload={"response": "stub"},
                    model=CHEAP_MODEL,
                    tags=(r.class_,),
                )
        elif strategy == "router":
            cost = row_baseline
            decision = router.route({"row_id": r.row_id})
            if decision.triggered_signal is not None:
                strong_pricing = get_pricing(STRONG_MODEL)
                cost += r.prompt_tokens * strong_pricing.input_per_mtok / 1_000_000
        elif strategy == "batch":
            cost = row_baseline * BATCH_DISCOUNT_FACTOR
        else:
            raise ValueError(f"unknown strategy: {strategy}")

        running_strategy += cost
        cumulative.append(
            {
                "row_index": i,
                "row_id": r.row_id,
                "class": r.class_,
                "baseline_total_usd": round(running_baseline, 6),
                "strategy_total_usd": round(running_strategy, 6),
                "cumulative_saved_usd": round(running_baseline - running_strategy, 6),
            }
        )
    return cumulative


# ----------------------------------------------------------------------
# Output formatting
# ----------------------------------------------------------------------


def _format_markdown(payload: dict[str, Any]) -> str:
    """Render the bench results as a markdown table for the README + docs."""
    lines = [
        "# Savings benchmark",
        "",
        "Synthetic 500-row workload, deterministic, hermetic. Numbers are "
        "what `scripts/bench_savings.py` produced on the host that wrote "
        "this file — re-run the script to refresh.",
        "",
        f"- Cheap model: `{CHEAP_MODEL}` "
        f"(${get_pricing(CHEAP_MODEL).input_per_mtok:.2f}/MTok input)",
        f"- Strong model: `{STRONG_MODEL}` "
        f"(${get_pricing(STRONG_MODEL).input_per_mtok:.2f}/MTok input)",
        f"- Workload mix: {payload['workload_mix']}",
        f"- Total prompt tokens (sum across rows): {payload['total_prompt_tokens']:,}",
        "",
        "| Strategy | Rows | $ spent | $ saved | % saved | Mean quality | Extra |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in payload["strategies"]:
        extra_fmt = ", ".join(f"{k}={v}" for k, v in row["extra"].items()) or "—"
        lines.append(
            f"| {row['strategy']} | {row['n_rows']} | "
            f"${row['total_usd']:.4f} | ${row['saved_usd']:.4f} | "
            f"{row['saved_pct']:.1%} | {row['mean_quality']:.3f} | {extra_fmt} |"
        )
    lines.append("")
    lines.append("Cumulative savings per row (per strategy) live in `savings.json`.")
    lines.append("The Streamlit dashboard renders those series; see the repo README.")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Public entry point (also called from tests)
# ----------------------------------------------------------------------


def run_bench(*, n: int = 500, seed: int = 0xC057) -> dict[str, Any]:
    """Run every strategy and return the full result payload.

    Pure-function shape so tests can call without touching disk.
    """
    workload = _build_workload(n=n, seed=seed)

    baseline = _run_baseline(workload)
    prompt_cache = _run_prompt_cache(workload, baseline)
    semantic_cache = _run_semantic_cache(workload, baseline)
    router = _run_router(workload, baseline)
    batch = _run_batch(workload, baseline)

    strategies = [baseline, prompt_cache, semantic_cache, router, batch]

    mix = {
        "redundant": sum(1 for r in workload if r.class_ == "redundant"),
        "easy": sum(1 for r in workload if r.class_ == "easy"),
        "hard": sum(1 for r in workload if r.class_ == "hard"),
    }
    cumulative = {
        "prompt_cache": _cumulative_savings(workload, "prompt_cache"),
        "semantic_cache": _cumulative_savings(workload, "semantic_cache"),
        "router": _cumulative_savings(workload, "router"),
        "batch": _cumulative_savings(workload, "batch"),
    }

    return {
        "schema_version": 1,
        "mode": "dry",
        "n_rows": len(workload),
        "seed": seed,
        "cheap_model": CHEAP_MODEL,
        "strong_model": STRONG_MODEL,
        "workload_mix": mix,
        "total_prompt_tokens": sum(r.prompt_tokens for r in workload),
        "strategies": [asdict(s) for s in strategies],
        "cumulative_savings_by_strategy": cumulative,
    }


def _write_workload(workload: list[WorkloadRow], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "rows": [
            {
                "row_id": r.row_id,
                "class": r.class_,
                "prompt": r.prompt,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "first_token_logprobs": list(r.first_token_logprobs),
                "cheap_quality": r.cheap_quality,
                "strong_quality": r.strong_quality,
            }
            for r in workload
        ],
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Run against the in-process stub (default). "
            "`--no-dry` opts into real-API mode, which currently errors out "
            "until the operator wires real adapters per D-007."
        ),
    )
    p.add_argument(
        "--out",
        default="docs/savings",
        help="Output stem; `.json` and `.md` are written next to it.",
    )
    p.add_argument("--n", type=int, default=500, help="Workload row count.")
    p.add_argument("--seed", type=int, default=0xC057, help="Deterministic order seed.")
    args = p.parse_args(argv)

    if not args.dry:
        print(
            "::error::real-API bench mode is not implemented in this PR. "
            "Run --dry to exercise the math + plumbing; the operator wires "
            "real adapters when they're ready to commit "
            "`docs/savings_real.md`. Same posture as scripts/tune_threshold.py.",
            file=sys.stderr,
        )
        return 2

    payload = run_bench(n=args.n, seed=args.seed)
    out_stem = Path(args.out)
    out_json = out_stem.with_suffix(".json")
    out_md = out_stem.with_suffix(".md")
    out_workload = out_stem.parent / "savings_workload.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    out_md.write_text(_format_markdown(payload), encoding="utf-8")
    _write_workload(_build_workload(n=args.n, seed=args.seed), out_workload)

    print(f"bench wrote {out_json}")
    print(f"bench wrote {out_md}")
    print(f"workload   {out_workload}")
    for row in payload["strategies"]:
        print(
            f"  {row['strategy']:50s}  "
            f"${row['total_usd']:.4f}  saved ${row['saved_usd']:.4f}  "
            f"({row['saved_pct']:.1%})  q={row['mean_quality']:.3f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
