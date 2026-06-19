# Session History (human-readable)

Chronological log of work sessions. Most recent first below the divider.

---

## 2026-05-19 — Issue #20: snapshot lock README numeric/identifier defaults to source constants
**Duration:** ~40 min · **Branch:** `session/2026-05-19-1915-issue-20` · **PR:** [#21](https://github.com/jt-mchorse/llm-cost-optimizer/pull/21) (ready)

- Added `tests/test_readme_defaults_snapshot.py` (5 tests) closing the orthogonal axis the existing `test_savings_snapshot.py` doesn't cover: README claims that quote **source constants** in prose — opus & haiku `input_per_mtok` from `cost_optimizer.pricing`, `BATCH_DISCOUNT_FACTOR` from `cost_optimizer.batch`, `pip install -e '.[<extra>]'` against `[project.optional-dependencies]` keys, and the `LIVE_CACHE_BUDGET_USD` $0.10 default from the integration test's `_DEFAULT_BUDGET_USD` fallback.
- Source is the truth — every failure message tells the operator to update the README quote to match the new live value. The opus price regex uses `/MTok\s+input` because that quote wraps a line in the savings-dashboard section; the live-budget test parses both README mentions (Quickstart + What-this-is) and asserts they agree before comparing against source so a one-side update doesn't silently desync the README with itself.
- Pricing assertions go through the public `get_pricing(...)` API, not the private `_PRICING` dict, so a future internal restructure can't break the snapshot for the wrong reason. Tamper-verified 3 of 5 (`BATCH_DISCOUNT_FACTOR` 0.5→0.4, opus price 15.00→12.00, `LIVE_CACHE_BUDGET_USD` 0.10→0.25) — all fire with the source symbol referenced in the failure message; revert restores green. Full suite 138/138 + 1 skipped (streamlit unavailable locally); ruff check + format clean.

**Why this work, this session:** Phase A repo selection ran with `priority:high` empty across the portfolio, `priority:med` issues already had open PRs against them in the two repos that had any, and `priority:low` was all 60-second demo captures (need screen recording — not autonomous-doable). Filing #20 + working it kept the portfolio's snapshot wave honest by closing the orthogonal source-constant gap in the cost-optimizer repo — sister to the same pattern landed in llm-eval-harness an hour earlier.

**Open questions / blockers:** None.

**Next session:** Continues with whichever repo Phase A selection picks; the same source-constants snapshot template likely applies to `prompt-regression-suite` (default thresholds, embedding similarity tolerance) and `agent-orchestration-platform` (model identifiers, eval extras).

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

## 2026-05-19 — Issue #22: Public-surface snapshot test
**Duration:** ~25 min · **Branch:** `session/2026-05-19-2324-issue-22` · **PR:** [#23](https://github.com/jt-mchorse/llm-cost-optimizer/pull/23) (ready, CI green, merging)

- Issue filed in-session (sister to `llm-eval-harness` #24 from earlier in this session). The README quotes four `from cost_optimizer import …` library-use snippets pulling 14+ names, but no test locked the SHAPE — coverage was already 100% because existing tests incidentally touch the re-exports, masking the surface-rename risk.
- New `tests/test_public_surface.py` adds four orthogonal axes: `__all__` round-trip vs AST-parsed imports; every `__all__` entry bound non-None; **every README `from cost_optimizer import …` snippet auto-discovered by regex and compiled against the live package** (so a fifth library example becomes a fifth test case for free); one anchor per submodule (batch / cache_wrapper / pricing / router / semantic_cache). A guard test asserts the regex still matches > 0 snippets so the regression mode is loud, not silent.
- Tamper-verified 3-of-4: dropping `SemanticCache` from `__all__` fires the round-trip test naming the entry; alias-renaming `PromptCacheWrapper as PCW` fires the snippet-0 test naming the missing symbol; nuking every `from cost_optimizer` in README fires the guard test.

**Why this work, this session:** Same posture as the sister `llm-eval-harness` snapshot landed earlier today. Library-style repos in this portfolio need their public surface locked at both levels — Python `__init__.py` and README text — because each catches a different class of silent break. The README-regex extraction is the load-bearing improvement over the eval-harness version: future library examples self-onboard.

**Open questions / blockers:** None.

**Next session:** Repo's open queue is now {#18 (demo capture)}, gated on human action. Same self-filed-actionable pattern available across the other Python repos in this portfolio (prompt-regression-suite, embedding-model-shootout, chunking-strategies-lab, vector-search-at-scale, python-async-llm-pipelines, rag-production-kit).

## 2026-05-22 — README quoted `cost_optimizer/dashboard/app.py`; actual path is `dashboard/app.py` (#25)

**Duration:** ~25 min. **Issue:** [#25](https://github.com/jt-mchorse/llm-cost-optimizer/issues/25). **PR:** [#26](https://github.com/jt-mchorse/llm-cost-optimizer/pull/26).

Two places in the README quoted `streamlit run cost_optimizer/dashboard/app.py`: the "Today the five runtime layers ship" bullet at L21 and the Demo section at L326. The actual dashboard lives at top-level `dashboard/app.py` (the middle of the README, the architecture doc, and the filesystem all agree on this). A reader who copy-pasted the literal command got `Error: Invalid value: File does not exist`.

Fix is two-line: update L21 and L326. Lock against drift via `tests/test_readme_paths_resolve.py` (+2 tests): the first parses all paths-shaped tokens that appear inside `` `backticks` `` or `` ```bash``` `` fences, then asserts each resolves on disk or appears on an explicit `_KNOWN_OPERATOR_GENERATED` allow-list (three legitimate "operator runs this, then this file appears" docs that the no-fabricated-benchmarks rule says we intentionally don't pre-commit). The second hard-pins the original failure: assert `cost_optimizer/dashboard/app.py` is absent from the README and `dashboard/app.py` is present, so even a future rename in either direction fails CI before merge.

Continuation note: this work was drafted in a prior session that did not open a PR — the code sat in the working tree as an unpushed stash. This session picked the stash up, branched, ran the full pytest suite (152 passed + 1 skipped), tamper-verified the snapshot test fires for the original failure mode, committed code separately from MEMORY, and opened PR #26. The path-snapshot test pattern is portable — any portfolio repo whose README quotes file paths inside code regions could adopt the same lock with ~30 lines of pytest. Open questions / followups: none.

## 2026-05-23 — Architecture-doc drift lock (#27)

**Duration:** ~30 min. **Issue:** [#27](https://github.com/jt-mchorse/llm-cost-optimizer/issues/27). **PR:** [#28](https://github.com/jt-mchorse/llm-cost-optimizer/pull/28).

This repo was one of five portfolio repos still lacking the architecture-doc lock that landed across `embedding-model-shootout` PR #20, `vector-search-at-scale` PR #22, `llm-eval-harness` PR #30, `prompt-regression-suite` PR #25, plus the JS variants in `mcp-server-cookbook` PR #23, `nextjs-streaming-ai-patterns` PR #19, and `ai-app-integration-tests` PR #19. This session shipped the lock without modifying `docs/architecture.md` — the doc was already in the steady-state shape; only the regression test was missing.

The schema pivots from sister repos: this doc annotates surfaces with `D-NNN` core-decision references rather than `(#NN)` issue references, so the active-decision coverage axis anchors to `MEMORY/core_decisions_ai.md` (every non-superseded `D-NNN >= 2` must be cited at least once). D-001 is the scope baseline and is intentionally excluded — it's a portfolio-level baseline, not an architectural shape decision.

Drift caught while authoring: `docs/architecture.md` quotes `docs/savings_real.md` as the file an operator commits after a real workload (per D-012's "no fabricated benchmarks" posture). That path doesn't (and shouldn't) exist in-repo until the operator runs the real-API path. The fix is an explicit `OPERATOR_SUPPLIED_PATHS` allow-list with an inverse safety net (`test_operator_supplied_paths_actually_absent`) that fires if a listed path ever lands on disk — at which point it has stopped being operator-supplied and should be dropped.

Tamper-verified three ways: reinjecting `(this PR — issue #1)` in §1 header fires `test_no_banned_phrases`; removing all `D-010` references fires `test_every_active_decision_referenced`; quoting `cost_optimizer/nonexistent.py` fires `test_backtick_paths_resolve_on_disk`.

**Why this work, this session:** First of five sister issues in this night-session sweep. The portfolio pattern of architecture-doc drift was the dominant work-shape of the 2026-05-22 day session; this completes the lock coverage across the Python half of the portfolio. **Open questions / blockers:** none. **Next session:** continue the sweep across `rag-production-kit`, `chunking-strategies-lab`, `python-async-llm-pipelines`, `agent-orchestration-platform`.

## 2026-05-23 — 60-second demo capture script (#18, AC3 of 3)

**Duration:** ~25 min. **Issue:** [#18](https://github.com/jt-mchorse/llm-cost-optimizer/issues/18). **PR:** [#29](https://github.com/jt-mchorse/llm-cost-optimizer/pull/29).

Sister to [`llm-eval-harness#33`](https://github.com/jt-mchorse/llm-eval-harness/pull/33), landed earlier in the same day-session loop. The two-stage structure mirrors the README's "Demo" section commands:

- **STAGE 1 (auto, hermetic).** `scripts/capture_demo.py` calls `scripts.bench_savings.main(["--dry", "--out", <tmp>])` in-process so the rendered five-strategy savings table appears in the recording's terminal frame under an explicit stage banner. Fresh artifact copies land at `docs/demo-artifacts/savings_demo.{md,json}` (gitignored). The bench-import helper uses the same `sys.path` bootstrap as `tests/test_bench_savings.py`, so a future rename of `scripts/bench_savings.py` fails both the test and the capture script at the same time — they share an import contract.

- **STAGE 2 (operator-action).** Cheat-sheet prints the exact `streamlit run dashboard/app.py` command, the `http://localhost:8501` URL, and a three-step checklist (strategy summary → cumulative-savings chart → strategy comparison view) so the click path is reproducible across recordings. `--launch-streamlit` subprocess-spawns the dashboard for one-key operator sessions; off by default because streamlit is a long-running server that can't run hermetically in CI.

`tests/test_capture_demo_smoke.py` adds four tests under the same hermetic contract as the existing smoke suites. Pass count: 167 → 171, plus the same one pre-existing streamlit-dashboard skip.

**Why this work, this session:** Second issue in the day-session multi-issue loop. The portfolio reached the quiet point where every open issue is a `[demo]` GIF/MP4 capture, the v0.1 quality bar's only outstanding row across all twelve repos. Of the three acceptance criteria, AC3 (capture script) is the only one Claude can land — AC1 and AC2 need a real screen recorder. Same pattern as the first loop iteration on `llm-eval-harness`; this one extends the script-coverage row across the cost-optimizer demo.

**Open questions / blockers:** AC1 + AC2 require operator action (screen recorder + README embed). The PR is ready for review on AC3 standalone — issue #18 stays open until JT records.

**Next session:** Continue the loop. Build-sequence pos 3 is `prompt-regression-suite` #15 — same AC3-only pattern.

## 2026-05-24 — Issue #30: `--dry`/`--no-dry` parity, real-API guard was dead code

**Duration:** ~20 min. **Issue:** [#30](https://github.com/jt-mchorse/llm-cost-optimizer/issues/30). **Branch:** `session/2026-05-24-0317-issue-30`.

`scripts/bench_savings.py` and `scripts/tune_threshold.py` both declared `--dry` as `action="store_true", default=True`, which pinned `args.dry` to True forever and made the `if not args.dry: print("::error::real-API ... not implemented"); return 2` block immediately below it unreachable. Both existing tests acknowledged the gap in a comment — `# default for --dry is True; can't actually trigger --no-dry from argparse` — and asserted `rc == 0` on a bare invocation instead of the documented `rc == 2`.

Switched both flags to `action=argparse.BooleanOptionalAction` (Python 3.9+ stdlib, already the project floor per `pyproject.toml`'s `requires-python`). `--no-dry` now actually opts into the real-API branch and the existing guard fires correctly. Rewrote the two `test_main_*` tests to invoke `--no-dry`, assert `rc == 2`, assert the `::error::real-API ... not implemented` marker is on stderr, and assert no artifacts were written; added a sister `test_main_dry_default_path_still_succeeds` in `tests/test_bench_savings.py` to belt-and-braces the unchanged stub path.

D-007's posture — real-API bench/tune mode is operator-supplied, not in-repo — was documented in the README and the source but couldn't be enforced at the CLI layer until this fix. Now `--no-dry` is a real CI assertion, not just an inline comment.

**Why this work, this session:** Opportunistic second issue in the night-session multi-issue loop after landing `llm-eval-harness` #34's `diff --format markdown` parity. Same shape of work — surface a quietly-broken contract, fix it, lock with a test, no new D-NNN.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Continue the night-session loop on build-sequence #3 (`prompt-regression-suite`) and beyond. The pattern this session establishes — "look for CLI flags or guards that should be enforceable but aren't" — generalizes across the portfolio's other dry/stub modes.

## 2026-05-24 — Issue #32: UncertaintyRouter validates signal names are unique at construction
**Duration:** ~30 min · **Branch:** `session/2026-05-24-issue-32`

- `UncertaintyRouter` accepted `signals: list[EscalationSignal]` but never checked that the `name` attributes were unique. `route()` builds `signal_values: dict[str, float | None]` by `readings[sig.name] = reading.value`; two signals sharing a name silently overwrote each other. D-009 explicitly designates `signal_values` as the dashboard's cost-attribution telemetry, so the bug was a data-integrity hole, not just a quality-of-life one.
- Added a `__post_init__` that raises `ValueError(f"duplicate signal names: {sorted(dups)}")` — same message shape as the existing `batch.submit()` duplicate-`custom_ids` guard, so the cost-optimizer keeps a consistent loud-failure dialect.
- Four new tests in `tests/test_router.py` under a `#32` block: same-name raises; three-signal case (two collide, one unique) lists only the colliding name; deliberately-distinct names on the same `JudgeConfidenceSignal` class construct cleanly and `route()` records both readings (the legitimate multi-judge use case); regression-pin that `[EntropySignal(), JudgeConfidenceSignal()]` — the canonical README pairing with different default names — still constructs.

**Why this work, this session:** Sister to `python-async-llm-pipelines` #28 (constructor-time validation parity) which landed in Phase A of this same day-session. The cost-optimizer was the last hot module in the portfolio that built a name-keyed telemetry dict without policing the names. Surfacing a real data-integrity bug, not just a guard-rail polish.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Continue the day-session loop. Build-sequence priority next is `prompt-regression-suite` (position 3) or back to `llm-eval-harness` (position 1) — both have a similar "is there a quietly-broken Protocol contract" hunting ground.

## 2026-05-24 — Issue #34: ModelPricing validates rates/multipliers in __post_init__
**Duration:** ~18 min · **Branch:** `session/2026-05-24-issue-34`

- `ModelPricing` at `cost_optimizer/pricing.py:26` is a frozen dataclass with four fields (`model`, `input_per_mtok`, `cache_write_multiplier`, `cache_read_multiplier`). No validation. A negative `input_per_mtok` or `cache_read_multiplier > 1.0` silently flips the sign of `dollars_saved` at `cache_wrapper.py:177-179`. Built-in entries at `pricing.py:42-45` are fine because the literals are sane, but the public `ModelPricing` constructor — and `register_pricing`, and `PromptCacheWrapper(pricing=...)` — were the actual contract boundary, untyped.
- Added `__post_init__` raising `ValueError` for: any of the three numeric fields `< 0.0` (with the offending field name and violated bound in the message), or `model` not a non-empty string. Frozen dataclasses can validate at construction since `frozen=True` only blocks reassignment, not initial set — a useful pattern worth pinning in memory.
- Eleven new tests in `tests/test_cache_wrapper.py` under a `#34` block: parametrized over (field, bad-value) for the three numeric fields × two bad values each (6 cases); over invalid model strings (empty, None, non-string — 3 cases); over inclusive-zero accepted for each numeric field (3 cases); plus one smoke that re-loads the built-in table to pin against regressing the literals. Full suite 184/184 + 1 skipped (streamlit unavailable locally).

**Why this work, this session:** Direct extension of D-003 (`in_repo_pricing_table_unknown_models_raise / savings_math_must_be_traceable_to_documented_rate_never_fabricated`). The decision said "no invented model"; this extends it to "no invented numbers within a known model". Second Phase B+C target of the 180-min day session after `llm-eval-harness` #40 (drift threshold validation, same harm-class family).

**Open questions / blockers:** none — PR ready for review.

**Next session:** Continue the day-session loop. Build-sequence position #3 (`prompt-regression-suite`) is the natural next pickup; scan its public-surface threshold/range parameters for the same shape of gap.

## 2026-05-25 — Issue #36: validate router signal thresholds and SemanticCache TTL finiteness
**Duration:** ~25 min · **Branch:** `session/2026-05-24-issue-36`

- Three sites silently absorbed operator misconfig that directly affected cost decisions. **`EntropySignal.threshold`** and **`JudgeConfidenceSignal.threshold`** had no validation at all: NaN made the trip comparison always-false → signal never trips → escalation gate silently disables; negative entropy threshold made every reading satisfy `>= threshold` → silent always-trip → strong model on every request → D-009 savings dashboard silently reports wrong cost attribution. Judge threshold `> 1.0` had the same silent-always-trip shape. **`SemanticCache.default_ttl_s`** had a sign-only `<= 0` check that accepted NaN; a NaN TTL stored as `expires_at = now + NaN = NaN`, then every `now < expires_at` check is false → every entry reads as expired → cache silently bypassed.
- Added `__post_init__` validation to both signal dataclasses using `math.isfinite` plus field-appropriate ranges (entropy `>= 0`; judge `[0, 1]`). Extended `SemanticCache.__init__` `default_ttl_s` sign-only check to finiteness with the error message "must be a finite positive number" (the new message keeps "positive" as a substring so the existing `test_ttl_validated_positive` test still passes unchanged).
- 16 new tests: parametrized rejection per signal (NaN, +Infinity, -Infinity, negative for entropy, out-of-range for judge); inclusive-boundary acceptance for each (`threshold=0.0` for entropy; `threshold ∈ {0.0, 0.5, 1.0}` for judge); default-value regression for both signals. SemanticCache adds 3 parametrized non-finite cases. Test count 202. Ruff + format clean.

**Why this work, this session:** Seventh Phase B+C target in the 360-min night session. Second PR in llm-cost-optimizer tonight; the first was via the Phase A fixup-merge of #35 (ModelPricing `__post_init__` validation). The cost-dataclass side was already done; this PR completes the cost-decision side, making D-009's savings-dashboard cost-attribution surface loud-on-misconfig end-to-end.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Continue the loop with rag-production-kit or embedding-model-shootout for a second iteration. Per memory, the cost dataclasses in those repos already got `__post_init__` validation in the fixup-merged PRs today; the operational/runtime gaps (TTL-like, similarity-threshold-like, signal-like) likely remain.

## 2026-05-25 — Issue #38: BatchRequest/BatchResultRow/BatchJobMeta __post_init__ guards
**Duration:** ~25 min · **Branch:** `session/2026-05-25-1535-issue-38`

- `cost_optimizer/batch.py` was the last unvalidated dataclass module in the repo — pricing and router both gained `__post_init__` guards in the recent sweep (#34, #36), but the batch API still accepted degenerate numerics silently. Added three guards at the dataclass boundary: `BatchRequest.max_tokens` (int >= 1, reject bool), `BatchResultRow.prompt_tokens`/`completion_tokens` (int >= 0, reject bool), `BatchJobMeta.n_requests` (int >= 1, reject bool).
- Each guard explicitly rejects `bool` because `bool` is an `int` subclass in Python; and rejects `float` (even `1.0`) because the field is typed `int`. Zero is permitted for `BatchResultRow` (the canonical "failed row" surface — existing tests pin this), rejected for the other two.
- 37 new tests in three nested classes follow the established `TestEntropySignalThresholdValidation` pattern: `pytest.mark.parametrize` over a bad-value table plus a boundary-accept test per field. All 238 existing tests stay green; ruff clean.

**Why this work, this session:** The Phase A merge pass closed 11 PRs across all 12 repos; only one priority:high issue (`mcp-server-cookbook#32`) remained, and Phase B+C closed it via a 12-line README test-count drift fix. With zero open priority:high left, I went repo-by-repo looking for the latent sweep gap and found that `batch.py` had been skipped. Filing and closing #38 in the same session matches the prompt's "aim for 2-4 issues per DAY session" directive.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Continue the loop. The portfolio's dataclass-validation sweep is now arguably complete across this repo; the trending workflow will surface fresh work topics.

## 2026-05-26 — Issue #40: HashEmbedder.ngram completes the portfolio's four-implementation HashEmbedder sweep
**Duration:** ~20 min · **Branch:** `session/2026-05-26-0030-issue-40`

- `HashEmbedder.__init__(ngram)` in `cost_optimizer/semantic_cache.py:60-63` was the last remaining sign-only HashEmbedder construction site in the portfolio. Today's sweep already tightened `rag-production-kit#43` (HashEmbedder.dim), `embedding-model-shootout#36` (hash_embedder.dim + ngram), and `prompt-regression-suite#38` (HashEmbedder.ngram). This PR closes the loop — all four implementations now share the `not isinstance(int) or isinstance(bool) or <= 0` contract with the matching `"must be a positive integer; got {ngram!r}"` error.
- Closed the cache-hit-rate-degradation harm class — `HashEmbedder(ngram=True)` silently bound to True (=1), produced unigram embeddings with worse retrieval quality, and the SemanticCache hit-rate silently degraded with no error. **Since this repo exists to optimize cost via cache hits, silent hit-rate loss is the worst-shaped failure mode for the repo purpose.**
- Updated one pre-existing test's `match=` regex from `">= 1"` to `"ngram must be a positive integer"`. New 15-value parametrize reject matrix + 5-value acceptance matrix + default-ngram pin (21 new collected cases). Full suite 238 → 259 passed (1 skipped streamlit). Ruff clean.

**Why this work, this session:** Tenth Phase B+C target in the 360-min night session. Picked because the four-HashEmbedder portfolio symmetry was 3/4 complete after today's earlier sweeps and `prompt-regression-suite#38` (this night). Closing the loop brings the contract to 4/4.

**Open questions / blockers:** none — PR ready for review.

**Next session:** The night session has now produced 9 Phase B+C PRs across 9 repos (or 10 PRs across 10 repos counting this one), plus 4 Phase A rescue merges. The portfolio-wide validation-sweep arc is comprehensively saturated. Future sessions should pivot away from validation per the prior memory's guidance.

## 2026-05-26 — Issue #42: Atomic `--out` writes in bench/sweep scripts (the atomicity pattern propagates)
**Duration:** ~25 min · **Branch:** `session/2026-05-26-1517-issue-42`

- Four `Path.write_text` call sites across `scripts/bench_savings.py` (3) and `scripts/tune_threshold.py` (1) wrote artifacts non-atomically. The streamlit dashboard (`cost_optimizer/dashboard/app.py`) loads `docs/savings.json` per the demo flow — Streamlit re-renders on file change, so a SIGINT mid-write displays partial strategy rows silently (the worst shape for this repo's purpose). `docs/savings.md` renders inline on the README; half-written breaks it in the same window. `tune_threshold.py`'s JSON feeds operator plot regen.
- Added `scripts/_io.py` with `atomic_write_text(path, text)` — `tempfile.NamedTemporaryFile(dir=parent, delete=False)` + `fsync` + `os.replace` + `contextlib.suppress(FileNotFoundError)` cleanup. Same shape as the helper landed in `llm-eval-harness#48` earlier this session so the portfolio-wide pattern is uniform. Lives under `scripts/` as a private utility (leading-underscore) so it doesn't expand the `cost_optimizer` public surface.
- Routed `bench_savings.py:731` (`_write_workload`), `bench_savings.py:771` (`savings.json`), `bench_savings.py:772` (`savings.md`), and `tune_threshold.py:294` through the helper. The `out_json.parent.mkdir(...)` calls were dropped because the helper does it.
- New `tests/test_atomic_write.py` (10 tests): six unit tests on the helper (happy path / parent-dir create / overwrite / `os.replace`-raises destination-absent / temp-cleanup-on-failure / overwrite-fails destination-unchanged — the property `Path.write_text` could never offer) plus four integration tests (bench_savings with monkeypatched `os.replace` asserts none of the three artifacts exist after failure; tune_threshold same; end-to-end happy paths through both scripts assert valid contents). Full suite 286 → 296 (one streamlit skip pre-existing). Lint + format green.

**Why this work, this session:** Second Phase B+C target in today's 180-min DAY session. Parallels `llm-eval-harness#48` (filed and merged earlier this session) — same harm class (output-layer corruption), same fix shape (sibling-tempfile + fsync + os.replace). Demonstrates that the portfolio-wide pattern uniformity called out in the prior memory entry is real, not aspirational.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Atomicity arc continues. `prompt-regression-suite` writes HTML diff reports; `rag-production-kit` writes cost-telemetry rollups. Both plausibly need the same pattern. Two more repos closes the arc.

## 2026-05-26 — Issue #44: README decision-range upper-bound lock
**Duration:** ~10 min · **Branch:** `session/2026-05-26-2322-issue-44`

- Added `tests/test_readme_decision_range.py` with the active-decision-range upper-bound invariant.
- Bumped README's architecture-section summary to cite `D-002…D-012`.

**Why this work, this session:** Same cross-portfolio drift class authored in chunking-strategies-lab this session and propagated to llm-eval-harness moments ago — extending to llm-cost-optimizer is propagation 2 of 10.

**Open questions / blockers:** none.

**Next session:** Continue propagation to prompt-regression-suite, then onward per build sequence.

## 2026-05-27 — Issue #46: drop stale "· this PR" from four README section headers + banned-phrase lock
**Duration:** ~12 min · **Branch:** `session/2026-05-27-0324-issue-46`

- Four section headers in `README.md` still carried PR-time framing ("· this PR") for surface that's been shipped for weeks: `Semantic cache (#2 · this PR)`, `Model routing (#3 · this PR)`, `Batch API integration (#4 · this PR)`, `Savings dashboard (#5 · this PR)`. Same drift class `prompt-regression-suite#43` just resolved.
- Rewrote the four headers to steady-state form.
- New lock: `tests/test_readme_banned_phrases.py` with `BANNED_PHRASES = ("this pr",)` + hard-pin tuple test. Mirrors the lock authored in `prompt-regression-suite#43` — same shape, same docstring, repo-specific section names only.
- Full suite 273 pass + 1 expected skip (streamlit not installed in dev env). Lock test 3/3 pass.

**Why this work, this session:** Iteration 4 of an autonomous NIGHT session loop, second repo in the README banned-phrase lock propagation arc.

**Open questions / blockers:** none — PR ready for review.

**Next session:** Two more repos in the portfolio have the same `· this PR` drift: `embedding-model-shootout` (1 hit) and `python-async-llm-pipelines` (2 hits). Same fix shape applies to both.

## 2026-05-27 — Issue #48: CONTRIBUTING.md cadence-wording propagation
**Duration:** ~3 min · **PR:** #49

- Replaced pre-D-008 `~60-minute session cap` line with D-008 (180/360 min, multi-issue loop) and D-004 (Phase A PR auto-merge) wording, matching the bootstrap template post-portfolio-ops#3.

**Why this work, this session:** Iteration in the autonomous NIGHT session propagation arc for portfolio-ops#3.

**Open questions / blockers:** none.

**Next session:** continue portfolio propagation.

## 2026-06-01 — Issue #50: Observability surface for cache telemetry
**Duration:** ~35 min · **Branch:** `session/2026-06-01-1524-issue-50`

- Added `CacheTelemetry.to_dict()` returning the dataclass field set verbatim (`{hits, misses, tokens_cached, tokens_written, dollars_saved}`). Locked by a field-set test against `dataclasses.fields(t)` so adding a new field to `CacheTelemetry` without teaching `to_dict` about it fails loud.
- Added `PromptCacheWrapper.dump_aggregate_json(path)` that writes the current `self.aggregate.to_dict()` as sorted-keys JSON with a trailing newline through the package-level atomic-write helper. `Path.write_text` was the obvious shape but it's not atomic — a Ctrl-C / disk-full / OOM between truncate and flush leaves the consumer reading a half-written file, which a log tailer or dashboard would crash on.
- Promoted `scripts/_io.py::atomic_write_text` to `cost_optimizer/io_utils.py`. Mirrors `llm-eval-harness` D-015 ("atomic-write helpers live at the package level, not file-private"). `scripts/_io.py` becomes a re-export so existing imports in `bench_savings.py` / `tune_threshold.py` keep working unchanged; an identity check (`scripts._io.atomic_write_text is cost_optimizer.io_utils.atomic_write_text`) is locked by a test so a future fork of the helper into two parallel implementations fails loud.
- Updated `tests/test_atomic_write.py`'s monkeypatch target from `scripts._io.os` to `cost_optimizer.io_utils.os` since the canonical home moved. All five `monkeypatch.setattr(io_mod.os, "replace", boom)` tests in that file still exercise the same atomicity invariant — the change is just the address.
- README "What this is" #1 bullet extended to name the new surface. `docs/architecture.md` Prompt-cache "Why these decisions" section extends to the JSON observability shape and the io_utils promotion. The architecture-doc lock caught a `::` in a function reference being parsed as a path; rewrote the prose to avoid double-colons.

**Why this work, this session:** Second DAY-session iteration of 2026-06-01. Build sequence picked `llm-cost-optimizer` next (earliest in §8 with zero open priority:high after `llm-eval-harness`). The runtime layer had aggregate telemetry but no serialization surface — the most common downstream use is shipping aggregate metrics to an observability sink, which today requires hand-rolled field extraction. Real productive gap, additive surface, no decision touched.

**Open questions / blockers:** none — 281 pytest pass + 1 expected skip (streamlit not in dev env), ruff clean.

**Next session:** the next natural extension is a streaming/per-call sink (`PromptCacheWrapper.on_call(callback)` or similar) so each individual `CallResult` flows through to a metrics backend without the caller polling `aggregate`. Out of scope for #50 — would be a clean follow-up.

## 2026-06-01 — Issue #52: `CacheStats.to_dict` + `SemanticCache.dump_stats_json` (observability parity)
**Duration:** ~35 min · **Branch:** `session/2026-06-01-1935-issue-52`

- Added `CacheStats.to_dict()` to `cost_optimizer/semantic_cache.py` returning the four raw counters (`hits`, `misses`, `invalidations`, `expired_purged`) plus the two derived properties (`total_lookups`, `hit_rate`). Derived fields are included so downstream log consumers don't recompute them from the raw counters — and so a future formula change is locked at the dict layer too, not just the property.
- Added `SemanticCache.dump_stats_json(path)` that writes `stats.to_dict()` via the package-level `cost_optimizer.io_utils.atomic_write_text` helper (the same helper #50 promoted from `scripts/_io.py`). Sorted-keys JSON, `indent=2`, trailing newline — byte-shape parity with `PromptCacheWrapper.dump_aggregate_json` so one log-parsing config consumes both files.
- 8 new tests in `tests/test_semantic_cache_dump.py` mirror the matrix `tests/test_cache_wrapper_dump.py` set up for #50: raw-field-set exhaustiveness via `dataclasses.fields`, derived-field correctness as a separate lock with triangulation against the manual formula, zero-state `hit_rate=0.0` lock (not NaN; the property short-circuits on `n_lookups == 0`), on-disk shape lock with sorted-keys check, parent-dir auto-create (from `atomic_write_text`'s `parent.mkdir(parents=True)`), atomic-overwrite with no tempfile leftovers, zero-state canary writer.
- README "Semantic response cache" bullet (#2) extended with one sentence on the new observability shape citing #52. `docs/architecture.md` layer-2 invariants section gains a parallel paragraph naming the parity with #50. No new D-NNN — this is pure pattern parity work.

**Why this work, this session:** Iteration 3 of today's DAY session. Iterations 1 and 2 closed `llm-eval-harness#58` (validate --calibration) and `prompt-regression-suite#49` (prompt-snap validate). Looking at the just-merged #51 (PR for #50 cache-wrapper observability), the symmetric gap on the other cache layer was obvious — `CacheStats` had no `to_dict`, `SemanticCache` had no `dump_stats_json`, the two layers were exposing two different observability shapes to downstream consumers in `rag-production-kit` and `agent-orchestration-platform`. Filing #52 and shipping the parity inside the same day session shrinks the cross-repo integration surface.

**Open questions / blockers:** none — full pytest pass (290/290 with one streamlit-extras skip), ruff check + format clean, live smoke shows the on-disk JSON has the expected shape.

**Next session:** with the two cache layers at observability parity, the natural follow-on is wiring both into the savings dashboard so the live UI can show hit-rate over time alongside the existing per-strategy dollar charts. Out of scope here; would be a clean #5-adjacent issue if operators ask for it.

## 2026-06-02 — Issue #54: StrategyResult.to_dict + ThresholdSweepRow.to_dict
**Duration:** ~18 min · **Branch:** `session/2026-06-02-0356-issue-54`

- Closed the last `dataclasses.asdict` usages in this repo. After #50 / #52 / the `io_utils` package-level promotion, the remaining gaps lived in the two operator-facing scripts:
  - `scripts/bench_savings.py`: `StrategyResult.to_dict` (8-field contract; `extra` shallow-copied) replaces `[asdict(s) for s in strategies]` in `_build_payload`.
  - `scripts/tune_threshold.py`: `ThresholdSweepRow.to_dict` (7-field contract) replaces `[asdict(r) for r in rows]` in `_build_payload`.
- Both files drop the `asdict` import; `grep -rn asdict scripts/ cost_optimizer/` returns no source matches (only stale `__pycache__`).
- 7 new tests across `tests/test_bench_savings.py` + `tests/test_tune_threshold.py`: per-class sorted-keys pin, value round-trip, shallow-copy guard on `StrategyResult.extra`, and an acceptance regression that the script's emitted payload uses the same field set as `to_dict`. The acceptance regression is the catch-net for a future refactor that re-introduces `asdict` in the list-comp without updating the dataclass.
- 288/288 pass (was 281, +7 new cases). Ruff check + format clean. No new `D-NNN` — pure extension of the observability-parity arc.

**Why this work, this session:** Iteration 6 of the night session loop. The five other repos in the observability-parity arc (vector-search-at-scale, prompt-regression-suite, python-async-llm-pipelines, rag-production-kit, and llm-cost-optimizer's package-level surface) are saturated; this PR completes the arc by closing the script-level dataclasses in the only repo that still had them.

**Open questions / blockers:** none — ready for review.

**Next session:** Observability-parity arc now fully saturated across the Python repos at both package and script levels. Future iterations should pivot to either novel parity opportunities outside the asdict / to_dict arc, or operator-blocked items (demo capture, trending workflow secrets).

## 2026-06-17 — Issue #56: Workflow YAML-parseability lock
**Duration:** ~10 min · **Branch:** `session/2026-06-17-1919-issue-56`

Added `tests/test_workflows_yaml_parseable.py` (5 tests across `ci.yml`
and `integration.yml`) and pulled `pyyaml>=6.0` into the `dev` extras.

**Why this work, this session:** Fifth hop of the `portfolio-ops#30`
propagation arc — same inverse safety net for the 21-day silent CI
outage closed in `portfolio-ops#27`.

**Open questions / blockers:** none — local `pytest` 301 → 306 + ruff
clean; PR #57 open.

**Next session:** continue propagation to the remaining 7 repos.

## 2026-06-17 — Issue #58: timeout-minutes guard for ci.yml
**Duration:** ~20 min · **Branch:** `session/2026-06-17-2322-issue-58`

- Added `timeout-minutes: 15` to each ci.yml job (lint, test, memory-check). `integration.yml`'s job already had `timeout-minutes: 10`.
- Added `tests/test_workflows_timeout_minutes.py` — same shape as the canonical lock in `llm-eval-harness` (1 smoke + 3 parametrized × 4 jobs = 13 tests). Policy band `[1, 30]` replicated without override.

**Why this work, this session:** propagation of `llm-eval-harness#62` shipped earlier in the same session as part of the multi-issue day-session loop. Next in §8 build sequence and already had one bounded workflow, making it the natural follow-on.

**Open questions / blockers:** none. 301 → 314 pytest passes. PR #59 open.

**Next session:** continue propagating across the remaining 10 portfolio repos when time/scope allows. After a few weekly audit-cron cycles (portfolio-ops#34), consider adding a `missing-timeout` fingerprint to the audit script so the cron surfaces unguarded jobs directly.

## 2026-06-18 — Issue #60: concurrency guard + lock test
**Duration:** ~15 min · **Branch:** `session/2026-06-18-1519-issue-60`

- Added top-level `concurrency:` to `ci.yml` (`ci-${{ github.ref }}`)
  and `integration.yml` (`integration-${{ github.ref }}`, distinct so
  the manual-dispatch live-API suite doesn't cancel CI runs on the same
  ref).
- Copied `tests/test_workflows_concurrency.py` from llm-eval-harness with
  docstring origin updated; integration-workflow note specifically calls
  out the `LIVE_CACHE_BUDGET_USD` double-billing risk a missing
  concurrency lock would expose on operator redispatch.

**Why this work, this session:** second per-repo hop in the
concurrency-lock propagation arc. Canonical first hop: llm-eval-harness
#64 / #65. Audit-side fingerprint: portfolio-ops #41.

**Open questions / blockers:** none. Test count 314 → 321 (1 streamlit
skip unchanged).

**Next session:** continue propagation to remaining priority-tier and
non-tier repos.

## 2026-06-19 — Issue #62: router observability surface
**Duration:** ~35 min · **Branch:** `session/2026-06-19-0310-issue-62`

- Added `RouterStats` dataclass to `cost_optimizer/router.py` with five
  raw counters (`total_routes`, `escalations`, `cheap_only`,
  `per_signal_trips`, `per_signal_measured`) plus a derived
  `escalation_rate` property. `to_dict` defensively copies the
  per-signal dicts so external callers can't mutate the live counters
  through the snapshot.
- `UncertaintyRouter` now accumulates stats in `route()`:
  first-trip-wins attribution credits only `triggered_signal`;
  `per_signal_measured` counts every signal that returned a non-`None`
  reading, preserving the "didn't trip" vs. "couldn't measure"
  distinction `RouterDecision.signal_values` already exposes per call.
- `dump_stats_json(path)` ships byte-shape parity with #50/#52: sorted
  keys, `indent=2`, trailing newline, atomic-write through
  `io_utils.atomic_write_text`.
- 10 new tests in `tests/test_router_dump.py` mirror
  `test_cache_wrapper_dump.py`'s recipe.
- Architecture doc §3 gets a matching `#62` bullet.

**Why this work, this session:** closes the last observability gap in
the runtime layer. All three runtime classes (prompt cache, semantic
cache, router) now expose one observability shape — same JSON dict
shape, same atomic-write helper, same operator workflow.

**Open questions / blockers:** none. 321 → 331 pytest passes. PR #63
open and ready.

**Next session:** consider plumbing `RouterStats` into the savings
dashboard so per-signal escalation cost is visible alongside cache
savings (separate issue).

## 2026-06-19 — Issue #64: Surface `RouterStats.to_dict()` in savings JSON
**Duration:** ~30 min · **Branch:** `session/2026-06-19-issue-64`

- Added optional `router_stats: dict[str, Any] | None = None` field to
  `StrategyResult`. Populated only on the uncertainty-router row from
  `router.stats.to_dict()` (#62 / PR #63); `None` everywhere else so a
  dashboard can identify the router by `router_stats is not None`
  without a string-substring check on the strategy label.
- Eight-field contract (#54) becomes nine-field (#54 + #64). Three
  pinning tests get `router_stats` added in alphabetical position;
  shallow-copy invariant for `extra` is mirrored by a deep-copy
  invariant for `router_stats` (nested `per_signal_*` dicts can't
  bleed back to the frozen dataclass).
- Regenerated `docs/savings.json` (16 lines added, `null` on four
  rows + populated dict on the router). `docs/savings.md` is
  unchanged — `_format_markdown` renders the `extra` column only,
  so the README table is also unchanged.
- New acceptance tests: `router_row_carries_router_stats` (six
  expected keys, cross-check against `extra.escalated`, single-signal
  lock on `per_signal_measured.entropy == 500` and `per_signal_trips
  .entropy == extra.escalated`), `non_router_rows_have_null_router_stats`
  (four non-router strategies must have `router_stats is None`).
- Architecture doc gets a matching `#64` bullet under the runtime layer
  section after the `#62` bullet, documenting the JSON-now /
  dashboard-panel-later split.

**Why this work, this session:** closes the explicit "Next session"
follow-up from PR #63's memory entry, phase one of two phases of
the dashboard plumbing. Phase two (a dedicated `st.dataframe` panel
for per-signal breakdown) is a follow-on issue — keeping the JSON
expansion small and reviewable on its own. Continues the multi-issue
loop in this session (third issue closed) — third sibling of the
sink-parity arc across portfolio-ops #52, rag-production-kit #60,
and now llm-cost-optimizer #64.

**Open questions / blockers:** none. 331 → 334 pytest passes. PR #65
open and ready.

**Next session:** file the follow-on issue for the dedicated dashboard
panel rendering `router_stats.per_signal_trips` and
`router_stats.per_signal_measured` as a small `st.dataframe` alongside
the existing cache-savings panels. Currently visible via the `Raw
JSON` expander only.

## 2026-06-19 — Issue #66: Per-signal router escalation panel in the dashboard
**Duration:** ~35 min · **Branch:** `session/2026-06-19-issue-66`

- Added `_pick_router_row(payload)` and `_router_panel_rows(router_stats)` to `dashboard/app.py` — two small pure helpers split out of `main()` so they're unit-testable without Streamlit's runtime. `_pick_router_row` is structural (`router_stats is not None`), not lexical, so relabeling the bench's router doesn't break the panel.
- `_router_panel_rows` emits one row per signal in the union of `per_signal_trips ∪ per_signal_measured`, sorted alphabetically. Columns: `signal`, `trips`, `measured`, `trip_rate`. `trip_rate` defaults to `0.0` when `measured == 0` so a signal that was wired up but never reached (earlier signal short-circuited) doesn't `ZeroDivisionError`.
- New `Router per-signal escalation` subheader inserted between `Quality maintained?` and `Per-strategy details`. When no row has `router_stats` (pre-#64 hand-rolled artifact), falls back to `st.info` rather than crashing.
- 7 new tests behind the existing `importlib.util.find_spec("streamlit"|"pandas")` skip pattern: structural-not-lexical row pick, sorted output, zero-division guard, signal-in-only-one-dict union behavior, and a cross-check against the committed `docs/savings.json` (single-signal entropy lock matching #64's contract test).
- Two CI iterations needed before clean: first `ruff check` caught a `PT018` compound assert in the union-test (split into 4 asserts), then `ruff format --check` flagged one block needing reformat (auto-fix + commit). Third push clean across lint, test ×2, memory-check.

**Why this work, this session:** explicit follow-on hint from PR #65's memory entry. Closes the second phase of the JSON-now-dashboard-panel-later split — the per-signal breakdown is now one scroll away from the dollar columns instead of hidden in the `Raw JSON` expander. Second substantive close of the day-session multi-issue loop after rag-production-kit #60 (PR #61 lint fixup).

**Open questions / blockers:** none. 335 → 342 pytest passes. PR #67 merged into main.

**Next session:** the dashboard observability story for the router is now complete for single-signal config. Per the issue body's out-of-scope, multi-signal config in the bench (so the panel can demonstrate attribution power with a real second signal) is a separate issue worth filing — it'd need a second signal in the routing config that doesn't pollute the snapshot. Per-signal *dollar* attribution is also a separate issue (the bench would need to track which signal caused each escalation row).
