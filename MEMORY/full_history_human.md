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
