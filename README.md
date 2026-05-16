# llm-cost-optimizer
> Production cost-reduction toolkit for LLM workloads: prompt caching, semantic cache, uncertainty-routed model fallback, batch API, and a savings dashboard.

![CI](https://github.com/jt-mchorse/llm-cost-optimizer/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

## What this is

LLM bills compound. A serious production app spends most of its tokens re-sending the same context — system prompts, tool definitions, long policy documents — to a stateless API on every call. Anthropic's prompt caching feature lets you mark a prefix as cacheable and pay a 90%-discounted read rate for subsequent calls that share that prefix. The savings are real, but using the feature correctly means juggling `cache_control` placement, reading the `cache_creation_input_tokens` / `cache_read_input_tokens` fields off every response, and converting those into something you can put in a cost dashboard.

`llm-cost-optimizer` is a small toolkit that does that work for you. The first shipped layer is `PromptCacheWrapper`: a duck-typed wrapper around the Anthropic SDK's `messages.create` that injects `cache_control: {"type": "ephemeral"}` on caller-chosen segments (system, tools, message prefix), reads the cache-usage fields off the response, and rolls them into a `CacheTelemetry` struct (`hits`, `misses`, `tokens_cached`, `tokens_written`, `dollars_saved`) — per call and aggregated across the wrapper's lifetime. Pricing is a small in-repo table per model so the `dollars_saved` number is always traceable to a documented rate rather than fabricated.

The wrapper layer is intentionally dependency-free: the Anthropic SDK is never imported, only duck-typed against `client.messages.create(...)`. That keeps the package importable without an API key, hermetically testable in CI, and embeddable inside other portfolio repos (notably `rag-production-kit` and `agent-orchestration-platform`) without forcing them to take an SDK dep. Future layers — semantic embedding cache (#2), uncertainty-routed model fallback (#3), and a savings dashboard — will land in their own modules so each can be adopted independently.

## Architecture
*See [docs/architecture.md](docs/architecture.md). Diagram pending follow-up issue.*

## Quickstart

Install for development:

```bash
git clone https://github.com/jt-mchorse/llm-cost-optimizer.git
cd llm-cost-optimizer
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
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

## Semantic cache (#2 · this PR)

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

## Model routing (#3 · this PR)

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

## Batch API integration (#4 · this PR)

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
quote = BatchCostQuote("claude-opus-4-7", input_per_mtok=15.0, output_per_mtok=75.0)
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

## Benchmarks / Results
*Real-API savings benchmark pending — to be filed as a follow-up issue. The wrapper's `dollars_saved` math is unit-tested against the published Anthropic multipliers (`tests/test_cache_wrapper.py`). The router's tuning curve is produced by `scripts/tune_threshold.py` against an operator-supplied dataset and API key. The batch layer's cost math is unit-tested against fixture prices in `tests/test_batch.py`.*

## Demo
*60-second demo pending.*

## Why these decisions
See [MEMORY/core_decisions_human.md](MEMORY/core_decisions_human.md).

## License
MIT
