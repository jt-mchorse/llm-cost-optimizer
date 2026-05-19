# Session History (human-readable)

Chronological log of work sessions. Most recent first below the divider.

---

## 2026-05-19 — Issue #17: drop 'Future layers' framing + extend drift lock
**Duration:** ~30 min · **Branch:** `session/2026-05-19-issue-17`

- Rewrote the fourth paragraph of "What this is" from "Future layers — semantic embedding cache (#2), uncertainty-routed model fallback (#3), and a savings dashboard — will land in their own modules" (true on 2026-05-12 when only #1 had shipped) to a six-bullet present-tense list of every shipped layer (#1 prompt cache, #2 semantic cache, #3 router, #4 batch API, #5 dashboard, #7 live-API integration).
- Replaced the bare "*60-second demo pending.*" Demo section with today's two-command hermetic demo (`scripts/bench_savings.py --dry` + `streamlit run cost_optimizer/dashboard/app.py`) and named the captured-asset follow-up as #18 (filed during this session).
- Extended `tests/test_savings_snapshot.py` with three drift-lock tests (11 total now): every closed-issue ref appears in "What this is"; the string `Future layers` does not appear anywhere in the README; the Demo section names a follow-up and describes the runnable surface.

**Why this work, this session:** PR #16 locked the numeric table against `docs/savings.json` but didn't touch the surrounding prose; the autonomous loop noticed two sister-PR-style fixups still missing here.

**Open questions / blockers:** None.

**Next session:** Continues with Phase A selection; #18 is priority:low demo capture.

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

## 2026-05-17 — Issue #5: Savings dashboard
**Duration:** ~70 min · **Branch:** `session/2026-05-17-2307-issue-5`

- Shipped `scripts/bench_savings.py` — runs a deterministic 500-row synthetic workload (60% redundant template paraphrases / 30% easy factual / 10% hard open-ended) through every shipped strategy and writes `docs/savings.{json,md}` plus a committed `docs/savings_workload.json` so the numbers can be re-derived. Five strategies: baseline (cheap-on-everything), prompt caching (system-prefix), semantic cache (HashEmbedder, threshold 0.95), uncertainty router (entropy 1.5), and batch API (0.50× discount). All cost math goes through the real `cost_optimizer.pricing` table and the real `BATCH_DISCOUNT_FACTOR` — no fabricated rates.
- Real measured numbers on this host: baseline $0.0577 → prompt caching saves 84.0% (1 write + 499 reads), semantic cache saves 56.2% (280 hits / 220 misses), batch saves 50.0% (the discount exactly), and the uncertainty router *spends 154.8% more* while lifting mean quality from 0.886 → 0.921. The router's negative-saving line is honest: the layer trades dollars for quality on hard rows, and the README leads with that framing rather than hiding it.
- Built `dashboard/app.py` — a Streamlit page that reads the JSON the bench produces and renders the per-strategy savings bar chart, the cumulative-savings-per-row line chart, the quality-maintained verdict, and an expandable raw-JSON panel. The dashboard does no recomputation; the file on disk is the source of truth, so the README table and the dashboard never drift. Streamlit is behind a new `[dashboard]` optional extra (D-011) — the core package stays dep-free.
- Recorded D-011 (dashboard is Streamlit behind optional extra, mirrors `[redis]` from D-004) and D-012 (bench workload is hermetic synthetic with documented composition; real-API mode is operator-triggered, same posture as `tune_threshold.py` per D-007). README's `Benchmarks / Results` placeholder is replaced with the real measured table; a new "Savings dashboard (#5)" section documents the one-command run.
- 18 new tests in `tests/test_bench_savings.py` covering determinism (two runs → byte-identical JSON), the 60/30/10 mix invariant, per-strategy `saved == baseline - total` reconciliation, cache strategies' positive-savings regression guard, the router's *cost up / quality up* invariant, batch's exact-discount math, cumulative-series reconciliation against strategy summaries (catches drift between the two derivations), monotone row-index, pricing-model sanity, markdown formatting, the `main()` artifact-write path on `tmp_path`, and a streamlit-imports-when-extra-installed test that skips when the extra is absent. Suite total 122/122 + 1 skipped. Ruff lint+format clean.

**Why this work, this session:** #5 is the visualization layer that ties the four cost layers (#1-4) into a single comparable savings table. With every preceding layer shipped, #5 was the last remaining `priority:med` open issue in this repo, and the dashboard is what the README's "Benchmarks / Results" placeholder has been waiting for. Closing it brings `llm-cost-optimizer` to v0.1 (modulo the 60-second demo line).

**Open questions / blockers:** Real-API mode for the bench is intentionally not implemented in this PR. Same honest stub the threshold-sweep script has: the operator wires real adapters and commits `docs/savings_real.md` once vetted. Headless screenshot of the live Streamlit page is deferred — the markdown table is the README's screenshot. No blockers.

**Next session:** Loop to another repo per the multi-issue prompt — `llm-cost-optimizer` has no more `priority:med` open. Likely candidates are the other repos with `priority:med` open: rag-production-kit, embedding-model-shootout, vector-search-at-scale, python-async-llm-pipelines (just merged), mcp-server-cookbook, nextjs-streaming-ai-patterns (just merged), ai-app-integration-tests.

## 2026-05-18 — Issue #7: Live-API integration test
**Duration:** ~25 min · **Branch:** `session/2026-05-18-issue-07` · **PR:** #12

- Added `tests/integration/test_live_cache.py`: cold→warm round trip against the real Anthropic API. Asserts `tokens_written > 0` on cold, `tokens_cached > 0` + `dollars_saved > 0` on warm, plus aggregate-counter consistency.
- Budget guardrail: `LIVE_CACHE_BUDGET_USD` (default $0.10) refuses to run if the synthetic prompt's worst-case spend exceeds the cap. Worst-case is computed at 1 char per token (an extreme over-estimate, real ~0.25/char) so the guardrail is conservative.
- `.github/workflows/integration.yml`: `workflow_dispatch`-only with `python-version` and `model` inputs. Verifies the secret is non-empty before any install.
- `pyproject.toml` gains `norecursedirs = ["tests/integration"]` so the default `pytest` invocation (the one CI runs on every push/PR) doesn't pick up the live tests. Unit suite stays at 122 passed + 1 skipped (streamlit-extra), ~21 s.
- README's quickstart gets a paragraph distinguishing the hermetic unit suite from the manually-dispatched integration suite.
- No new D-NNN: gating live tests on a secret + budget is a pattern (well-established in the portfolio: see e.g. ai-app-integration-tests' record/replay gating), not a tradeoff with alternatives worth recording.

**Why this work, this session:** Low-priority backlog item with a contained 30-minute scope; the gating pattern is reusable across the portfolio.

**Open questions / blockers:** PR explicitly flags that the actual live run is operator-triggered post-merge — the secret-gate + budget-gate are testable locally, the cold-then-warm against the real API is not.

**Next session:** Loop continues — likely embedding-model-shootout #5 (notebook reproducing numbers) or wrap.

## 2026-05-18 — Issue #13: Architecture doc covers all shipped layers
**Duration:** ~30 min · **Branch:** `session/2026-05-18-1537-issue-13` · **PR:** [#14](https://github.com/jt-mchorse/llm-cost-optimizer/pull/14) (ready)

- Rewrote `docs/architecture.md` from one-layer stub to six-section doc: one top-of-page integrated mermaid showing the runtime request lifecycle (semantic cache → router → prompt-cache wrapper → API → telemetry → bench/dashboard), plus per-layer sections for #1/#2/#3/#4/#5 and a #7 live-API integration posture section.
- Each layer section has a prose statement of what it does and what it costs, a mermaid diagram of its own flow, the relevant D-NNN references back to MEMORY, and a "composes with" line. Mermaid labels containing parens are quoted to prevent parser issues.
- README Architecture section dropped its "Diagram pending follow-up issue" stub for a one-line summary that points at the now-real doc.

**Why this work, this session:** Every original `priority:high` issue is closed and the savings dashboard (#5) shipped. The repo failed the §1 quality-bar "architecture diagram" item — there was a diagram, but it only described 1 of 5 shipped layers. Filling that gap is the cleanest move toward v0.1.

**Open questions / blockers:** None — PR is ready for review.

**Next session:** Move to next zero-open-issue repo in build sequence (prompt-regression-suite per §8).

## 2026-05-18 — Issue #15: snapshot test for `docs/savings.{json,md}` + README table
**Duration:** ~30 min · **Branch:** `session/2026-05-18-1921-issue-15`

- Added `tests/test_savings_snapshot.py` (8 tests) that locks the bench output to the three committed artifacts: `docs/savings.json` (full payload), `docs/savings.md` (markdown table), and the README's "Savings dashboard" table. The previous `test_bench_savings.py` covered relative invariants (deterministic order, mix proportions, math identities) but not absolute numbers — a future tweak to a price constant or seed could silently desync the docs from the bench.
- README rows are matched by substring on the strategy keyword (`baseline`, `prompt caching`, `semantic cache`, `uncertainty router`, `batch API`) so cosmetic renames are allowed; numeric cells (`$ spent`, `$ saved`, `% saved`, `Mean quality`) are locked to the JSON with rounding-aware tolerances. Failure messages on every assertion print the one-line regen command.
- README also drops the stale "(122 tests, ~21 s)" count next to the default-pytest line — same hygiene as the `llm-eval-harness` PR from earlier today.
- Verified the failure path by temporarily tampering `docs/savings.json`'s `total_usd`; the assertion fired with the regen hint visible.

**Why this work, this session:** D-012 commits the repo to a "no fabricated numbers" posture; the snapshot test is the enforcement mechanism. The repo has shipped five layers + a savings dashboard with a public table; that table is now structurally guarded against silent desync.

**Open questions / blockers:** None — PR ready for review.

**Next session:** Move to the next build-sequence repo (`prompt-regression-suite` or `rag-production-kit`) and find the equivalent enforcement gap.
