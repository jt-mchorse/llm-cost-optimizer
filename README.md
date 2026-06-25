# llm-cost-optimizer
> Production cost-reduction toolkit for LLM workloads: prompt caching, semantic cache, uncertainty-routed model fallback, batch API, and a savings dashboard.

![CI](https://github.com/jt-mchorse/llm-cost-optimizer/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

## What this is

LLM bills compound. A serious production app spends most of its tokens re-sending the same context — system prompts, tool definitions, long policy documents — to a stateless API on every call. Anthropic's prompt caching feature lets you mark a prefix as cacheable and pay a 90%-discounted read rate for subsequent calls that share that prefix. The savings are real, but using the feature correctly means juggling `cache_control` placement, reading the `cache_creation_input_tokens` / `cache_read_input_tokens` fields off every response, and converting those into something you can put in a cost dashboard.

`llm-cost-optimizer` is a small toolkit that does that work for you. The runtime entry point is `PromptCacheWrapper` (#1): a duck-typed wrapper around the Anthropic SDK's `messages.create` that injects `cache_control: {"type": "ephemeral"}` on caller-chosen segments (system, tools, message prefix), reads the cache-usage fields off the response, and rolls them into a `CacheTelemetry` struct (`hits`, `misses`, `tokens_cached`, `tokens_written`, `dollars_saved`) — per call and aggregated across the wrapper's lifetime. Pricing is a small in-repo table per model so the `dollars_saved` number is always traceable to a documented rate rather than fabricated.

The wrapper is intentionally dependency-free: the Anthropic SDK is never imported, only duck-typed against `client.messages.create(...)`. That keeps the package importable without an API key, hermetically testable in CI, and embeddable inside other portfolio repos (notably `rag-production-kit` and `agent-orchestration-platform`) without forcing them to take an SDK dep.

Today the five runtime layers and one offline sibling have all shipped:

- **Prompt-cache wrapper** (#1) — the duck-typed Anthropic-SDK wrapper above; `CacheTelemetry` per call and aggregate. `CacheTelemetry.to_dict()` and `PromptCacheWrapper.dump_aggregate_json(path)` (#50) emit a JSON-stable observability shape — atomic-written so a log-tailer never reads a half-written file.
- **Semantic response cache** (#2) — `cost_optimizer.semantic_cache` keys on an embedding of the user prompt, caches the full response, and exposes TTL plus exact-prompt invalidation. Pluggable `Embedder` Protocol (default in-repo hash embedder; swap in a real one). `CacheStats.to_dict()` and `SemanticCache.dump_stats_json(path)` (#52) emit the same JSON-stable observability shape as the prompt-cache wrapper (raw counters + `total_lookups` + `hit_rate`) — atomic-written so a log-tailer never reads a half-written file.
- **Uncertainty-routed model fallback** (#3) — `cost_optimizer.router` first-passes the cheap model and escalates to the strong model only when a confidence signal (logprob entropy or judge score) clears a threshold. The threshold curve is produced by `scripts/tune_threshold.py` against an operator-supplied dataset.
- **Anthropic Batch API integration** (#4) — `cost_optimizer.batch` wraps the non-realtime batch endpoint with an idempotency key derived from request content, exposes a polling-friendly `BatchJobMeta`, and reports both the realtime-equivalent cost and the actual batch cost so the savings number is directly comparable.
- **Savings dashboard** (#5) — `streamlit run dashboard/app.py` renders the five-strategy savings bench against a realistic mixed workload. Strategy summaries and cumulative series live in `docs/savings.json`; the dashboard reads them directly so the same data backs the README table, the markdown report, and the live UI.
- **Live-API integration test** (#7) — `tests/integration/` exercises `PromptCacheWrapper` against real Anthropic prompt caching (cold call writes tokens, warm call reads them), gated on `ANTHROPIC_API_KEY` and a `LIVE_CACHE_BUDGET_USD` guardrail (default $0.10). Runs in CI only on `workflow_dispatch`.

Each layer is adoptable on its own; the architecture diagram below shows the seams.

## Architecture

Five layers ship today plus a live-API integration posture. Each layer is adoptable on its own — semantic cache → uncertainty router → prompt-cache wrapper at runtime, batch-API as the offline sibling, and the savings dashboard reading bench artifacts that are produced from the same pricing table the runtime layers use. The full integrated diagram, per-layer flows, and the design decisions behind each one (D-002…D-012) live in **[docs/architecture.md](docs/architecture.md)**.

## Quickstart

Install for development:

```bash
git clone https://github.com/jt-mchorse/llm-cost-optimizer.git
cd llm-cost-optimizer
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

The default `pytest` invocation runs the full hermetic unit suite (a few seconds to a few tens of seconds, no API key) and intentionally excludes the **live-API integration** suite under `tests/integration/`. That suite exercises `PromptCacheWrapper` against real Anthropic prompt caching — cold call writes tokens, warm call reads them — and is gated on `ANTHROPIC_API_KEY` plus a `LIVE_CACHE_BUDGET_USD` guardrail (default `$0.10`). It runs in CI only on a manual `workflow_dispatch` against the `integration` workflow, never on push or PR. To run locally:

```bash
ANTHROPIC_API_KEY=sk-... pytest tests/integration -v
```

Use the wrapper against a real Anthropic client:

```python
from anthropic import Anthropic
from cost_optimizer import PromptCacheWrapper

client = Anthropic()
wrapped = PromptCacheWrapper(client, model="claude-haiku-4-5")

result = wrapped.create(
    system="<long stable system prompt here>",
    messages=[{"role": "user", "content": "what's the policy on refunds?"}],
    max_tokens=512,
)

print(result.response.content)          # the underlying SDK response
print(result.telemetry)                 # per-call cache stats
print(wrapped.aggregate.dollars_saved)  # cumulative across all calls
```

By default the system prompt is marked as cacheable. To cache other prefix segments:

```python
wrapped = PromptCacheWrapper(
    client,
    model="claude-sonnet-4-6",
    cache_segments=("system", "tools", "messages_prefix"),
)
```

If you're testing locally without an API key, the wrapper works against any object exposing `client.messages.create(...)` — see `tests/test_cache_wrapper.py` for the fake client used by the test suite.

## Semantic cache (#2)

The second layer is a **semantic response cache** — keyed by embedding
similarity, not exact-match on the prompt string. Two paraphrased
prompts hit the same entry; a new model call only happens when the
incoming request really is novel.

Two pluggable Protocols (D-004), parallel to the patterns in
`rag-production-kit` and `llm-eval-harness`:

- **`Embedder`** — `embed(text) -> list[float]`. Ships with `HashEmbedder`
  (dep-free, deterministic, hermetic for tests). Production callers BYO
  via the Protocol — Cohere, Voyage, OpenAI, sentence-transformers all
  conform with a one-line wrapper.
- **`Storage`** — `put`/`find_nearest`/`invalidate_by_tag`/`purge_expired`.
  Ships with `InMemoryStorage` (dep-free) and `RedisStorage` (lazy-imports
  the `redis` SDK behind the new `[redis]` extra). RedisStorage uses
  Redis SETs for tag-membership and native Redis TTL for expiry.

```python
from cost_optimizer import HashEmbedder, InMemoryStorage, SemanticCache

cache = SemanticCache(
    embedder=HashEmbedder(),
    storage=InMemoryStorage(),
    similarity_threshold=0.95,    # high on purpose (D-006)
    default_ttl_s=3600,           # 1h default; per-call override available
)

result = cache.lookup("how do I refund a charge", model="claude-haiku-4-5")
if result.hit:
    print("cache hit, sim =", result.similarity)
else:
    response = client.messages.create(...)  # call the model
    cache.put("how do I refund a charge", response, model="claude-haiku-4-5",
              tags=("policy",))
```

Tag-based invalidation: `cache.invalidate(tag="policy")` drops every
entry tagged `policy` (e.g., when the underlying policy doc changes).

**False-positive measurement** is offline by design (D-007):
`measure_false_positive_rate(cache, held_out, model=..., call_model=...)`
samples cache hits on a held-out set, calls the real model on each, and
reports the rate at which the cached response disagreed with the model's
actual response. Online sampling would slowly bleed the cost savings the
cache exists to deliver, so the helper is run by the operator, not on
every request.

The 1000-row hit-rate benchmark + measured false-positive rate are
**deferred to issue #5** (savings dashboard) since they need a real
embedder + a real workload to be honest measurements; running them with
the `HashEmbedder` would produce numbers that don't generalize to
production. The cache infrastructure is shipped here; the dashboard
plots the numbers when #5 lands.

## Model routing (#3)

The third layer is **uncertainty-routed model fallback**: the cheap
model handles every request; the router escalates to the strong model
when an `EscalationSignal` trips on the cheap response.

Two signals ship today. **`EntropySignal`** computes Shannon entropy
over the cheap model's first-token logprobs and escalates when entropy
crosses a threshold (high entropy ≈ "cheap model isn't confident
between several token continuations"). **`JudgeConfidenceSignal`**
runs an `eval_harness.Judge`-shaped object on the cheap output and
escalates when the score is below threshold — directly reusing the
judge layer from `llm-eval-harness` rather than re-implementing a
quality signal.

```python
from cost_optimizer import EntropySignal, JudgeConfidenceSignal, UncertaintyRouter
from eval_harness import Judge, AnthropicBackend  # cross-repo import

router = UncertaintyRouter(
    cheap_model="claude-haiku-4-5-20251001",
    strong_model="claude-opus-4-7",
    cheap_adapter=YourCheapAdapter(),   # .call_cheap(request) → response
    signals=[
        EntropySignal(threshold=1.5),
        JudgeConfidenceSignal(
            judge=Judge(backend=AnthropicBackend()),
            rubric="faithfulness",
            threshold=0.7,
        ),
    ],
)
decision = router.route({"prompt": "..."})
print(decision.model_id, decision.triggered_signal, decision.signal_values)
```

Signals are evaluated in the order configured; the first signal that
trips wins, but **every** signal is measured so the resulting
`RouterDecision.signal_values` is a complete telemetry record for the
savings-dashboard work in #5 (D-009).

Tune the entropy threshold against your workload:

```bash
# Dry run: against committed sample fixtures, no API needed.
python scripts/tune_threshold.py --out docs/threshold_demo
# → docs/threshold_demo.json with one row per threshold (escalation_rate,
#   mean_quality_overall, dollars_per_request, …)
# → docs/threshold_demo.png if matplotlib is installed
```

**Per the no-fabricated-benchmarks rule**, this README *does not*
claim that "quality at 80/20 ≥ quality at 100% strong" — the
*script* that proves that claim ships here, but the verified curve
lands in `docs/threshold_report.md` only when the operator runs the
script against a real API and a real dataset.

## Batch API integration (#4)

Anthropic's Messages Batch API charges 50% of standard input/output
rates and runs eligible non-realtime workloads asynchronously. The
`cost_optimizer.batch` layer wraps the submit / poll / results
lifecycle behind a `BatchBackend` Protocol — same seam shape as the
rest of the toolkit (D-002) — so callers can swap a deterministic
in-memory backend for hermetic CI in for the production
Anthropic-backed binding without touching call sites.

```python
from cost_optimizer import (
    AnthropicBatchBackend, BatchRequest,
    BatchCostQuote, compare_realtime_vs_batch,
)

backend = AnthropicBatchBackend(client)   # duck-typed; any `.messages.batches`
requests = [
    BatchRequest(custom_id=f"row-{i}", user=prompt, model="claude-opus-4-7")
    for i, prompt in enumerate(prompts)
]
job = backend.submit(requests, idempotency_key=f"shard-{shard_id}-{date}")

import time
while job.status not in {"ended_succeeded", "ended_failed", "ended_canceled"}:
    time.sleep(30)
    job = backend.poll(job.job_id)

rows = backend.results(job.job_id)
quote = BatchCostQuote("claude-opus-4-7", input_per_mtok=5.0, output_per_mtok=25.0)
cmp_ = compare_realtime_vs_batch(rows, prices={"claude-opus-4-7": quote})
print(f"realtime ${cmp_.realtime_usd:.2f} → batch ${cmp_.batch_usd:.2f} "
      f"({cmp_.savings_pct:.0%} savings on {cmp_.n_rows} rows)")
```

**Idempotency (D-010).** Same payload + same key → returns the existing
`job_id`. Different payload + same key → `IdempotencyConflict` — the
failure mode that would otherwise silently double-charge. The payload
hash is content-only (request count, custom_ids, prompts, model,
max_tokens, system).

**Cost comparison.** Prices are caller-supplied — no list-price defaults
ship, matching D-003. `BATCH_DISCOUNT_FACTOR = 0.5` is the documented
Anthropic batch discount; override per call if your contract differs.
The comparison skips failed rows (they aren't billed on either path)
and supports multi-model batches via `model_of={custom_id → model}`.

For local development and tests, use `InMemoryBatchBackend` — same
protocol, dedupes on idempotency key, exposes `advance(job_id)` and
`complete(job_id, results=…)` test helpers, zero dependencies.

## Savings dashboard (#5)

The dashboard ties the four cost layers together: every shipped
strategy runs against the *same* synthetic workload, and the resulting
savings table + cumulative-savings series is what the operator opens
to compare options.

The workload is **hermetic synthetic** (D-012): 500 rows, deterministic,
60% redundant template paraphrases / 30% easy factual / 10% hard
open-ended. All token counts and first-token logprobs are canned in
the committed `docs/savings_workload.json` so the run is bit-for-bit
reproducible; the pricing math is the real `cost_optimizer.pricing`
table (`claude-haiku-4-5` @ $1/MTok input, `claude-opus-4-7` @ $5/MTok
input) and the real `BATCH_DISCOUNT_FACTOR`. No fabricated numbers.

```bash
# Refresh the savings artifacts (writes docs/savings.{json,md} +
# docs/savings_workload.json). Hermetic; no API key needed.
python scripts/bench_savings.py --dry --out docs/savings

# Open the dashboard (Streamlit; install the optional extra first).
pip install -e '.[dashboard]'
streamlit run dashboard/app.py -- --json docs/savings.json
```

Measured on the host that produced this file (500-row workload,
input-token economics only — output is held constant across
strategies so each row of the table is like-for-like):

| Strategy | Rows | $ spent | $ saved | % saved | Mean quality | Extra |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| baseline (no optimization, cheap model) | 500 | $0.0577 | $0.0000 | 0.0% | 0.886 | — |
| prompt caching (system prefix) | 500 | $0.0092 | $0.0485 | 84.0% | 0.886 | 1 write + 499 reads |
| semantic cache (threshold 0.95) | 500 | $0.0253 | $0.0324 | 56.2% | 0.886 | 280 hits / 220 misses |
| uncertainty router (entropy 1.5) | 500 | $0.0874 | $-0.0297 | -51.6% | 0.921 | 50 escalated (10%) |
| batch API (discount 0.50×) | 500 | $0.0288 | $0.0288 | 50.0% | 0.886 | — |

A few honest notes the README leads with rather than buries:

- **Prompt caching is the cheapest line item by far** because the workload
  shares a stable system prompt across every row. Real apps that fan out
  to many distinct system prefixes will see smaller wins on this layer.
- **The uncertainty router shows a *negative* dollar saving** against the
  cheap-on-everything baseline — that's the design. The router *spends
  more* to buy higher quality on hard rows (mean quality 0.886 → 0.921).
  The right way to read the row is "+3.5pp quality at +51.6% spend on
  this workload's 10% hard slice"; the right pairing is router + a
  cache layer so the cache offsets the strong-model spend.
- The Streamlit dashboard renders the bar chart of per-strategy
  savings, the cumulative-savings line, a quality-maintained flag,
  and an expandable JSON view of the raw artifact.

Real-API savings against a real workload follows the same posture as
`scripts/tune_threshold.py` (D-007): the script supports a `--dry` mode
and an explicit "real-API mode is not implemented" branch; the operator
wires real adapters and commits `docs/savings_real.md` once vetted.

## Benchmarks / Results

See [docs/savings.md](docs/savings.md) for the table above, refreshed
by re-running `scripts/bench_savings.py`. Cumulative per-row savings
live in `docs/savings.json` and are rendered by the Streamlit
dashboard.

The pricing math is unit-tested against the published Anthropic
multipliers (`tests/test_cache_wrapper.py`); the router's tuning
curve is produced by `scripts/tune_threshold.py` against an
operator-supplied dataset and API key; the batch layer's cost math is
unit-tested against fixture prices in `tests/test_batch.py`; the
five-strategy savings bench is reconciled against both the strategy
summaries and the cumulative series in `tests/test_bench_savings.py`.

## Demo

Today's hermetic demo is two commands on a fresh clone, both runnable
without an API key:

```bash
# Reproduce the savings table + cumulative series under each strategy.
python scripts/bench_savings.py --dry --out /tmp/savings

# Render the dashboard (reads the committed docs/savings.json).
streamlit run dashboard/app.py
```

The first writes a fresh `savings.json` and `savings.md` to `/tmp/`
(passing `--out docs/savings` regenerates the committed copies); the
second is the live dashboard the README's savings table is derived
from. A captured 60-second GIF/video walking through both is tracked
in **#18**.

## Why these decisions
See [MEMORY/core_decisions_human.md](MEMORY/core_decisions_human.md).

## License
MIT
