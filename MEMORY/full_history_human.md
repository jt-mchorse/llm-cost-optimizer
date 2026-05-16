# Session History (human-readable)

Chronological log of work sessions. Most recent first below the divider.

---

## 2026-05-14 — Issue #1: Anthropic prompt-caching wrapper
**Duration:** ~65 min · **Branch:** `session/2026-05-14-0952-issue-01`

- Shipped `PromptCacheWrapper`: duck-typed wrapper around `client.messages.create` that injects `cache_control: {"type": "ephemeral"}` on caller-chosen prefix segments (system, tools, messages_prefix) and surfaces a `CacheTelemetry(hits, misses, tokens_cached, tokens_written, dollars_saved)` per call plus an aggregate rollup.
- Added an in-repo `pricing.py` for the current Claude 4.x family using Anthropic's documented cache multipliers (write 1.25×, read 0.10×). Unknown models raise rather than fabricate — the savings math is always traceable to a recorded rate.
- Replaced the stub CI with real `ruff check` + `ruff format --check` + `pytest --cov` on py3.11 and py3.12. README "What this is" and "Quickstart" filled in with real content; `docs/architecture.md` gets a mermaid flow for the shipped layer. 18 tests, 96% coverage on the wrapper layer.

**Why this work, this session:** First feature on the repo's earliest open issue; the wrapper is the foundation that semantic cache (#2) and model fallback (#3) layer on top of. Selected because `llm-cost-optimizer` was the earliest in the build sequence among 11 repos that had not been touched in >36h.

**Open questions / blockers:** None. A real-API integration test (hits Anthropic to validate the wrapper against live `usage` fields) is intentionally deferred; needs an API key in CI secrets and will be filed as a `priority:low` follow-up.

**Next session:** Pick up either issue #2 (semantic cache) or #3 (model fallback) — both build on the shipped wrapper. Per the session protocol, repo selection will re-run at next session start.

## 2026-05-15 — Issue #2: Semantic response cache
**Duration:** ~70 min · **Branch:** `session/2026-05-15-1547-issue-02`

- Shipped `cost_optimizer/semantic_cache.py`: `SemanticCache` orchestrator, `Embedder` Protocol with `HashEmbedder` reference (D-004), `Storage` Protocol with `InMemoryStorage` + `RedisStorage` (lazy-imports `redis`, behind new `[redis]` extra), `cosine` math helper, `CacheStats` telemetry, `CacheLookupResult` return type, `measure_false_positive_rate()` offline helper (D-007).
- Cache keys include the model id (D-005); default similarity threshold 0.95 conservative on purpose (D-006); per-call TTL overrides default; tag-based `invalidate()`; opportunistic `purge_expired()` on every lookup (no-op for Redis since native TTL eviction).
- 35 new hermetic tests (cosine math, HashEmbedder properties, InMemoryStorage CRUD + tag/TTL, SemanticCache hit/miss boundary, model isolation, TTL expiry, tag invalidation, threshold validation, false-positive helper modes, RedisStorage parity via fakeredis). 53/53 passing.
- Added `[redis]` optional extra (`redis>=5.0`) and `fakeredis>=2.20` to dev extras so `RedisStorage` tests are exercised hermetically in CI.
- Backfilled README "Semantic cache" section pointing at the install/use flow; honestly marked the 1000-row hit-rate benchmark + false-positive-rate measurement as deferred to issue #5 (savings dashboard) since they need real embedder + real workload.

**Why this work, this session:** Semantic cache is the second largest cost-savings lever after prompt caching, and the Protocol shape locks the same single-method seam pattern adopted across the portfolio (rag-kit Reranker/Embedder, eval-harness Backend, agent-orchestration use-case). Shipping the infrastructure now lets issue #5's dashboard plot a real hit-rate / savings number without re-litigating the cache design.

**Open questions / blockers:** None. Real-embedder integration test pending operator's BYO embedder choice (Cohere/Voyage/sentence-transformers); the helper is shipped, the operator runs it once.

**Next session:** Issue #3 (uncertainty-routed model fallback) — natural sibling that consumes the same telemetry surface and routes through `PromptCacheWrapper` + `SemanticCache` for the strong-model leg.

## 2026-05-16 — Issue #3: Uncertainty-routed cheap → strong model fallback
**Duration:** ~35 min · **Branch:** `session/2026-05-16-0411-issue-3`

- Shipped `cost_optimizer/router.py`: `EscalationSignal` Protocol (D-008, same one-method shape as Tool/Reranker/Embedder/Backend), `UncertaintyRouter` with first-trip-wins semantics, `RouterDecision` dataclass carrying the chosen model id + name of the tripped signal + the full per-signal measurement table (D-009, the telemetry surface the #5 dashboard reads).
- Two signals out of the box: `EntropySignal` computes Shannon entropy in nats over the cheap model's first-token logprobs (supports both the test-fake shape and the SDK's nested `content[0].logprobs[0].top_logprobs` shape), tripping above its threshold. `JudgeConfidenceSignal` is the cross-repo seam to `llm-eval-harness` — calls `judge.score(prompt, response_text, rubric=...)` (the same API the regression runner uses) and trips below its threshold. Signals return `SignalReading(value, trip)`; a value of `None` means "couldn't measure" rather than "didn't trip", so models without logprobs don't silently skip the entropy gate.
- `scripts/tune_threshold.py` sweeps entropy thresholds against a 5-row hand-crafted dataset (chosen so each row exercises a distinct entropy regime: pinned, two-way, uniform-3, uniform-5) and writes a JSON table + optional matplotlib plot. Default `--dry` mode runs entirely against stub adapters/judges so CI exercises the plumbing without an API key. The real-API mode is explicitly documented as "operator wires real adapters when they're ready to commit `docs/threshold_report.md`" — no fabricated numbers.
- 24 new tests: 18 in `tests/test_router.py` (no-signals no-escalation, first-trip-wins, second-signal-can-still-trip, signal-returning-None doesn't trip, entropy math against known pinned/uniform/truncated distributions, entropy works against both the test-fake and SDK shapes, judge calls don't waste a score on empty text, end-to-end with real signals). 6 in `tests/test_tune_threshold.py` (sweep returns one row per threshold; escalation rate monotone-non-increasing in threshold; dollar math; main writes JSON; main returns 0 in dry mode). Suite total now 77/77 pass; ruff lint+format clean.
- README: new "Model routing (#3 · this PR)" subsection with the 6-line invocation snippet and the honest "quality at 80/20 ≥ 100% strong verification requires the operator to run the script" disclosure.

**Why this work, this session:** Issue #3 is the last load-bearing cost-optimization layer (#1 prompt cache, #2 semantic cache, #3 model routing). With it shipped, the only remaining open issue in the repo is #5 (savings dashboard), which is a visualization layer on top of the telemetry these three layers already emit.

**Open questions / blockers:** None. The router's `quality at 80/20 ≥ 100% strong` verification is intentionally deferred to operator-run real-API mode — the script + the schema are committed; the curve is not, per the no-fabricated-benchmarks rule.

**Next session:** Either #5 (savings dashboard) once the operator is ready, or move to a different repo. With #1/#2/#3 shipped, `llm-cost-optimizer` is at v0.1-minus-dashboard.

## 2026-05-16 — Issue #4: Anthropic Batch API integration
**Duration:** ~55 min · **Branch:** `session/2026-05-16-1951-issue-4`

- Shipped `cost_optimizer/batch.py` — `BatchBackend` Protocol with `submit/poll/results`, an `InMemoryBatchBackend` (dep-free, deterministic, hermetic-CI), and an `AnthropicBatchBackend` (duck-typed per D-002 — takes a pre-constructed SDK client; package imports without `anthropic` installed). Status enum mirrors the Anthropic Messages-Batch API: `pending` / `in_progress` / `ended_succeeded` / `ended_failed` / `ended_canceled`.
- Recorded D-010: idempotency is **caller-supplied key + payload content hash**. Same payload + same key → existing `job_id` (flaky-retry path); different payload + same key → `IdempotencyConflict` (accidental key-reuse path). Caller key alone is insufficient (silent overwrite risk); content hash alone is insufficient (caller may not have serialized the payload yet at the call site). Both together cover both failure modes.
- Cost comparison: `compare_realtime_vs_batch(rows, prices)` applies `BATCH_DISCOUNT_FACTOR = 0.5` (Anthropic public list, cite docs in commits since rates move) to both input and output tokens. Prices are caller-supplied (`BatchCostQuote`), no list defaults shipped — same posture as D-003. Skips failed rows (neither path bills them). Multi-model batches supported via `model_of={custom_id → model}`.
- 28 new hermetic tests covering: pending-in_progress-ended lifecycle on InMemory; results-before-terminal raises; idempotency dedup; conflict on key-collision with different payload; order-sensitive payload hashing; submit validation (empty list, blank idempotency key, duplicate custom_ids); cost-comparison math against fixture prices; discount-constant default; failed-row skipping; multi-model `model_of` required when `len(prices) != 1`; unknown-model and missing-model_of-entry both raise; out-of-range discount rejection; AnthropicBatchBackend protocol conformance with a fake `_FakeClient` (forwards `Idempotency-Key` header in `extra_headers`; maps SDK status strings to canonical values; surfaces per-row errors via `BatchResultRow.error`); bad-client-shape (`TypeError`) and `None` client (`ValueError`) rejected at construction.
- Public surface added to `cost_optimizer/__init__.py`. README grows a "Batch API integration (#4)" subsection with the lifecycle example + cost-comparison example + D-010 / D-003 explanations.
- Full suite 105/105 pass (was 77/77); ruff clean.

**Why this work, this session:** #4 was the lower-numbered open `priority:med` (the other being #5 savings dashboard, which is a visualization layer on top of telemetry). The portfolio handoff §2 lists "Batch API integration where applicable" as a core deliverable for this repo; with the wrapper shipped, the savings dashboard (#5) can pull batch-vs-realtime savings as one of its strategy columns.

**Open questions / blockers:** Real-API smoke testing against Anthropic's Batch API is operator-triggered with `ANTHROPIC_API_KEY` + budget; CI uses `InMemoryBatchBackend`.

**Next session:** Only #5 (savings dashboard) remains open in this repo. Loop to a different portfolio repo per the multi-issue prompt.
