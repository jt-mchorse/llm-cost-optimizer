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

## 2026-06-22 — Issue #69: keep EntropySignal defensive on duck-typed responses
**Duration:** ~25 min · **Branch:** `session/2026-06-22-0340-issue-69`

- Found during Phase A code-reading: `_extract_first_token_logprobs` advertises itself as defensive ("returns None for anything else"), but its nested-SDK-shape branch did `getattr(top, "top_logprobs", None) or top.get("top_logprobs")`. When `top` was a plain object with neither the attribute nor a `.get` method, the `.get` call raised `AttributeError`, crashing `EntropySignal.measure` and aborting the whole `UncertaintyRouter.route()` — the opposite of the router's documented "couldn't measure → fall through to the next signal" contract.
- Fix: added a small `_read_field(obj, name, default)` helper that reads an attribute first, then a dict key only when `obj` is actually a `dict`, else returns the default — never calling `.get` on a non-dict. Routed both `top_logprobs` and the per-entry `logprob` extraction through it (the per-entry `v.get("logprob")` one line down had the identical latent crash). Removed the now-unneeded `# type: ignore[union-attr]`.
- 3 new tests: object-typed `top_logprobs` shape, bare-object → `None`, and an end-to-end `route()` that falls through from a logprob-less entropy signal to a tripping judge signal. Suite 342 → 345, ruff clean. PR #70 ready.

**Why this work, this session:** the portfolio is saturated; this was a real `AttributeError` crash in the production routing path (not a synthetic API-completeness fill). I explicitly declined the alternative — adding `from_dict` readers to the three telemetry `to_dict` classes — because nothing hand-rolls typed reconstruction from those JSON artifacts (the dashboard reads them via `.get()` chains by design), so that work would have been busywork.

**Open questions / blockers:** none.

**Next session:** `EntropySignal.threshold` default carries a comment (`1.5  # ~3 plausible tokens with equal mass`) that's numerically off — 1.5 nats ≈ 4.5 equal-mass tokens, ln(3) ≈ 1.10. Cosmetic; low-pri comment fix if a future session needs filler in this repo.

## 2026-06-22 — Issue #71: pricing — reject non-finite rates/multipliers
**Duration:** ~25 min · **Branch:** `session/2026-06-22-1102-issue-71`

- Found during Phase A code-reading: `ModelPricing.__post_init__` validated rates/multipliers with a sign-only check (`value < 0.0`). But `NaN < 0.0` and `inf < 0.0` are both `False`, so a non-finite rate slipped through and poisoned `cache_wrapper._dollars_saved` — a NaN rate makes savings NaN, +Inf makes them Inf — propagating silently into the aggregate and the savings dashboard with no diagnostic. This is the exact finiteness-sweep gap (#36) already closed on `SemanticCache.default_ttl_s` and the router thresholds; pricing was missed.
- Fix: widened the guard to `math.isfinite(value) or value < 0.0` with a "must be a finite number >= 0.0" message. Added `tests/test_pricing.py` (18 tests) and updated the one existing test that asserted the old wording.
- Full suite 359 → 363, ruff clean. PR #72 ready.

**Why this work, this session:** the portfolio is saturated (only binary demo-capture tasks left open). This was a real silent-corruption bug in the savings-math path, found by reading `pricing.py` against the documented #36 sweep — strictly higher value than a synthetic fill.

**Open questions / blockers:** none.

**Next session:** two low-pri leads in `router.py` — (1) `JudgeConfidenceSignal.measure` collapses a missing `verdict.score` to `0.0` via `or 0.0`, which *trips* (escalates) rather than reporting "couldn't measure" (`None`), inconsistent with the EntropySignal contract; latent only because the real eval-harness `Judge` always returns a valid float. (2) The `EntropySignal.threshold=1.5  # ~3 plausible tokens` comment is numerically off (1.5 nats ≈ 4.5 equal-mass tokens; 3 tokens ≈ 1.10 nats). Cosmetic.

## 2026-06-22 — Issue #73: router — "couldn't measure" on missing/non-finite judge score
**Duration:** ~25 min · **Branch:** `session/2026-06-22-1511-issue-router-confidence`

- Acted on the explicit next-lead from the #71/#72 session: `JudgeConfidenceSignal.measure` used `float(getattr(verdict, "score", None) or 0.0)`. The `or 0.0` collapsed a missing/None judge score to `0.0`, which then tripped (`0.0 < threshold`) and silently escalated **every** request to the expensive model — the opposite of a cost optimizer's purpose. Reachable because the signal is duck-typed (D-002): any object satisfying the `judge.score` contract is accepted, so a judge returning a malformed verdict is a real possibility, not just the eval-harness `Judge` that always returns a valid float.
- Fix: read the raw score; missing/None → `SignalReading(value=None, trip=False)` ("couldn't measure", matching the EntropySignal and empty-text guards); non-finite (NaN/inf) → same (consistent with the #36/#71 finiteness sweeps); a genuine finite `0.0` is a real measurement and still trips.
- 6 new tests (missing `.score` attr, `score=None`, parametrized NaN/inf/-inf, and a genuine-`0.0`-still-trips regression guard). Verified the 5 failure-mode tests fail pre-fix and the 0.0 guard passes pre-fix. Suite 363 → 369, ruff clean. PR ready.

**Why this work, this session:** the portfolio is saturated (only `priority:low` demo-capture tasks open). This was a real correctness bug in the cost-routing decision path, already documented as the next lead in prior memory — strictly higher value than a synthetic fill.

**Open questions / blockers:** none.

**Next session:** the cosmetic `EntropySignal.threshold = 1.5  # ~3 plausible tokens` comment is numerically off (filed as #74, priority:low). No other specific lead in `router.py` — signals and the router are now well-hardened.

## 2026-06-22 — Issue #74: router — correct the EntropySignal threshold comment
**Duration:** ~20 min · **Branch:** `session/2026-06-22-2310-issue-74`

- Closed the cosmetic lead that prior sessions (#71/#72/#73) kept carrying forward. The `EntropySignal.threshold = 1.5` comment claimed "~3 plausible tokens with equal mass", but Shannon entropy of N equal-mass tokens is `ln(N)` nats, so 1.5 nats ≈ `e^1.5 ≈ 4.48` tokens. At 3 equal-mass tokens entropy is only `ln(3) ≈ 1.10` — below the threshold, so it doesn't trip. The repo's own existing tests already encoded this (3 tokens → no trip, 5 tokens → trip); the comment just contradicted them.
- Fix: comment now reads "~4-5 equal-mass tokens (e^1.5 ≈ 4.48; ln4≈1.39, ln5≈1.61)". The threshold value stays 1.5 — it's what the README quickstart and the defaults-snapshot test reference, so re-tuning it would be a separate behavior-change decision, out of scope for a cosmetic issue.
- Added `test_entropy_default_threshold_maps_to_four_to_five_equal_mass_tokens`, which pins the corrected claim to executable math (1.5 ∈ (ln4, ln5); a 4-token uniform doesn't trip, a 5-token one does) so the comment can't silently drift again. Suite 369 → 370, ruff clean. No behavior change. PR #76 ready.

**Why this work, this session:** the portfolio is saturated (remaining open issues are `priority:low` demo-capture tasks that need screen recording, not doable headless). This was the last documented `router.py` lead — a real doc-accuracy defect on a parameter operators tune, with a test that locks the math.

**Open questions / blockers:** none. `router.py` signals and the router are now well-hardened with no remaining known leads.

**Next session:** no specific `router.py` lead remains. Future substantive work in this repo will need a fresh dogfood sweep (cache layers, batch, savings dashboard) since the issue tracker is down to `priority:low` demo-capture binaries.

## 2026-06-23 — Issue #77: RedisStorage drops stale tag memberships on re-put
**Duration:** ~25 min · **Branch:** `session/2026-06-23-0321-issue-77`

- Fixed a backend-parity bug in the semantic cache. `RedisStorage.put` recorded tag membership additively (`sadd`) and never pruned tags a re-put record no longer carried. So reclassifying a cached entry — same prompt/model (same key), different tags — left the old tag's Redis set still pointing at the key. A later `invalidate_by_tag` on that lost tag then wrongly evicted the record. `InMemoryStorage` doesn't have this problem because its `put` replaces the whole record. The fix loads any existing record and `srem`s the dropped tags before the additive write.
- Added a `fakeredis` parity test (`put a:legal → put a:urgent → invalidate_by_tag('legal')` must drop 0 and keep the entry, then `invalidate_by_tag('urgent')` drops it). Verified red pre-fix, green post-fix. Suite 370 → 371, ruff clean.

**Why this work, this session:** found by the night session's Phase A parallel dogfood sweep; it's a real silent-data-loss path (wrong cache eviction) on the production Redis backend, reachable through the public `SemanticCache.put(..., tags=...)` API.

**Open questions / blockers:** none.

**Next session:** dangling tag-set entries after a multi-tagged record is invalidated are benign (record's gone, count stays correct) and were deliberately left out of scope. No other known semantic-cache lead.

---
## 2026-06-23 — Issue #79: bench_savings crashed on an empty (--n 0) workload
**Duration:** ~20 min · **Branch:** `session/2026-06-23-0415-issue-79`

- Fixed `run_bench(n=0)`, which crashed at three sites the empty path never exercised: `ZeroDivisionError` on the semantic-cache `hit_rate` and router `escalation_rate` divisions (the sibling `mean_quality` divisions were already guarded), and `ValueError` from the batch backend's empty `submit()`.
- Guarded the two divisions with `if n else 0.0` and short-circuited `_run_batch` to a trivial zero result on an empty workload. Added an `n=0` smoke test. Red pre-fix, green post-fix. Suite 370 → 371, ruff clean.

**Why this work, this session:** found by a different-angle second pass in the night session's Phase A dogfood wave; the reported two divisions were only part of it — fixing them revealed the batch-submit crash, so the PR makes the whole `n=0` path complete rather than partial.

**Open questions / blockers:** none.

**Next session:** `AnthropicBatchBackend.poll` reading a never-set `_idempotency_key` (informational, real-API-only) is a separate low-pri lead.

## 2026-06-23 — Issue #81: SignalReading didn't enforce its value=None ⟹ not-trip contract
**Duration:** ~20 min · **Branch:** `session/2026-06-23-1920-issue-81`

- A Phase A dogfood sweep of the router path found that `SignalReading` documents `value=None` as "couldn't measure" and the router counts only non-None readings in `per_signal_measured`, but nothing stopped a custom `EscalationSignal` from returning `value=None, trip=True`. That would increment `per_signal_trips` without `per_signal_measured`, breaking the `trips ≤ measured` invariant and the dashboard's `trip_rate`.
- Added a `__post_init__` guard rejecting that combination, matching the module's existing contract-tightening guards. The built-in signals already return `trip=False` when they can't measure (this had been fixed reactively once for judge-confidence); this enforces it for all signals at the type boundary. Suite 373 → 376, ruff clean.

**Why this work, this session:** third of three parallel dogfood finds across priority-tier repos in this DAY session; the only open `priority:high` issues elsewhere were operator-blocked or `decision-revisit` security work.

**Open questions / blockers:** none.

**Next session:** none specific to this issue.

## 2026-06-23 — Issue #83: BatchCostQuote had no validation; a negative/NaN rate silently corrupted the savings dashboard
**Duration:** ~25 min · **Branch:** `session/2026-06-23-2317-issue-83`

- A Phase A dogfood code-read of the batch cost path found that `BatchCostQuote` — the batch-axis equivalent of `ModelPricing` — had no `__post_init__` at all, while `ModelPricing` (#71) and its siblings `BatchRequest` / `BatchResultRow` all validate their numeric fields. A negative or non-finite `input_per_mtok` / `output_per_mtok` was freely constructible.
- Reproduced: a `-15.0` rate makes `compare_realtime_vs_batch` return `realtime_usd=-15.0, savings_usd=-7.5` (sign inverted); a `NaN` rate makes the dollar fields `NaN`. In both cases the `savings_pct … if realtime_total > 0 else 0.0` guard masks the percentage to a clean `0.0`, so the garbage dollars land on the savings dashboard (a headline deliverable) with no diagnostic.
- Added a `__post_init__` rejecting a non-string/empty `model` and a non-finite or negative rate, mirroring `ModelPricing.__post_init__`. Added `import math` (not previously imported in `batch.py`). 15 new tests (negative + NaN/±Inf on each field, empty/non-string model, zero-rate boundary, clean end-to-end flow), red pre-fix / green post-fix. Suite → 391, ruff clean.

**Why this work, this session:** priority-tier repo, next in build sequence after the iteration-1 fix in `llm-eval-harness`; the only `priority:high` issues elsewhere were operator-blocked or `decision-revisit` work deferred to JT. Continues this repo's "no invented/garbage numbers" arc (D-003 / #71) by closing the one cost-rate dataclass that never got the finiteness guard.

**Open questions / blockers:** none.

**Next session:** the dataclass boundary is the right choke point — a constructed `BatchCostQuote` is now trustworthy downstream, so no re-validation was added inside `compare_realtime_vs_batch`.

---
## 2026-06-24 — Issue #85: per-call put(ttl_s) was unvalidated while default_ttl_s wasn't
**Duration:** ~22 min · **Branch:** `session/2026-06-24-0326-issue-85`

- `SemanticCache.put(..., ttl_s=...)` — the per-call override that takes precedence over `default_ttl_s` — applied no finiteness/positivity check, while the constructor validates `default_ttl_s` (#36). A negative `ttl_s` stored an already-expired entry (silently evicted on the next lookup → cache degrades to full bypass with no diagnostic); a NaN/Inf corrupted `expires_at`.
- Added the same guard to `put`, raising the same descriptive ValueError. `ttl_s=None` still falls back to the default.
- 6 new tests (non-positive rejected, NaN/±Inf parametrized, None-fallback, valid-positive store/expiry lifecycle). Red via `git stash`, green after. Suite 391 → 397, ruff clean.

**Why this work, this session:** llm-cost-optimizer was the next priority-tier repo by build-sequence tie-break; pricing/batch/router were already saturated, so a parallel dogfood sweep of the cache modules surfaced this asymmetric-validation gap.

**Open questions / blockers:** none.

**Next session:** cache_wrapper.py and io_utils.py are the remaining dogfood frontier in this repo if picked again.

---
## 2026-06-24 — Issue #87: non-finite embedding silently disabled the semantic cache
**Duration:** ~25 min · **Branch:** `session/2026-06-24-1523-issue-87`

- A BYO embedder (the documented `Embedder` Protocol seam) returning a NaN/Inf component flowed unvalidated into `cosine()`, making every similarity `nan`. Because `nan >= threshold` is always False, an identical prompt that must be a cache hit was reported as a miss — the cache silently went fully bypassed, the model was re-invoked on every lookup (lost savings), and `nan` leaked into `similarity`/`hit_rate`.
- Added a `_validate_embedding` seam guard in `lookup` and `put` (reject-loud with a `ValueError` naming the index, matching the module's ttl guards #36/#85 and the sibling prompt-regression-suite #67), plus a defense-in-depth `cosine()` finiteness fallback so `find_nearest` can't be poisoned by a `nan`. 8 tests, red-without / green-with, full suite + ruff clean.

**Why this work, this session:** found via a Phase A dogfood sweep and reproduced end-to-end; llm-cost-optimizer was next in build sequence among the priority tier (D-009) after llm-eval-harness this run.

**Open questions / blockers:** none.

**Next session:** rescaling vectors by max-abs component for an exact `1.0` similarity on huge-but-finite vectors is a separate, lower-value concern (the `cosine` finiteness fallback is the minimal consistent fix).

---
## 2026-06-25 — Issue #89: add current-frontier model pricing (opus-4-8, fable-5)
**Duration:** ~30 min · **Branch:** `session/2026-06-25-1513-issue-89`

- The `_PRICING` table couldn't price `claude-opus-4-8` — the model new Anthropic SDK work defaults to — so `get_pricing` raised `UnknownModelError` for it, and `PromptCacheWrapper` callers on the default model couldn't compute `dollars_saved` without hand-wiring a `ModelPricing`. `claude-fable-5` was also absent.
- Added both at their currently published input rates (`claude-opus-4-8` $5.00/MTok, `claude-fable-5` $10.00/MTok) with the documented ephemeral-cache multipliers (1.25× write / 0.10× read, the `ModelPricing` field defaults). Source cited in the commit (Anthropic published-pricing reference, current-models table dated 2026-06-04). Purely additive — no existing entry, README text, or benchmark output changed. 5 tests, red-without / green-with, 405 → 410 suite green, ruff clean.
- Found while scoping: the existing `opus-4-7`/`opus-4-6` entries at $15.00 are stale (current family rate is $5.00). Left unchanged here because `opus-4-7` is the escalation target in `bench_savings.py`, so correcting it regenerates the savings benchmark and its README snapshot. Documented the staleness inline and filed #90 (priority:med) for that deliberate, benchmark-regenerating refresh.

**Why this work, this session:** llm-cost-optimizer was the only priority-tier repo past the 18h freshness floor (D-009) this run; its sole open issue (#18, a human-blocked demo capture) wasn't autonomously completable, so a substantive, real, sourced pricing gap was the right scoped issue.

**Open questions / blockers:** none — #90 tracks the stale-price refresh.

**Next session:** #90 — refresh opus-4-7/4-6 to $5.00 and regenerate the savings benchmark deterministically.

---
## 2026-06-25 — Issue #90: refresh stale opus-4-7/4-6 pricing + regenerate savings bench
**Duration:** ~30 min · **Branch:** `session/2026-06-25-1915-issue-90`

- `claude-opus-4-7` and `claude-opus-4-6` were still listed at $15.00/MTok input while `claude-opus-4-8` was added at $5.00 in #89 — internally inconsistent, since the whole Opus 4.6/4.7/4.8 family is $5 in / $25 out on the current Anthropic published-pricing reference (current-models table, cached 2026-06-04). Refreshed both to $5.00 and removed the stale-price note #89 had left inline.
- `opus-4-7` is the `STRONG_MODEL` escalation target in `scripts/bench_savings.py`, so the price change regenerated the savings benchmark. Re-ran the hermetic dry bench (`python scripts/bench_savings.py --dry --out docs/savings`); only the uncertainty-router row moved (it is the sole strategy that escalates to the strong model): $0.1469 spent / -154.8% → $0.0874 spent / -51.6%, quality unchanged at 0.921. Output is deterministic and idempotent (two consecutive dry runs are byte-identical).
- Updated the README in four places (benchmark table router row, the snapshot-locked opus-4-7 input-price quote, the "+spend" prose figure, and the illustrative `BatchCostQuote` example $15/$75 → $5/$25 for consistency) and the three snapshot/assertion tests to the regenerated truth. No fabricated numbers — every figure comes from the dry bench.

**Why this work, this session:** documented next step from #89 (filed #90 in the same run that merged #89's pricing PR); llm-cost-optimizer was the next priority-tier repo in build sequence after llm-eval-harness this multi-issue day session.

**Open questions / blockers:** none.

**Next session:** the only open llm-cost-optimizer issue left is #18 (demo capture), which is human-blocked (needs a screen recording).

---
## 2026-06-25 — Issue #93: abstain when a top_logprobs entry lacks its logprob
**Duration:** ~20 min · **Branch:** `session/2026-06-25-2329-issue-93`

- `_extract_first_token_logprobs` defaulted a missing per-entry `logprob` to `0.0` — but `0.0 == log(1.0)` is a fabricated probability-1.0 (maximal-certainty) token, not a neutral sentinel. That phantom entry flows into `_shannon_entropy_nats`, which normalizes `exp(lp)` across the distribution, so it skews the entropy and the `trip = entropy >= threshold` escalation decision built on it. The function's docstring promises a defensive `None` for malformed shapes, and `EntropySignal.measure` already maps `None ⟹ value=None, trip=False`.
- Fix: read each entry's `logprob` with no default and return `None` (abstain) if any entry lacks the field; a *present* `0.0` is preserved. Two tests (missing-field abstains, present-0.0 preserved); red-green verified (without the fix the malformed case returns `[-0.693, 0.0]`). Full suite green, ruff clean. Same defensive class as #82 (value=None⟹not-trip), #73 (missing judge score), #69 (defensive `_read_field`).

**Why this work, this session:** second issue of a multi-issue DAY session; after llm-eval-harness #98 the priority-tier loop returned to llm-cost-optimizer. A strict dogfood audit of the router surfaced the one remaining signal-extraction path that fabricates rather than abstains.

**Open questions / blockers:** none.

**Next session:** the entropy/judge signal paths now uniformly abstain on missing/malformed data; future router work is more likely on routing policy than signal extraction.

## 2026-06-26 — Issue #98: Semantic-cache degenerate-input false positives across model/content
**Duration:** ~25 min · **Branch:** `session/2026-06-26-2318-issue-98`

- `HashEmbedder.embed` returned a *constant* vector (`vec[0] = 1.0`) whenever the input produced zero n-grams. Since `_scoped_prompt` prepends `[model=X] `, that degenerate branch fires for an empty/whitespace prompt at the default `ngram=2`, and for *any single-word prompt* at `ngram>=3`. The constant vector ignored both model id and content, so every such input collided at cosine 1.0 — a silent false-positive cache hit that returned one model's response to a different model's caller (and one prompt's response to a different prompt). Reproduced both cases on main.
- This defeats D-005 (model-scoped entries) and D-006/D-007 (false positives are user-visible bugs; the 0.95 default exists to avoid them). The existing D-005 test only used a multi-word prompt, so it never exercised the degenerate branch.
- Fixed by seeding the single non-zero slot from a SHA-256 hash of the full scoped text. The vector stays unit-length, two identical degenerate inputs still collide (a correct hit), but different model/content land in different slots and miss. 4 regression tests; suite 417 → 421, ruff clean.

**Why this work, this session:** second issue of a multi-issue DAY run. After closing llm-eval-harness #105 I rotated to the next priority-tier repo in build sequence (llm-cost-optimizer), which had no open backlog, so I dogfooded it with an Explore agent and filed #98 from a reproduced finding.

**Open questions / blockers:** none.

**Next session:** two dogfood runners-up remain unfiled — `compare_realtime_vs_batch` rounds the three dollar figures independently (sub-cent `savings_usd != round(realtime - batch)`), and `_shannon_entropy_nats` raises `OverflowError` on a finite-but-large positive logprob. Both are low value (cosmetic / logprobs are realistically <= 0); file if a session needs small work here.

## 2026-06-27 — Issue #100: capture_demo --no-open was dead on the default path
**Duration:** ~20 min · **Branch:** `session/2026-06-27-0413-issue-100`

- `--no-open`'s help documents "default is to open the URL once STAGE 2 begins", but the only `webbrowser.open(DASHBOARD_URL)` call was nested inside the `--launch-streamlit` success branch. So on the default invocation (no `--launch-streamlit`) the browser was never opened and `--no-open` controlled nothing — the documented default was unreachable. Reproduced: a default `main(...)` run never called `webbrowser.open`.
- Hoisted the open to STAGE 2 top-level (guarded by `--no-open`), keeping the `time.sleep(2.0)` grace period inside the auto-launch branch and removing the now-duplicate open (no double-open). Chose code-matches-docs because the help text unambiguously documents open-by-default and the demo flow assumes a running dashboard. Added 2 tests (default opens; `--no-open` suppresses) via monkeypatched `webbrowser.open`; the existing smoke tests pass `--no-open` so they're unaffected. Suite 421 → 423, ruff clean.

**Why this work, this session:** eleventh issue of a multi-issue NIGHT run; surfaced by a second-pass dogfood of priority-tier llm-cost-optimizer (first pass clean) on the demo orchestrator.

**Open questions / blockers:** none.

**Next session:** the demo capture honors `--no-open` on every path; a minor cheat-sheet wording nit (STAGE 1 "regenerated docs/savings.json" vs the actual demo-artifacts path) remains unfiled, low value.

## 2026-06-27 — Issue #102: Semantic-cache single-bigram collision serves wrong content
**Duration:** ~30 min · **Branch:** `session/2026-06-27-1917-issue-102`

- A Phase A dogfood of the priority-tier repos surfaced a real correctness bug: `HashEmbedder.embed` turns a one-word prompt into the 2-token scoped string `[model=m] word`, which yields a single-slot unit vector over only 128 slots. Distinct one-word prompts collided at cosine 1.0, so the cache served the wrong prompt's cached response — the same D-006/D-007 false-positive class that #98 fixed for the zero-ngram branch, in the adjacent one-ngram branch.
- Fixed by blending several independent content slots (from disjoint 4-byte windows of the scoped text's SHA-256) into any single-occupied-slot vector, so a false hit now needs every window to collide at once. Identical inputs keep cosine 1.0; multi-slot vectors are untouched. A first single-extra-slot attempt failed (for a one-word prompt the bigram string equals the scoped text, so reusing the same hash bytes reproduces the colliding slot) — the disjoint-window approach is what drove false hits to zero across a 500-prompt stress test.
- 3 lock tests, all verified to fail on the pre-fix code; full suite green (419), ruff clean.

**Why this work, this session:** first issue of a multi-issue DAY run; the only actionable priority/med issues were decision-revisit JT-blockers (mcp #54/#55, llm-cost-optimizer #97), so the saturated-state dogfood pattern surfaced this real correctness bug in a priority-tier repo.

**Open questions / blockers:** none.

**Next session:** rag-production-kit has a low-severity rewriter double-terminator bug (`Who is the CEO.?`) found in the same sweep — candidate next issue this run.

## 2026-06-28 — Issue #104: `_make_key`'s space-delimited concatenation collided distinct (model,prompt) pairs
**Duration:** ~25 min · **Branch:** `session/2026-06-28-1922-issue-104`

- `SemanticCache._make_key` hashed a bare `f"{model} {prompt}"`. The space delimiter lets the model/prompt boundary slide, so distinct pairs like `("b c","a")` and `("c","a b")` produced the same key. Storage keys records by `record.key`, so the second `put` silently overwrote the first — a lost cache entry and a D-005 model-scoping violation. Reproduced firsthand: both keys `0e9f64031fcb2bc7`, store size 1 instead of 2.
- The embedding layer already used the unambiguous `_scoped_prompt` form (`[model=...] prompt`, hardened in #98/#102); the key layer was the remaining inconsistency. Fixed by hashing `self._scoped_prompt(prompt, model)` in `_make_key` so only a genuinely identical `(model, prompt)` collides; D-005 isolation preserved and keys stay opaque (no on-disk migration). Added 3 regression tests; suite 429 passed, ruff clean.

**Why this work, this session:** second substantive issue of a multi-issue DAY run. Rotated off llm-eval-harness (where #116 landed) to the next priority-tier repo in build sequence to avoid same-repo append-only MEMORY conflicts. Phase A found no mergeable PRs and a clean audit, so a dogfood sweep on cost_optimizer surfaced this. Two weaker dogfood findings deferred (negative `CacheTelemetry` token counts; `scripts/tune_threshold.sweep([])` ZeroDivisionError — not CLI-reachable). Left #97 (batch-idempotency decision-revisit) for JT.

**Open questions / blockers:** none.

**Next session:** continue the loop — rotate to another repo.

## 2026-06-28 — Issue #106: direct logprob path crashed on a None logprob instead of abstaining
**Duration:** ~18 min · **Branch:** `session/2026-06-28-2320-issue-106`

- `_extract_first_token_logprobs` documents "returns None for anything else so signals can stay defensive," and the nested SDK path was hardened (#94) to abstain on a missing logprob. But the direct `first_token_logprobs` path ran `float(v)` over every element before any validation, so a present-but-`None` logprob raised a raw `TypeError` that escaped `EntropySignal.measure` and `UncertaintyRouter.route()` — aborting the request rather than abstaining.
- Added a `None`-guard before the `float()` conversion, a tight mirror of the nested path's #94 fix, so both paths handle identical bad input the same way (`value=None ⟹ trip=False`). One regression test mirroring the nested-path test for the direct path. Suite 429 → 430, ruff check + format clean.

**Why this work, this session:** second issue of a multi-issue DAY run, rotating off rag-production-kit (#98) to another priority-tier repo. Surfaced by a Phase A dogfood sweep as the direct-path completion of the #94 nested-path abstain fix.

**Open questions / blockers:** none.

**Next session:** continue the loop — rotate to another repo. Still untouched and JT-bound: #97 (batch-idempotency decision-revisit) and #18 (demo capture).

## 2026-06-29 — Issue #108: architecture.md named nonexistent symbols
**Duration:** ~9 min · **Branch:** `session/2026-06-29-0406-arch-symbol-names`

- `docs/architecture.md` named `BatchAPIBackend` (no such class; the offline backend is `AnthropicBatchBackend`) and `EscalationSignal.evaluate` (the Protocol method is `measure`). Both corrected. Independent of the JT-blocked #97.

**Why this work, this session:** twelfth issue of the night run, from the second parallel doc-contract subagent batch. Same arch-doc-test gap class as embedding-model-shootout #71, but the doc uses bare class names (not fully-qualified), so the dotted-symbol lock test from #71 doesn't transfer cleanly here.

**Open questions / blockers:** none.

**Next session:** architecture.md references only real `cost_optimizer` symbols.

## 2026-06-29 — Issue #110: semantic cache's zero-ngram path emitted a single-slot vector — empty/degenerate prompts false-hit across models
**Duration:** ~30 min · **Branch:** `session/2026-06-29-1921-issue-110`

- `HashEmbedder.embed` has two degenerate paths. The single-bigram path was hardened in #102 to blend 4 independent slots from disjoint SHA-256 windows (+ L2-normalize), escaping the single-slot 1/128 birthday collision. The zero-ngram path (`if not ngrams:`, semantic_cache.py:96-98) — empty/whitespace prompts at the default ngram=2, single-word prompts at ngram≥3 — was left returning the original #98 single-slot basis vector early, bypassing both the blend and the normalization. So distinct degenerate inputs collided at cosine 1.0 and returned the wrong model's / wrong prompt's cached response, violating model scoping (D-005) and the false-positives-are-bugs contract (D-006/D-007). The branch's own comment even acknowledged the single-slot collision risk, but the #98 fix it described still used one slot.
- Reproduced firsthand: an empty-prompt `put` under model `m2` then a `lookup` under a never-inserted model `m55` returned `m2`'s response at similarity 1.0; a 500-model birthday stress gave **488/500** cross-model false-positive hits.
- Fixed by applying the same 4-disjoint-window blend the single-bigram path uses to the zero-ngram branch and removing the early single-slot return so flow falls through to the existing L2-normalize. Identical degenerate inputs still map to identical slots (correct hit preserved); a false hit now needs all 4 windows to collide (~`(1/128)^4`). Post-fix the birthday stress is 0/500, the same-model empty prompt still hits at 1.0, and ngram=3 single-word wrong-content hits are 0/300. 2 lock tests added (a 500-entry empty-prompt bulk stress mirroring the #102 lock + an embedder-level multi-slot structural assertion), both confirmed failing on pre-fix code. Suite 430 → 432, ruff check + format clean.

**Why this work, this session:** second substantive issue of a multi-issue DAY run, after #120 in `llm-eval-harness`. Rotated to priority-tier `llm-cost-optimizer` (build-sequence pos 2); its only open issues are #97 (batch-idempotency decision-revisit, deliberately left for JT) and #18 (priority:low demo capture), so a dogfood hunter — steered clear of the #97 area — surfaced this latent embedder collision, the zero-ngram sibling of the #98/#102 family.

**Open questions / blockers:** none. #97 still awaits JT.

**Next session:** continue the loop on another repo to avoid same-repo append-only MEMORY conflicts.

## 2026-06-30 — Issue #112: _extract_text crashed on an SDK text block with text=None
**Duration:** ~18 min · **Branch:** `session/2026-06-30-0321-issue-112`

- `_extract_text` (`router.py:357`) guarded its direct path with `isinstance(direct, str)` but the SDK-shape branch did not: `parts = [getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text"]`. A truncated/malformed SDK `type="text"` block carrying `text=None` put `None` into `parts`, so `"".join(...)` raised a raw `TypeError` that escaped `JudgeConfidenceSignal.measure` and `UncertaintyRouter.route()`, aborting the whole request instead of abstaining.
- Fixed by filtering non-`str` `.text` values in the SDK branch, mirroring the direct-path guard. A `text=None` block now contributes nothing → empty text → `measure` abstains via its existing empty-text guard (`SignalReading(value=None, trip=False)`); a `None` between two valid blocks is dropped, not fatal (`["a", None, "b"]` → `"ab"`). Extends the "abstain, don't crash on malformed SDK shapes" contract already applied to the logprob extractor (#94/#106).
- Two lock tests (abstain on None-only; over-rejection guard on None-between-valid), both confirmed failing pre-fix via `git stash` (raw `TypeError`). Suite 432 → 434, ruff clean.

**Why this work, this session:** third issue of a NIGHT multi-issue run; a dogfood hunter surfaced this in priority-tier `llm-cost-optimizer`, reproduced firsthand before acting. Unrelated to the JT-blocked #97.

**Open questions / blockers:** none — ready for review.

**Next session:** continue the loop.

## 2026-06-30 — Issue #114: a malformed usage token crashed `create()` and destroyed a successful response
**Duration:** ~20 min · **Branch:** `session/2026-06-30-1521-issue-114`

- `_read_telemetry` (`cache_wrapper.py:189-190`) read the cache token counts with a bare `int(getattr(usage, …, 0) or 0)` — *after* `messages.create` had already returned a valid response. The `or 0` tolerated `None`/missing, but a present-but-malformed value crashed the `int(...)`: `int(NaN)` → `ValueError`, `int(inf)` → `OverflowError`, `int("abc")` → `ValueError`. So a telemetry-accounting hiccup destroyed a successful API call — the valid `response` was lost to a raw traceback. Reproduced all three firsthand before acting.
- Fixed by extracting `_coerce_token_count(value)` (used for both `write` and `read`): a single `try/except (TypeError, ValueError, OverflowError)` around `int(value or 0)` plus a `>= 0` clamp. A finite, non-negative numeric (incl. a numeric string like `"5"`) still coerces to `int` unchanged; `None`/`NaN`/`inf`/`-inf`/negative/non-numeric abstain to `0`. `create()` now always returns its response; a bad usage field just yields zero telemetry for that call — the same "abstain, don't crash on malformed SDK shapes" contract as #94/#106/#112.
- 20 tests: the `_coerce_token_count` abstain/over-rejection table plus an end-to-end `test_create_survives_malformed_usage_token` over both fields × {NaN, inf, "abc", −7}. Inverse safety net: reverted only the `_read_telemetry` body (keeping the helper) and the malformed cases failed pre-fix, then passed restored. Suite 434 → 454, ruff clean.

**Why this work, this session:** second issue of a DAY multi-issue run. Switched from `llm-eval-harness` (#126, also shipped this run) to priority-tier `llm-cost-optimizer` per the build sequence to avoid same-repo append-only MEMORY conflicts. Both pre-filed issues are blocked (#97 JT decision-revisit; #18 operator-blocked demo capture), so dogfood→issue→PR: read `cache_wrapper.py` and found the telemetry `int()` seam.

**Open questions / blockers:** none — ready for review. Unrelated to the JT-blocked #97.

**Next session:** continue the loop on another repo.

## 2026-06-30 — Issue #116: cost-comparison report columns didn't reconcile (independent rounding)
**Duration:** ~25 min · **Branch:** `session/2026-06-30-2329-issue-116`

- `compare_realtime_vs_batch` (`batch.py:588`) computed `savings` from the **unrounded** running totals, then rounded `realtime_usd`, `batch_usd`, and `savings_usd` independently to 6 dp. Since `round(a) - round(b) != round(a - b)` at odd half-cents, the three published dollar figures could fail to reconcile — `realtime_usd - batch_usd != savings_usd` — which reads as a math error in a savings report. Reproduced firsthand: 1111 prompt tokens @ \$15/MTok (default 0.5 discount) gives `realtime 0.016665`, `batch 0.008332`, `savings 0.008332`, but `0.016665 - 0.008332 = 0.008333`; a sweep of `1..3999` tokens mismatched on `2000/3999`. Fixed by rounding `realtime_usd`/`batch_usd` first and deriving `savings_usd` and `savings_pct` from those published figures. Per-row math and discount semantics unchanged.
- +2 tests: an odd-half-cent lock and a property check over `prompt_tokens 1..1999` that the columns reconcile; both fail pre-fix. The round-number `known_math` test stays green. Suite 454 → 456, ruff + format clean.

**Why this work, this session:** first issue of a DAY multi-issue run after shipping chunking-strategies-lab #95. The portfolio is in a saturated state (no open priority:high/med code issues), so I dogfood-hunted three priority-tier repos in parallel; two yielded only unreachable findings (a judge-histogram `int(s*10)` that is actually exact for clean decimals, and a `HashEmbedder` zero-norm branch that is dead code), and this one — found by empirical verification — was real. Filed **#116** then fixed it in-session.

**Open questions / blockers:** none — ready for review.

**Next session:** continue the loop on another repo.

## 2026-07-01 — Issue #118: a finite-but-large logprob crashed route() with OverflowError
**Duration:** ~25 min · **Branch:** `session/2026-07-01-2317-issue-118`

- `_shannon_entropy_nats` (router.py) did `math.exp(lp)` per logprob. `math.exp` raises `OverflowError` above ~709.78, so a finite-but-large logprob (a corrupt SDK distribution carrying a positive value — not a valid log-probability) crashed the entropy math, and that `OverflowError` escaped `EntropySignal.measure` and propagated through `UncertaintyRouter.route()`, aborting the entire routing request. That's the exact "abstain/degrade, don't crash on malformed SDK shapes" failure the extractor's #94/#95/#106 guards exist to prevent — reached via a finite value that slips the finiteness guard. Reproduced firsthand (`[710.0, -1.0]` and a stub adapter returning `[800.0]` both raised).
- Fixed by subtracting `max(logprobs)` before `exp` — the standard softmax stabilization. Entropy is taken over the normalized distribution, so subtracting a constant is exactly shift-invariant: bit-identical for valid (≤0) inputs (max |Δ| = 4.4e-16 over 2000 random vectors), but every exponent becomes ≤0 so `exp` never overflows for any finite input. A large-but-finite logprob degrades gracefully (one dominant token → entropy ≈ 0; `[1000,1000]` → ln2). Non-finite logprobs still abstain via the existing #95 guard. +7 test cases (reference-lock, no-overflow parametrized ×4, signal degrades gracefully, route doesn't propagate). Suite 456 → 463, ruff + format clean.

**Why this work, this session:** second issue of a DAY run; `llm-cost-optimizer` was the next stalest priority-tier repo (~24h, build order 2) with only a JT-blocked `decision-revisit` (#97) and a non-headless `[demo]` (#18) open → dogfood hunt. Read the full core surface (router/pricing/semantic_cache/cache_wrapper/batch); this overflow gap was the single reproducible bug found via execution probe.

**Open questions / blockers:** none — ready for review.

**Next session:** continue the loop. #97 (batch idempotency order-sensitivity) remains a JT one-way `decision-revisit`.

## 2026-07-02 — Issue #120: offline FP-rate audit silently polluted cache telemetry
**Duration:** ~30 min · **Branch:** `session/2026-07-02-2315-issue-120`

- `measure_false_positive_rate` (semantic_cache.py) is the sanctioned **offline** false-positive audit (D-007). Its loop calls `cache.lookup`, which increments `cache.stats` (hits/misses/hit_rate). Run against a *populated* cache — the only useful way to measure a real FP rate — it inflated the very `hit_rate` the savings dashboard reports, and optimistically: offline lookups that hit push `hit_rate` up. Reproduced firsthand: a cache with real `hits=1, hit_rate=0.5` became `hits=3, hit_rate=0.75` purely from running the offline diagnostic over two held-out prompts.
- **Why a bug, not a tradeoff:** D-007's stated rationale is that FP measurement is offline *specifically so it doesn't bleed into production* ("online sampling silently bleeds savings; offline helper runs by operator explicit cost"). `hit_rate` is a production economic signal feeding the dashboard (#5); silently inflating it is exactly the bleed D-007 exists to prevent, and it matches this repo's consistent anti-silent-signal-corruption fix class (SignalReading trip invariant, judge missing-score, cosine non-finite, NaN ttl). No existing test locked the current behavior.
- **Fix:** snapshot the `CacheStats` via `dataclasses.replace` on entry, restore it in a `finally` — so the diagnostic reports the FP rate without touching reported telemetry. The `finally` also covers a caller-supplied `call_model`/`equality` that raises mid-measurement. Public signature and return value unchanged. +2 regression tests (stats byte-identical before/after a real hitting measurement; stats restored on a raising `call_model`), both fail pre-fix. Suite 463 → 465, ruff + format clean.

**Why this work, this session:** first issue of a DAY run. `llm-cost-optimizer` was the stalest priority-tier repo (~20h, build order 2) with only a JT-blocked `decision-revisit` (#97) and a non-headless `[demo]` (#18) open → dogfood hunt. Read all six core modules + the bench/tune scripts and ran a parallel adversarial hunt subagent; the repo is exceptionally hardened and this telemetry bleed was the single reproducible defect.

**Note for JT:** an independent hunt agent flagged the same mutation but read it as a *design choice* ("a lookup is a lookup"). I overrode that: D-007 plus the repo's own anti-silent-corruption pattern make it a defect. The fix is reversible/cheap — revert if you disagree.

**Open questions / blockers:** none — ready for review.

**Next session:** continue the loop. #97 (batch idempotency order-sensitivity) remains a JT one-way `decision-revisit`.

## 2026-07-03 — Issue #122: symbol-resolution doc-lock (propagates portfolio-ops #55) (~20 min)

**What got done.** Third per-repo propagation of the portfolio-ops #55 symbol-resolution lock (after llm-eval-harness #140 and rag-production-kit #118). Added `test_doc_symbol_refs_resolve` to `tests/test_architecture_doc.py`: multi-word CamelCase public types the doc names must resolve against the `cost_optimizer` surface **or any submodule**. Two firsthand-verified wrinkles, neither drift: `UnknownModelError` is a real class in `cost_optimizer.pricing` (not `__all__`), so resolution is submodule-aware; `StrategyResult` is a `scripts/bench_savings.py` dataclass (script-owned), exempted via a hard-pinned `EXTERNAL_SYMBOLS` allowlist with a hard-pin test + shadow test. Inverse-verified drift is flagged. Suite 465 → 468, ruff clean.

**Why this work, this session:** fifth issue of the DAY run, continuing the systemic #55 effort. This repo is the clearest demonstration that the propagation is genuinely per-repo: three repos, three distinct resolution schemes so far (fully-qualified only; bare-submodule + surface-only; submodule-aware + external allowlist).

**Open questions / blockers:** none — ready for review.

**Next session:** continue #55 propagation (chunking, prompt-regression-suite, vector-search-at-scale, agent-orchestration-platform, mcp-server-cookbook; TS: nextjs, ai-app — exported-name check).

## 2026-07-05 — Issue #125: semantic cache served cross-model responses for long prompts (D-005 violation)
**Duration:** ~35 min · **Branch:** `session/2026-07-05-1539-issue-125` · **PR:** #126

- D-005 requires model-scoped cache entries so a Haiku response is never served to an Opus caller. But isolation was enforced only through the embedding: `_scoped_prompt` prepends `[model=<id>] ` before embedding, and `lookup` matched via `find_nearest` (pure vector NN, no model filter); `CacheRecord` had no `model` field. For an `n`-token prompt the two scoped vectors have cosine `(n-1)/n`, which crosses the 0.95 threshold at `n ≥ 20` — so a 32-token prompt cached under Haiku was served to an Opus caller (reproduced firsthand, sim 0.9773). The existing model-scoping test only used a 5-token prompt (cosine 0.80), so the long-prompt regime was uncovered.
- Fixed by making model isolation a hard filter: added a `model` field to `CacheRecord` (set in `put`, round-tripped through Redis, default `""` for backward-compat) and gated the lookup hit on `record.model == model`. An identical same-model prompt embeds to ~1.0 and always wins `find_nearest` when present, so rejecting a cross-model nearest is a conservative miss (D-006-aligned), never a wrong-model hit. This fixes the implementation to honor the already-decided D-005 — it does not revisit it; benchmark-neutral. 468 → 469 passing, ruff clean.

**Why this work, this session:** third issue of the DAY loop, and the strongest. Portfolio is deeply saturated (8 empty dogfood hunts across two waves), but a targeted "key/collision" lens — the same lens that surfaced the prompt-regression #107 anchor collision and chunking #108 loader-parity this run — found a real D-005 quality bug in the semantic cache. All three shipped fixes came from that lens or from re-examining hunter-dismissed "design choice" leads against objective invariants.

**Open questions / blockers:** none — ready for review. Optional future enhancement noted in the PR (Protocol-level model filter in `find_nearest`).

**Next session:** correctness surface is saturated; the productive vein is the key/collision + parity lens. The remaining open items are JT-gated decision-revisits (#71 vsas, #97 lco) and display-blocked demo captures.

## 2026-07-06 — Issue #127: ship a PEP 561 `py.typed` marker for `cost_optimizer`
**Duration:** ~20 min · **Branch:** `session/2026-07-06-2317-issue-127` · **PR:** #128

- `cost_optimizer` is heavily type-hinted (6 of 7 modules) and the README markets it as embeddable inside sibling portfolio repos, but it shipped no PEP 561 `py.typed` marker — so any downstream `pip install` + mypy/pyright treated `import cost_optimizer` as untyped and lost every annotation. Added an empty `cost_optimizer/py.typed`, the `Typing :: Typed` trove classifier, and a two-axis regression test (marker is a packaged resource + classifier present). Verified firsthand that `python -m build --wheel` includes the marker (hatchling ships tracked package files by default). 471 passing, ruff clean.

**Why this work, this session:** the correctness surface is saturated — five fresh-lens dogfood hunts this run all came back empty (nextjs config-parity, chunking boundaries, lco cost-arithmetic, prs+aop artifact-drift, ems+vsas artifact-drift, all firsthand-verified). Pivoted to an objective packaging-correctness sweep and found a real distribution defect: all six Python library packages lack a `py.typed` marker despite being heavily typed.

**Open questions / blockers:** none — ready for review.

**Next session:** apply the same `py.typed` fix to `llm-eval-harness` (the flagship imported-by-everything repo — rag-production-kit has a real committed git dep on `eval-harness`, so the gap bites a consumer today). Do NOT PR the marker for ems/vsas/prs/chunking — those aren't imported as libraries by siblings, so it would be sibling-churn.

## 2026-07-07 — Issue #129: Non-strict mypy gate for cost_optimizer
**Duration:** ~40 min · **Branch:** `session/2026-07-07-0315-issue-129`

- Added a non-strict `mypy` gate (`[tool.mypy]` in `pyproject.toml`, `mypy` in the `dev` extra, a step in the `ci.yml` lint job, and `tests/test_mypy_clean.py` locking it) so the annotations shipped via the #127 `py.typed` marker can't silently drift.
- Resolved all 5 `semantic_cache.py` errors — redis-py's `ResponseT = Awaitable[Any] | Any` command-return stub noise — with narrow `cast()`s at each storage-boundary call (the sync `redis.Redis` client never takes the async arm), plus dropped a now-redundant import ignore. Casts stay non-redundant even against the real redis stubs.
- Config declines a blanket `ignore_missing_imports` (typo'd imports still surface) and scopes a per-module override to the optional `redis` SDK — verified clean both with and without it installed. Full suite: 472 passed.

**Why this work, this session:** Objective, pre-filed follow-up (#129), sibling to `llm-eval-harness#148`; the gate is the machine-checked half of the "annotations are honest" contract.

**Open questions / blockers:** none.

**Next session:** Both priority-tier mypy-gate siblings shipped this run; the other 4 Python library packages' py.typed gaps remain cosmetic per the py.typed lens.

## 2026-07-07 — Issue #131: isolate InMemoryStorage payloads (cache poisoning) (~35 min)

**What got done.** `SemanticCache.lookup(...)` returned the cached payload by reference on the default `InMemoryStorage` backend, so a consumer mutating a returned payload (a natural pattern — appending to a citations/`refs` list, tagging) poisoned the committed cache entry served to every later hit, including semantically-similar prompts that match the same record (`r2.payload is r1.payload` was `True`). `RedisStorage` was immune because it json.dumps the payload on `put` and reconstructs a fresh object per read, isolating both seams; only the dep-free in-memory default leaked the live reference. Fixed by making `InMemoryStorage` deep-copy the payload on `put` (ingress) and `find_nearest` (egress), mirroring Redis's serialize/deserialize isolation — `vector`/`tags` are already immutable, so only `payload` needs copying. Added storage-level and public-API lock tests for both seams (incl. the semantically-similar-hit poisoning path) and an over-rejection guard. Full suite green, ruff clean. PR #132.

**Why prioritized.** Second issue of a DAY multi-issue loop; the static queue was exhausted so work came from parallel fresh-lens dogfood hunts. Five lens families were empty across the run (metric/ratio formulas, sort/ordering, threshold operators, CLI/config-default drift, error-contract on the TS/API repos) before the shared-mutable-state-aliasing lens surfaced this on the highest-value target (the semantic cache). The tell: a repo with two storage backends where the serializing one (Redis) hides the aliasing bug and implicitly documents the expected isolation — the by-reference in-memory default breaks parity. Reproduced firsthand before filing and fixing.

**Open questions / blockers.** None — ready for review.

**Next session:** lco semantic-cache payload isolation is now at Redis parity. The async-pipelines `attach_speedup extra` alias is a known below-bar defensive-copy gap (to_dict already copies, no consumer mutates) — don't churn it.

## 2026-07-08 — Issue #133: cross-model entry no longer masks a valid same-model hit (~40 min)

**What got done.** Multi-model semantic caching (D-005) could silently drop legitimate cache hits. `Storage.find_nearest` returned only the single global-best-cosine record, and `SemanticCache.lookup` applied the D-005 model filter *afterward* (the #125 approach). When a record stored under a different model had a higher cosine than a valid, above-threshold record stored under the query's model, `find_nearest` returned the cross-model record, the `record.model == model` post-check rejected it, and the lookup reported a miss — even though a real same-model hit was available. Reproduced deterministically with the shipped `HashEmbedder`: a 60-token base prompt cached under Haiku (cosine 0.9881) masks a same-model Opus record for a near-identical prompt (0.9836, above the 0.95 threshold); the Opus lookup missed, and dropping the unrelated Haiku record made the identical lookup hit. Fixed by moving the model filter into `find_nearest` (new optional `model=` param on the `Storage` Protocol and both backends) so cross-model records are never candidates; `lookup` passes the query model. Backward compatible (`model=None` preserves the full scan). Added a masking regression test and a `find_nearest` unit guard; updated the #125 long-prompt test (a cross-model-only store now reports `similarity == 0.0` rather than the rejected record's `>= 0.95`, which was misleading telemetry). Full suite green, ruff + mypy clean. PR #134.

**Why prioritized.** First issue of a DAY multi-issue loop; the static priority:high queue was globally exhausted, so work came from a static read of the semantic-cache lookup path. It's a direct incompleteness of the #125/#126 D-005 fix — the safe direction (never serves wrong data) is preserved, but the hit-rate erosion defeats the cache in exactly the multi-model deployment D-005 exists to support.

**Open questions / blockers.** None — ready for review. No core decision: this completes D-005 (hard model isolation), it doesn't change it.

**Next session:** Also spotted (not yet filed) — `batch.py::_from_sdk_batch` sums `n_requests` over only processing/succeeded/errored/canceled and omits Anthropic's `request_counts.expired` field, an undercount on the operator-only Anthropic-backed path. Low severity; file priority:low if it holds up. New lens: when a hardening fix uses a *post-filter* on a single "best" candidate, check whether a higher-ranked *rejected* candidate can mask a valid lower-ranked one (selection-time vs post-filter gap).

## 2026-07-10 — Issue #136: batch usage-token abstain parity with #114 (~30 min, night)

**What got done.** `_from_sdk_result_row` (`batch.py:483`) read batch-result usage tokens with the bare `int(getattr(usage, ..., 0) or 0)` pattern that #114 replaced with `_coerce_token_count` in the cache wrapper — the unfixed sibling of that class. A present-but-malformed usage token on one succeeded row (NaN→ValueError, inf→OverflowError, "abc"→ValueError, negative→poisoned totals) raised out of the parse and, because `results()` maps every row through `_from_sdk_result_row`, destroyed retrieval of the whole completed batch — a worse shape than the single-call #114 crash. Verified firsthand.

Relocated the canonical `_coerce_token_count` to the shared token-domain module `pricing.py` (already imports `math`, already imported by `cache_wrapper`) and re-imported it into `cache_wrapper`'s namespace so its existing call sites and tests are unaffected; `batch.py` imports it too and routes both token reads through it. Relocating rather than duplicating prevents this exact divergence from recurring. Added a parametrized malformed-usage abstain test, an over-abstention guard, and a `results()`-level test proving one bad row no longer sinks the batch — all fail pre-fix. Full suite, ruff, and mypy (D-014 gate) all green.

**Why prioritized.** Static priority:high queue globally exhausted; found via the sibling-incomplete-fix meta-lens. The SDK-projection seams in lco (router text/logprob extractors #94/#106/#112, cache-wrapper usage #114, now batch-result usage #136) are now all hardened on the abstain-don't-crash contract.

**Deferred.** `request_counts` sum (`batch.py:441`) — `BatchJobMeta.__post_init__` treats a malformed count as a backend-shape bug, so a loud failure is arguably intended; left as-is to avoid churn.

**Open questions / blockers.** None — PR ready for review.

## 2026-07-10 — Issue #138: abstain on a non-numeric judge score (~22 min, night)

**What got done.** `JudgeConfidenceSignal.measure` coerced the judge verdict's `.score` with a bare `float(raw)`. A present-but-non-numeric score — a string label (`"high"`), a JSON-decoded string, or any non-coercible object — raised `ValueError`/`TypeError` straight out of `measure()` and `UncertaintyRouter.route()` (no guard at the call site), aborting the whole routing decision for a completed cheap-model call. Since `.score` comes off a BYO duck-typed judge (the `llm-eval-harness.Judge` seam, D-002), this is reachable. The method's docstring already contracts "no usable score (missing/None/non-finite) → couldn't measure → abstain"; the #72 fix guarded three branches but left the bare `float()` unwrapped — present-but-non-numeric is the fourth "no usable score" case.

Wrapped the coercion in `try/except (TypeError, ValueError)` → `SignalReading(value=None, trip=False)`, mirroring the None/non-finite branches. A numeric string (`"0.9"`) still parses; a genuine finite `0.0` still trips. 8 test cases (6 non-numeric shapes abstain, numeric-string still measures, end-to-end `route()` survives a malformed BYO verdict and stays on the cheap model). Full suite (494) + ruff green. Verified the repro firsthand before/after.

**Why prioritized.** Static priority:high queue globally exhausted; found via the sibling-incomplete-fix / abstain-on-malformed-SDK-shape lens. Unrelated to the JT-gated #135/#97.

**Open questions / blockers.** None — PR ready for review.

## 2026-07-10 — Issue #140: abstain on a non-numeric logprob element (~20 min, night)

**What got done.** `_extract_first_token_logprobs` already guarded `None` elements (#106) and non-finite floats (#95), but both `float()` coercion sites (the direct `first_token_logprobs` path and the nested SDK-shape path) were bare list comprehensions. A present-but-non-numeric element — a string label, a JSON-decoded string, or any object `float()` can't accept — off an adapter-set / BYO distribution raised `ValueError`/`TypeError` straight out of `measure()` and `UncertaintyRouter.route()`, aborting the whole routing decision for a completed cheap-model call. This is the exact sibling of the just-merged #138/#139 (a non-numeric judge `.score` off the BYO `eval_harness.Judge` seam) — same "present-but-non-numeric value off a duck-typed seam" class, left unguarded on the entropy path.

Wrapped both coercions in `try/except (TypeError, ValueError)` → `return None` (abstain), consistent with the None/non-finite branches and `measure`'s value=None ⟹ not-trip rule. A numeric string (`"0.9"`) still parses; a finite `0.0` logprob is preserved. 6 tests (non-numeric direct + nested abstain, numeric-string still parses, end-to-end `route()` survives and stays cheap). Full suite + ruff green. Reproduced both paths firsthand before/after.

**Why prioritized.** Static priority:high queue globally exhausted; found via the sibling-incomplete-fix meta-lens on the 7 PRs merged in this run's Phase A. Unrelated to the JT-gated #135/#97.

**Open questions / blockers.** None — PR ready for review.

## 2026-07-11 — Issue #142: reject present-but-non-numeric embedding component in SemanticCache (~20 min, night)

**What got done.** `SemanticCache._validate_embedding` guarded a numeric-but-non-finite embedding component (NaN/±Inf, #87) with a clean `ValueError`, but a **present-but-non-numeric** component (a `str`/`None`/list off the BYO `Embedder` Protocol seam) hit a bare `math.isfinite(v)` and raised an **uncaught `TypeError`** deep inside both `put()` and `lookup()` instead of the seam's clean, index-naming `ValueError`. This is the direct sibling of the #140/#138 "present-but-non-numeric coercion off a duck-typed/BYO seam" class fixed this same run at the router logprob and judge-score seams — the same guard shape, never applied at the embedding-validation seam.

Widened the guard with a small `_corrupt` helper (non-numeric → bad, else `not math.isfinite`); the NaN/Inf branch keeps its exact `"non-finite component"` message so the #87 tests still match, and a new branch names the offending index/value/type for the non-numeric case. Six tests (`put()` + `lookup()` each rejecting a `str`/`None`/list component). Full suite + ruff green. Reproduced firsthand: a BYO embedder returning one `str` element went from `TypeError: must be real number, not str` (uncaught) to a clean `ValueError: ... non-numeric component at index 127: '0.2' (str) ...`.

**Why prioritized.** Static priority:high queue globally exhausted; found via the sibling-incomplete-fix meta-lens on the 8 PRs merged in this run's Phase A. `Embedder` is an explicit BYO Protocol the `HashEmbedder` docstring points production callers at, so a JSON-decoded/truncated-SDK embedding row reaching this seam is real, not synthetic. Unrelated to the JT-gated #135 (per-call model pricing) / #97 and to any semantic-cache threshold tradeoff.

**Open questions / blockers.** None — PR #143 ready for review.

## 2026-07-11 — Issue #144: isinstance-guard 3 present-but-non-numeric pricing/batch seams (~22 min, night)

**What got done.** Completed the present-but-non-numeric coercion sweep (#138/#140/#142) across the pricing/batch cost layer. Three sibling seams applied a bare numeric op to a caller-/JSON-supplied value with no isinstance pre-guard, so a `str`/`None` raised a raw `TypeError` at exit 1 instead of the clean field-named `ValueError` each contract promises (each already handled NaN/Inf cleanly): (1) `compare_realtime_vs_batch` `discount` — a documented per-call override, bare chained comparison `0.0 <= discount <= 1.0`; (2) `ModelPricing.__post_init__` — bare `math.isfinite` on rates (the exact #142 shape); (3) `BatchCostQuote.__post_init__` — same bare `math.isfinite` on prices.

Added `isinstance(value, (int, float))` short-circuiting before the numeric op at each site; messages unchanged for the numeric fields, discount keeps its `[0.0, 1.0]` text, and behavior for bool/int/float is unchanged (kept minimal — only genuinely non-numeric values newly rejected). 20 new test cases (str/None/list/dict at each site; discount NaN case added). Full suite + ruff green. Reproduced all three firsthand before/after.

**Why prioritized.** Found via a **second-order sibling hunt** on this run's own #142 fix (the same class's remaining siblings in the pricing/batch layer). Explicitly not JT-gated #135 (per-call model-pricing *semantics*) — this is pure input-validation completeness.

**Open questions / blockers.** None — PR #145 ready. (Sibling of this run's #142/PR #143: different code files, but both touch MEMORY, so next Phase A merges #143 first then rebases #145.)

## 2026-07-12 — Issue #146: present-but-non-numeric SDK request count crashes _from_sdk_batch (~20 min, night)

**What got done.** `_from_sdk_batch` (`cost_optimizer/batch.py`) projects a duck-typed SDK `Batch` object (D-002) to `BatchJobMeta`, computing the request-count total with a bare `sum(getattr(request_counts, k, 0) for k in ...)` and a bare `int(n or 0)`. A **present-but-non-numeric** count — a `str`/`list`/`dict`/`None` off the duck-typed `resp` (a test fake, SDK version drift, a hand-built `poll()` response) — hit those bare numeric ops and raised a **raw `TypeError`/`ValueError`** deep inside `AnthropicBatchBackend.submit()`/`poll()` at exit 1, instead of the clean, field-named `ValueError` that `BatchJobMeta.__post_init__` documents as the construction boundary ("that's a backend-shape bug, not a valid batch, so reject it here").

Added a module helper `_sdk_request_total` that sums genuine numerics (`int`/`float`, missing field → 0 as before) but returns a present-but-non-numeric value **unchanged**, so `__post_init__` rejects it with `BatchJobMeta.n_requests must be an int >= 1; got X`. 12 tests (valid `request_counts` sum, valid scalar, non-numeric `request_counts` field ×4, non-numeric scalar ×3, missing-count bottoms-out). Full suite (516) + mypy (D-014) + ruff green. Reproduced all three cases firsthand: raw `TypeError: unsupported operand ... 'int' and 'str'` → clean `ValueError: ... got 'oops'`.

**Why prioritized.** Static priority:high queue globally exhausted. Found via the sibling-incomplete-fix meta-lens: **#136** routed the token-count `int()` sites in the immediate neighbor `_from_sdk_result_row` through `_coerce_token_count` but left the `n_requests` `int()`/`sum()` sites in `_from_sdk_batch` bare. The two seams have deliberately **different** contracts — token usage is best-effort observability and *abstains* (→0); a request count of "no valid requests" is meaningless and must *raise*. Verified firsthand (one of 7 sibling-hunt agents hit; the other 6 repos — leh/ems/prs/rag/chunking/aiapp — came back EMPTY, with leh's `JudgeScore.__post_init__` and chunking's `from_json` scalar-field gaps correctly left unfiled as no-real-entry-point / test-only-path churn).

**Open questions / blockers.** None — PR #147 ready. Not JT-gated #135/#97/#124 (D-013).

## 2026-07-12 — Issue #148: architecture.md calls a shipped dashboard panel a "follow-on issue"
**Duration:** ~12 min · **Branch:** `session/2026-07-12-0943-issue-148`

- `docs/architecture.md` (RouterStats-in-savings-JSON section) still described the per-signal router `st.dataframe` dashboard panel as unbuilt future work ("a follow-on issue"), but it shipped in #66 — `dashboard/app.py` renders the "Router per-signal escalation" panel via `_pick_router_row`/`_router_panel_rows`. Rewrote the sentence to reflect the shipped panel, citing #66 consistent with the doc's existing #62/#64 citations. Doc-only (the session's documentation exception to tests-with-code); the architecture-doc lock suite + full test suite stay green.

**Why this work, this session:** Fifth hit of the run — the served-output doc-drift lens's second finding (with rag#140), reconciling a flagship architecture doc with shipped code.

**Open questions / blockers:** none — ready for review.

**Next session:** Phase A merge PR for #148.

## Session 2026-07-13 (night) — issue #150: harden three SemanticCache numeric-config guards

`SemanticCache` validated three numeric config values — `similarity_threshold`, `default_ttl_s` (constructor), and the per-call `put(ttl_s=)` override — by running the numeric test (a chained comparison or `math.isfinite`) *before* any type check. A present-but-non-numeric value (a `str`/`None`/`list` arriving from a JSON/YAML/env config table) therefore reached the numeric operation and raised a raw `TypeError` with a message naming neither the field nor the value, instead of the clean field-named `ValueError` the rest of the token/config layer already raises (`ModelPricing`, `_validate_embedding`, `compare_realtime_vs_batch.discount`).

The fix adds an `isinstance(value, (int, float))` check first in each guard, short-circuiting before the numeric op — the isinstance-first sibling of the #142/#144/#146 present-but-non-numeric sweep that had hardened the pricing, batch, and embedding seams but skipped these three cache-config seams. Reproduced all three raw `TypeError`s firsthand before filing, confirmed the sibling seams already raise the clean `ValueError`. Added three parametrized lock tests (`str`/`None`/`list`) across all seams; full suite (473 tests) green, ruff clean.

**Why this work, this session:** First hit of the night run, surfaced by the sibling-incomplete-fix dogfood hunt on llm-cost-optimizer and verified firsthand.

**Open questions / blockers:** none — PR #151 ready for review.

**Next session:** Phase A merge PR for #150.

## 2026-07-13 (night) — Issue #152: isinstance-guard router thresholds
**Duration:** ~20 min · **Branch:** `session/2026-07-13-0913-issue-152` · **PR:** #153

`EntropySignal.threshold` and `JudgeConfidenceSignal.threshold` fed their value into `math.isfinite()`/a chained comparison in `__post_init__` before any type check, so a present-but-non-numeric config value (a `str`/`None` from a JSON/YAML/env table) raised a raw `TypeError` instead of the clean field-named `ValueError` the rest of the numeric-config layer raises. Prepended an `isinstance(x, (int, float))` check to both guards — the direct `router.py` sibling of the #151/#142/#144 isinstance-first sweep. Added `str`/`None`/`list`/`object` lock tests to both threshold-validation test classes (the prior tests only covered numeric-but-invalid values). Swept the module: no other seam has the gap.

**Why this work, this session:** First hit of the night run, surfaced by the numeric-config sibling-incomplete-fix hunt on llm-cost-optimizer and verified firsthand with a running repro before filing.

**Open questions / blockers:** none — PR #153 ready for review.

**Next session:** Phase A merge PR for #152.

## 2026-07-14 (night) — Issue #154: atomic_write_text overflows NAME_MAX on a long basename
**Duration:** ~12 min · **Branch:** `session/2026-07-14-0743-issue-154` · **PR:** #155

`atomic_write_text` built its temp file name as `.<basename>.<random>.tmp`, so a destination basename near `NAME_MAX` (255 bytes) overflowed the limit and raised `OSError` ENAMETOOLONG — even though a plain `write_text` of that same path succeeds. Reachable from the `save(path)` dump APIs on `PromptCacheWrapper` / `Router` / `SemanticCache`. Identical to `rag-production-kit#128` and `mcp-server-cookbook#96`. Verified firsthand.

Fixed by porting the rag#128 fix (`_cap_base_for_temp`, 200-byte typed char-boundary budget). One regression test; full suite + mypy gate green, ruff clean.

**Why this work, this session:** Eighth hit of the night run — the priority-tier leg of the cross-repo `atomic_write_text` overflow sweep (leh #175, chunking #128, lco #154 all fixed this run; the non-tier repos ems/prs/pyasync/vsas carry the identical bug and are deferred to next run per D-009).

**Open questions / blockers:** none — PR #155 ready for review.

**Next session:** Phase A merge PR for #154; finish the atomic-write sweep on ems/prs/pyasync/vsas.

## 2026-07-14 (night, issue #156) — bench scripts miss the write-seam / bad-input exit-2 invariant

`scripts/bench_savings.py` and `scripts/tune_threshold.py` already commit to an exit-2-for-operator-misconfig contract (the `--no-dry` branch prints `::error::` and returns 2), but three sibling operator-input errors escaped as a raw traceback at exit 1 — breaking that contract and the portfolio-wide write-seam / bad-input invariant (llm-eval-harness#158/#159, python-async-llm-pipelines#84, vsas, chunking#126) that had never reached lco's scripts.

Gaps: `bench_savings --n 0`/negative ran to completion and emitted a vacuous all-$0.00 "benchmark" over an empty workload (now exit 2, matching the `--n >= 1` guard in the pyasync/vsas bench scripts); an unwritable `--out` in either script raw-tracebacked after the run (now wrapped in `try/except OSError` → exit 2); and a non-numeric `--thresholds` token in tune raised a raw `ValueError` (now exit 2). Verified every path firsthand: all six error/happy cases exit as intended.

The lens: a script that already has an explicit `return 2` operator-error path but raw-tracebacks on a sibling operator input is an internal contract inconsistency, not churn — the existing exit-2 branch is the evidence the gap is real. Found by my own manual hunt (lco scripts weren't in the agent wave; I'd only checked the core package earlier). Gotcha: two existing `test_atomic_write.py` tests monkeypatched `os.replace` to raise and asserted the OSError *propagates*; my guard now catches it, so I updated both to assert `rc == 2` with no partial artifacts (the atomic all-or-nothing intent is preserved under the new exit-2 contract). Full suite green, ruff clean. Shipped as PR #157.

## 2026-07-15 — Issue #158: bool config values slip past isinstance(int,float) (sibling of leh#178)

A cross-repo second-order sibling of this run's own llm-eval-harness #178. The
#142/#144/#151 sweep added `isinstance(x,(int,float))`-first checks to reject
str/None config before math.isfinite raised a raw TypeError — but `bool` is an
`int` subclass, so True/False coerce to 1.0/0.0 in-range and slip through. Verified
firsthand across 5 public seams (router EntropySignal/JudgeConfidenceSignal
thresholds, SemanticCache similarity_threshold/default_ttl_s/per-call ttl_s), each
with the documented silent-misconfig harm (disable/always-trip/near-instant-evict).

Fixed by rejecting bool explicitly, mirroring leh calibration.py/judge.py/#178.
Scoped to config validators with documented harm; left pricing + embedding-vector
checks (different value class). Full suite green.

Why prioritized: the bool-is-int-subclass lens from #178 transfers to every repo
that added isinstance(int,float) for str/None but not bool.

## 2026-07-16 (night) — tune_threshold non-finite/negative dollar flags (#160)

`scripts/tune_threshold.py` carefully guards `--thresholds`, `--out`, and `--no-dry` with a clean exit-2 contract, but the two dollar flags `--cheap-dollars`/`--strong-dollars` had only argparse's `type=float` — which parses `nan`/`inf`/negative. Those values flowed into `json.dumps` as bare `NaN`/`Infinity` (invalid JSON) and into `sweep()`'s per-request dollar arithmetic, so a mistyped flag could write invalid JSON or a fabricated negative cost into the committed `docs/threshold_demo.json` (the artifact README:181 documents regenerating) at exit 0.

Fixed by validating both flags with `math.isfinite(v) and v >= 0` after `parse_args`, emitting a clean `::error::` line + exit 2 — the same operator-misconfig contract as `--thresholds`, and the portfolio's "no non-finite / no fabricated dollar at JSON egress" rule. Verified all cases firsthand. 8 tests added (7 parametrized rejects + a happy-path strict-parse). Note: the tests pass `--flag=value` because a bare `-inf` token is otherwise mistaken for an option by argparse. PR #161.

## 2026-07-17 — Issue #162: guard the PNG plot write seam (exit 2)

The #156/#157 fix wrapped the JSON write in `tune_threshold.py` so an unwritable
`--out` exits cleanly with code 2 instead of a raw traceback. It missed the
sibling PNG write one line below: `_try_save_plot` runs `mkdir` + `savefig`, both
of which raise `OSError` when the `.png` path is unwritable (a directory sitting
at the `.png` stem, a read-only file) — with matplotlib installed that escaped
`main()` as a raw exit-1 traceback. The guard's own comment even wrongly claimed
the plot write "already degrades gracefully" (only true when matplotlib is
absent). Wrapped the plot call to emit `::error::could not write sweep plot` and
return 2, matching the JSON seam. Test monkeypatches `_try_save_plot` to raise
`OSError` so the lock runs in CI without a matplotlib dependency. Shipped as PR
#163 (ready).

## 2026-07-21 — cost-rate validators reject bool (#164, PR #165)

Three lco cost-rate validators — `ModelPricing.__post_init__`,
`BatchCostQuote.__post_init__`, and `compare_realtime_vs_batch`'s `discount`
guard — checked `isinstance(value, (int, float))` + finiteness/range + non-negative
but none excluded `bool`. Since bool subclasses int, a JSON `true`/`false` from a
config/JSON pricing contract passed every check and fabricated a rate: `True`→$1/MTok
or a 1.0× multiplier, `False`→a 0.0× free-cache multiplier or a 100% batch discount;
`discount=True`→0% savings. The int-field validators in the same `batch.py` already
excluded bool, so the float-rate validators were inconsistent. Verified all three
firsthand. lco#158 swept the router/semantic-cache *config* threshold seams but not
these *cost-rate* validators — a distinct dataclass cluster. Three parametrized
tests. Lesson: a "bool-is-int-subclass EXHAUSTED" note scoped to one cluster doesn't
cover a different one — re-scan per dataclass family when the vein resurfaces.

## 2026-07-31 — ruff 0.16.1 started formatting Markdown (#168, PR)

CI installs ruff unpinned through `pip install -e '.[dev]'`, and ruff 0.16.1 —
released since the last green run — extended `ruff format` to Python code
blocks *inside Markdown*. Nothing in this repo changed; the tool's scope did.
Six portfolio repos broke the same way on the same day: rag-production-kit,
llm-eval-harness, chunking-strategies-lab and llm-cost-optimizer went red on the
morning's merges, while prompt-regression-suite and python-async-llm-pipelines
were latent, set to go red on their next push.

The trap is version skew. Local venvs still carry 0.15.13, so `ruff format
--check` passes locally and fails in CI; reproducing it at all meant installing
0.16.1 into a throwaway venv. I only found it because my own in-flight PR went
red on lint and `main` turned out to be red too.

Reformatting the Markdown would have been the wrong fix. The lint contract here
has always been "format Python source", and prose is not Python source. The
sharpest case is chunking-strategies-lab, where the same sweep wanted to rewrite
`data/corpus/05_async_pipelines.md` — a pinned benchmark corpus document.
Editing a code block inside it changes the text the chunkers run over and shifts
every canonical metric. A lint tool must never rewrite a benchmark input.

So: `extend-exclude = ["*.md"]`, which re-states the scope the config always
meant, plus a lock test so a future pyproject cleanup can't silently re-expand
it. The test asserts on the config rather than shelling out to ruff, because the
intent needs to be un-droppable and the assertion has to hold on any ruff
version — including ones predating the Markdown feature, which is the very skew
that let this land unnoticed. Amusingly, the lock test itself tripped a *second*
0.16.1 change: UP036 now flags the `sys.version_info >= (3, 11)` tomllib import
guard at `target-version = "py311"`. Lint rules drift on minor releases too, not
just formatter scope.

Pinning a ruff range in `.[dev]` is the deeper fix, but that is a dependency
policy call across six repos rather than a bug fix, so it is flagged for JT
rather than made unilaterally.

## 2026-07-31 — `_sdk_request_total` laundered malformed counts (#166, PR #167)

`_sdk_request_total` exists for one reason: hand a malformed SDK request count
back untouched so `BatchJobMeta.__post_init__` can reject it with a clean,
field-named `ValueError` instead of a raw exception erupting out of
`submit()`/`poll()`. Its `isinstance(part, (int, float))` gate was wider than the
"well-formed count" the docstring claims, and the trailing `int(total)` then
laundered the extras straight past the guard the docstring points at.

Four branches, verified firsthand against `main`. `succeeded=True` summed to 1
and fabricated `n_requests=1` — bool subclasses int, so `__post_init__`'s own
explicit bool check never ran; that guard was dead on this path. `2.7` truncated
silently to `2`. `NaN` reached `int()` and raised a raw, non-field-named
`ValueError`. And `inf` raised `OverflowError` — which is not a `ValueError`
subclass, so it slipped past every downstream `except ValueError` and escaped as
a traceback. That last one is the worst: it breaks the exception *type* contract,
not just the diagnostic.

Fixed by extracting `_is_malformed_count_part`, which rejects bool, non-finite,
and non-integral floats so all three join `str`/`None`/containers on the clean
path; integral floats like `3.0` still sum, since the SDK legitimately returns
JSON numbers. Tests cover every shape on both the `request_counts` and scalar
`n_requests` paths, lock the exception type, and regression-lock integral floats.

Two lessons worth carrying. First: when a fix hardens a dataclass's
`__post_init__` validators (#164 did exactly that for this file's float rate
validators), also grep the module for *coercion helpers that run before
construction* — they can render the new guard dead on their path. Second: audit
the exception *type*, not just the presence of a guard. `int(nan)` is a
`ValueError` but `int(inf)` is an `OverflowError`, and that difference defeats
`except ValueError` contracts.

## 2026-08-03 — Issue #170: eight dashboard tests that had never run

The savings dashboard is layer 5 of the README, it is in the repo's one-line
description, and `README.md:21` tells the reader to run `streamlit run
dashboard/app.py`. Eight tests in `tests/test_bench_savings.py` exist to
protect it. None of them had ever executed in CI.

The `test` job installed `pip install -e '.[dev]'`, and `[dev]` carries neither
streamlit nor pandas — those are the `[dashboard]` extra. So every one of the
eight hit its `find_spec(...) → pytest.skip(...)` guard and skipped, and the
job reported green on every PR while nothing checked the feature. Confirmed in
a fresh 3.11 venv with exactly CI's install line: eight `SKIPPED` lines. With
`[dashboard]` added, zero skips and the full suite green on 3.11 and 3.12.

This is a different shape from the CI-coverage-gap sweep that produced mcp#90
and rag#120. Those were missing *jobs*. Here the job exists and is green while
the tests quietly skip — a green check mark that means less than it looks like.
It is also mechanically findable: run pytest with `-rs` in a fresh venv using
the repo's own CI install line, and count the skips. Doing that across the
portfolio turned up the same shape in three more repos (chunking 2, ems 4, vsas
3), filed separately. rag's seven Postgres skips are *not* in this class — its
`integration-pg` job brings up a real Postgres service, so those genuinely run.

The guards themselves stay. They are right for a contributor in a minimal venv;
CI is not that environment, and the bug was treating it as though it were. Both
packages are pure-Python wheels, so CI stays hermetic.

The lock test is the part worth reusing. Asserting "CI installs `[dashboard]`"
would let the next extra repeat this silently, which is exactly how this one
happened — so it reads the requirement out of the suite instead, scanning the
skip guards for `(the [<name>] extra)` and asserting each name appears in the
install line. There is an anti-vacuous test that fails loudly if the guard
wording changes, rather than letting the lock start passing while checking
nothing, and a parametrized check that each guarded extra actually exists in
pyproject — a guard naming a nonexistent extra can never be satisfied. And I
reverted the install line to confirm the lock fails with the right diagnostic
before shipping it. Shipped as PR #171.

## 2026-08-04 — Issue #172: two clocks, one subtraction

`SemanticCache` takes an injectable `now_fn` and computes
`expires_at = now_fn() + ttl_s`. `RedisStorage.put` turned that absolute
timestamp back into a relative TTL with a direct `time.time()` call.

Two different clocks in one subtraction. With the default they're the *same
object* and the difference is microseconds, which is exactly why this survived
this long. Under the injected clock the parameter exists for — the one the
repo's own `_cache()` test helper uses, with a fake `1000.0` — the subtraction is
nonsense, and `max(1, …)` floors the result:

```
ttl requested: 3600s; redis TTL actually set: 1
in-memory: after 1.2 REAL seconds of a 3600s ttl -> hit=True
redis:     after 1.2 REAL seconds of a 3600s ttl -> hit=False
```

A one-hour entry evicted after one second, with no diagnostic, and it inverts
what the tool is for: the Redis-backed cache stops caching, every lookup misses,
every request goes to the model.

The `max(1, …)` is what made it silent rather than visible — it turned "these
numbers came from different clocks" into a plausible short TTL. But it's doing a
real job (stopping an already-expired record from becoming `EXPIRE key 0`, an
immediate delete), so it stays. It was the clock that was wrong, not the floor.

The lens worth keeping: **when a component takes a `now_fn`, grep the whole
module for direct `time.time()` / `datetime.now()` / `perf_counter()` calls.**
Any collaborator that consumes a timestamp the injected clock *produced*, but
reads the wall clock to do it, is a silent divergence — and one that's invisible
at the default, so tests written against the default won't find it.

It's also the #131 shape again: one `Storage` interface, two implementations,
and a property that holds for free in one is hand-maintained in the other.
`InMemoryStorage` honours `expires_at` through `purge_expired(now_fn())`, the
cache's clock threaded correctly. `RedisStorage` delegates expiry to Redis, which
only knows the wall clock. The README states it as a feature: "RedisStorage uses
… native Redis TTL for expiry."

`RedisStorage` now takes its own `now_fn`, and `SemanticCache.__init__` refuses a
storage whose clock isn't its own — by *identity*, so an untouched construction
(the same `time.time` on both sides) passes and the only configuration rejected
is a clock set on one side and not the other, which is precisely the mistake.

I considered `EXPIREAT` with `record.expires_at`, which looks tidier and is
worse: it hands Redis a timestamp on a clock Redis doesn't share, so a fake clock
starting at 1000.0 would delete the key instantly instead of after a second.

One test does nothing but assert that the pre-fix expression really did yield 1
for a 3600-second TTL and the corrected one yields 3600. That keeps the guard
anchored to the silent short TTL rather than to a constructor error a future
change could relax around.

626 passed. Shipped as PR #173.

## 2026-08-05 — a stemless `--out` crashed both artifact scripts (#174)

`bench_savings.py` and `tune_threshold.py` both take `--out` as a *stem* and
derive the real filenames from it:

```python
out_stem = Path(args.out)
out_json = out_stem.with_suffix(".json")
out_md   = out_stem.with_suffix(".md")     # .png in tune_threshold
```

`Path.with_suffix` raises `ValueError` when the path has no filename component,
and `Path("")`, `Path(".")` and `Path("/")` all qualify. That call sat *outside*
the write-seam `try`, so all three escaped `main()` as a raw traceback at exit 1
on both scripts.

What makes it a gap rather than a nitpick is the company it keeps. Both `main()`s
already translate every other operator misconfiguration into a clean `::error::`
line and exit 2 — `--no-dry`, `--n < 1`, malformed `--thresholds`, and, pointedly,
an **unwritable `--out`**. So an unusable `--out` of one kind (can't be written to)
was a clean exit 2 with a comment explaining why, while an unusable `--out` of an
adjacent kind (has no filename to suffix) was a stack trace. The write-seam guard's
own comment says it exists because the `OSError` "escaped `main` as a raw traceback
at exit 1 — the 'success' range"; that sentence describes this path equally well.

In `tune_threshold` it also failed later than necessary: the whole sweep ran to
completion before the suffix blew up.

The fix adds `scripts/_io.resolve_out_stem`, used by both `main()`s at
argument-handling time. Eleven tests cover the three bad stems on each script,
the fail-before-the-work property (asserting stdout is empty), and ordinary,
nested and already-suffixed stems to prove nothing else moved.

Two notes on scope. The first parametrization included `..` and `x/..`, and both
*failed* — `Path("..").name` is `".."`, not empty, so `with_suffix` happily yields
`...json`, and writing a file with that name is a coherent if odd request rather
than a crash. They came out; the guard now matches exactly the set `with_suffix`
rejects. And the sibling stem-suffix site in
`python-async-llm-pipelines/scripts/bench_1000_doc.py` was checked and is fine —
there `atomic_write_text(out_path, …)` runs *before* `with_suffix`, so a stemless
`--out` surfaces as the already-guarded `OSError` and exits 2 correctly.
`capture_demo.py`'s two calls build their stem from an internal temp dir and
aren't operator-reachable.

How it surfaced is worth recording, because it wasn't a hunch. An AST scan across
all eight Python repos extracted the exception arms of every `main`/`cli` function
looking for intra-repo asymmetry, and `bench_savings` catching only `OSError` next
to `tune_threshold` catching `OSError` *and* `ValueError` is what pointed at the
file. The actual bug turned out to be a third thing neither arm covered — the scan
found the right file for the wrong reason, which is still a good trade.

Full suite 638 passed; ruff clean under 0.15.13 and 0.16.1.

## Session 2026-08-06 — the workload file belonged to the directory, not the run (#176)

`bench_savings.py` builds three output paths from `--out STEM`. Two of
them use the stem. The third didn't:

```python
out_json     = out_stem.with_suffix(".json")
out_md       = out_stem.with_suffix(".md")
out_workload = out_stem.parent / "savings_workload.json"   # stem discarded
```

The canonical invocation is `--out docs/savings`, whose stem is
`savings` — so all three names come out identical and the divergence is
invisible. The README's other example, `--out /tmp/savings`, works for
the same accidental reason. It only shows up when the stem is anything
else, which is how I found it: running the documented command with
`--out /tmp/lco_sav` printed

```
workload   /tmp/savings_workload.json
```

The script announces the mismatch on its own stdout.

### What it costs

Because the basename was a constant, two runs in one directory
overwrite each other:

```
$ python scripts/bench_savings.py --dry --n 500 --out /tmp/d/savings
$ python scripts/bench_savings.py --dry --n 25 --seed 7 --out /tmp/d/savings_small
$ ls /tmp/d
savings.json  savings.md  savings_small.json  savings_small.md  savings_workload.json
```

One workload file for two runs — and since `--n` and `--seed` are
operator flags, it isn't the same workload at a different size, it's a
different workload at a different seed. The 500-row `savings.json`,
whose table the README quotes, ends up sitting next to a 25-row workload
record. `savings_small` never gets a file at all.

That file isn't incidental output. It's the provenance record the whole
benchmark rests on. D-012:

> Token counts and first-token logprobs are canned in
> `docs/savings_workload.json` **so the numbers are bit-for-bit
> reproducible.**

Silently swapping it for another run's is exactly the failure D-012
exists to prevent. It's the no-fabricated-benchmarks rule approached
from the other side: every number is honest, but the artifact that
proves they're re-derivable can be quietly replaced.

It was already latent in shipped code. `capture_demo.py` runs the bench
with stem `savings_run`, so it writes `savings_run.json`,
`savings_run.md`, and `savings_workload.json` — and its own docstring
*documented* that mismatch as though it were the design.

### Checking whether it was deliberate first

Two existing tests reference `<parent>/savings_workload.json`. Per the
lesson from python-async-llm-pipelines#90 — a test that names a
behaviour can mean the code is right and the docstring is wrong — that
had to be ruled out before calling this a bug.

It isn't the same situation. Both tests use a stem of literally
`savings`, where `<stem>_workload.json` and `<parent>/savings_workload.json`
are the *same string*. They mirror the code; they can't observe the stem
being discarded. And both pass **unmodified** under the fix, which is
the cleanest evidence available that the canonical path is untouched —
so neither was edited.

D-012 itself pins the *committed* artifact's location, and its stated
rationale is re-derivability. The fix serves that rationale rather than
conflicting with it, and there's now a lock test asserting `--out
docs/savings` still produces `docs/savings_workload.json`, so a future
refactor of the derivation can't quietly rename the one path D-012, the
README, and `docs/architecture.md` all depend on.

### The fix

`out_stem.with_name(out_stem.name + "_workload.json")`. Byte-identical
at the canonical stem, so no committed artifact was regenerated — this
is name derivation only, and regenerating would be churn against the
benchmark-integrity rule.

The regression test asserts on the *corruption*, not on file existence:
each workload's row count has to match its own run's `n_rows`. Two files
existing would pass even if the contents were crossed.

### Generalizable

When N paths are derived from one operator argument, check that all N
actually use it — and probe with a value where the derivations *differ*.
A default that makes two formulas agree hides the one that's wrong.
Running the shipped example verbatim proves nothing here, because the
documented stem is precisely the value that masks the bug.

## 2026-08-07 — `--help` printed a command that fails (#178)

`scripts/tune_threshold.py` documented a `--dataset path/to.jsonl` flag in
the Usage block at the end of its module docstring. No such flag exists.
What makes it worse than a stale comment is that argparse uses the module
docstring as the parser description, so `--help` printed that broken example
immediately above the list of flags that contradicts it. The README repeated
the claim twice, in the present tense, saying the curve is produced against
an operator-supplied dataset — when the dataset is five hardcoded rows.

The fix moves the docs to the code rather than the other way round. The
real-API path is a deliberate stub that exits 2 and says so, with a comment
explaining it would need real adapters and a real key and that a fabricated
version isn't going to ship. Adding the flag without the adapters would be
half a feature and would quietly undo that. The docstring now says what the
dry path actually uses and that a supply-your-own flag comes with the
adapters.

The interesting part was the sweep this came from — the same lens as the
llm-eval-harness fixture work earlier tonight, moved from documented *paths*
to documented *flags*. Six candidates across twelve repos, and five were
false positives. Every one of them taught the test something: one flag was a
second-position alias, one was synthesized by `BooleanOptionalAction`, two
belonged to other commands quoted as provenance, and one was a test module
that isn't a CLI at all.

So the lock reads each script's own `--help` instead of grepping
`add_argument`, which makes aliases and the implicit `--no-X` resolve for
free, and it scans only the `Usage:` block, which is what keeps other
commands' flags out. One subtlety mattered more than it looks: accepted flags
are collected from the option-entry lines of the help output and never from
the option help prose, because `--dry`'s own help text contains the string
"--dry" — harvesting prose would have made the lock too lenient, letting any
ghost flag mentioned anywhere pass.

And the check only ever runs `--help`, which exits before `main()` does
anything. The more obvious design — try each documented flag and see if
argparse rejects it — would have run `--dry` on its own and written into
`docs/`.

## 2026-08-12 — a wrapper that threw away the exit code its callee got right (#180)

`scripts/bench_savings.py` does the right thing. Given a read-only output
directory it catches the write failure, prints a clean
`::error::could not write bench artifacts: [Errno 13] ...`, and returns the
documented 2. That behaviour is exactly what #156 was for.

`scripts/capture_demo.py` then threw all of it away. `_run_bench_into` raised
an uncaught `RuntimeError` on any non-zero rc, which downgraded the code from
2 to 1 — the findings code, for an I/O error — dumped a traceback on top of
the one diagnostic worth reading, and did so via a message ending in
`output captured:` followed by nothing at all. That last part is almost funny:
`redirect_stdout` captures stdout, and the bench's diagnostic goes to stderr,
so the single thing the exception contributed over the callee's own reporting
was a blank line.

The general shape is worth keeping: **a wrapper that raises on a callee's
non-zero rc destroys that callee's exit-code contract.** The entire point of
an exit-code sweep is that a *code* travels through a call chain. An exception
doesn't. Anywhere in the portfolio, `if rc != 0: raise ...` sitting above a
script that was carefully taught to return 2 is the same bug.

What settled it as an oversight rather than a decision was twelve lines below
the raise, where the sibling failure — same class, "the bench didn't give us
what we need" — was already handled properly, with a clean `[capture]` line
and an explicit return. Two adjacent handlers for two halves of one failure
mode, only one of them actually written as a handler.

The other three seams are the ordinary family, and they match the
llm-eval-harness sibling shipped earlier in the same run: an unvalidated
`type=float --pause-seconds` that crashes on `inf` (after STAGE 1 has already
run) and silently pauses nowhere on `nan`, a bare `mkdir`, and a bare
`copy2`. The `copy2` deserves its own note: it is genuinely a second seam, not
a redundant one, because an existing-but-unwritable *file* leaves the
*directory* perfectly valid — so it sails through `mkdir(exist_ok=True)` and
fails at the copy instead.

Worth recording that porting a lens is not porting a fix. The leh sibling
prompted the look here, but the most damaging finding in this repo has no
counterpart there.

## 2026-08-13 — The cache key delimiter could be forged, and the comment said it couldn't (#182)

**Duration:** ~35 min · **Issue:** #182 · **PR:** #183

`_make_key` has now hand-built a delimiter twice. The first, `f"{model} {prompt}"`, slid on a space — two different (model, prompt) pairs hashing to one key, and since storage keys records by their key, the second write silently destroyed the first. The replacement was `f"[model={model}] {prompt}"`, and it slides on `] ` in exactly the same way.

What made it findable was the comment. It asserted that the `[model=...]` delimiter "can't be produced by any other split, so only a genuinely identical (model, prompt) collides". That is a test case written in prose, and running it took under a minute. This is the third time this run that a comment asserting a property turned out to be the thing pointing at the bug.

The lesson isn't to pick a better delimiter — a third hand-picked one would have the same shape of bug. It's to stop hand-picking. `json.dumps` escapes field content, so a boundary can't be produced from inside a field by construction, and nobody has to reason about which characters a model id might contain. That pattern was already in the repo: `batch.py` hashes canonical JSON for its payload comparison. Two key-derivation sites in one package, one structurally safe and one asserted safe.

I've been explicit in both the issue and the PR that reachability here is contrived — a real caller passes a real model id, and this is not a live incident. The reasons to fix it are the wrong comment and the correct pattern sitting one module over, not the collision itself.

The embedding input keeps its readable `[model=...]` form and no longer backs the key. A key needs unforgeable boundaries; an embedding input is text going to a tokenizer, where braces and quotes would degrade the similarity signal, and that prefix was tuned against the 0.95 threshold in #125/#133. Consolidate the mechanism, not the string.

**Operational note:** every key value changes, so an upgrade starts from a cold cache. In-memory that's nothing; Redis ages the old keys out on their TTL.

## 2026-08-14 — a router signal that raises destroyed a paid-for cheap call (#184)

`UncertaintyRouter.route()` calls each escalation signal in a bare loop, with
no exception handling, *after* it has already called the cheap model and been
billed for it. A signal that raised took that completed response down with it
and handed the caller nothing.

What makes this a clean find rather than a speculative one is that `router.py`
argues against itself. Its comments state the contract three separate times —
"aborting the whole routing decision for a completed cheap-model call. Abstain
instead". But every fix that produced those comments (#94, #106, #95, #112,
#118) hardened a signal against a malformed **return value**. Not one covered
the signal **raising**. The general form is worth keeping: when a module has a
whole wave of "abstain, don't crash" fixes, ask which axis the wave ran along,
and probe the other one.

The raise half is also the half that actually happens in production. Both entry
points are BYO by design: `EscalationSignal` is a public `Protocol`, and
`JudgeConfidenceSignal.judge` is explicitly the `eval_harness.Judge` seam
(D-002). That second one is a direct consequence of work merged in this same
session — llm-eval-harness#201 deliberately made `Judge.score` raise
`JudgeParseError` in more cases, on the sound reasoning that a wrong-but-
plausible judge score is the failure that harness exists to catch. Right call
there; it widens exactly the branch this repo didn't catch. Finding it meant
looking at the *consumer* of a seam I had just changed in another repo.

The probe was a four-row table — usable score, non-numeric score, raising
judge, raising BYO signal — printing the outcome next to two counters: how many
cheap calls had been paid for, and what `total_routes` recorded. Rows two and
three side by side are the entire issue: a judge returning garbage is handled
gracefully, a judge raising is not.

Printing the telemetry counter is what surfaced the second defect.
`total_routes` read **0** on both failing rows, because the stats block sits
after the signal loop and a raising signal skips it. `escalation_rate` is
`escalations / total_routes`, so every failed route was missing from the
*denominator* — meaning a router whose judge fails intermittently reports a
better-looking escalation rate than reality, to `dump_stats_json`,
`docs/savings.json` and the dashboard alike.

One design point generalizes. Converting a raise into an abstention trades a
loud failure for a silent one unless you also record it: `signal_values` shows
`None` whether the signal abstained or exploded. So the new
`RouterStats.per_signal_errors` counter isn't polish, it's what makes the fix
correct, and there's a test asserting that a signal abstaining by *returning*
`None` is not counted as an error. The isolation also turned out to be
per-signal rather than per-route — pre-fix, one broken judge silently disabled
every signal declared after it, which got its own test.

`docs/savings.json` had to be regenerated because `router_stats` embeds
`RouterStats.to_dict()` wholesale. The diff is exactly one line and every
dollar figure is byte-identical, which is the check that this is an
observability change and not a benchmark movement. Four existing shape locks
failed on the new key; all four were right, and were updated rather than
weakened.

Two hunts in this repo came back empty and are recorded so they aren't
repeated. `pricing.py` is saturated. And `bench_savings.py`'s `_sort_key` does
hand-concatenate `f"{seed}:{row_id}"` into a SHA-256 — the exact class #182
fixed in `semantic_cache` — but `row_id` is generated internally as
`easy-`/`medium-`/`hard-` plus four digits and `seed` is an `int`, so no
delimiter can be injected. Separately, lco was missing from the portfolio's
GFM pipe-escaping enumeration, so I checked it: `_format_markdown` has one
free-form cell, but every `extra` key is a fixed literal and every value is a
number, so it isn't reachable. lco is now enumerated too.

## 2026-08-19 — `--thresholds` was guarded for parse failure and nothing else (#186)

`scripts/tune_threshold.py` validates `--cheap-dollars`, `--strong-dollars`
and `--out` against their value domains and exits 2 on each. `--thresholds` —
the flag the script is named after — was checked only for tokens `float()`
*refuses*. The comment introducing the dollars guard, four lines above, states
the rule it was missing almost verbatim:

> argparse only enforces `float`, which happily parses `nan`/`inf`/negative

That rule was applied to two flags and not to the third. This is a lens worth
reusing: when a guard's comment articulates a rule in general terms, check
every sibling the rule covers, not just the one the comment is attached to.

Running the variant table (and capturing the exit code *before* any pipe)
showed the matrix immediately:

```
--thresholds 'abc'       exit=2   correct
--thresholds 'nan,0.5'   exit=1   raw ValueError traceback
--thresholds 'inf'       exit=1   raw ValueError traceback
--thresholds '-1.0,0.5'  exit=1   raw ValueError traceback
--thresholds '1e400'     exit=1   raw ValueError traceback
--thresholds ''          exit=0   (!)
--thresholds ','         exit=0   (!)
```

All four exit-1 rows reach `EntropySignal.__post_init__`'s own guard from
*inside* `sweep()`, so a script-level usage error surfaces as a library-level
exception at the wrong exit code. `1e400` is the one an operator cannot see
coming — an ordinary finite-looking decimal literal that `float()` returns as
`inf`.

The two exit-0 rows turned out to be the bigger problem. An empty or
comma-only list survives the `if t.strip()` filter, leaving `thresholds == []`,
so the sweep produces no rows and the script prints `sweep wrote ...` and
succeeds. `--out` defaults to `docs/threshold_demo`, which is the stem the
README's documented command uses, so I watched it replace the committed
eight-row artifact with `{"rows": []}` and then restored it with
`git checkout`. With matplotlib installed, `ax.plot([], [])` is legal too, so
a blank PNG lands beside it and is reported as `plot wrote`. The empty row
list is silent at every seam. `--thresholds "$THRESHOLDS"` with an unset
variable is the ordinary way to reach this from CI, and the success exit code
is exactly what lets it survive review.

Both guards go before `sweep()` and before any write, matching where the
existing guards sit, so a rejected run leaves no half-produced `.json`/`.png`
pair. The tests assert the exit code *and* that neither artifact exists —
for the empty-list case the artifact is the whole defect, so asserting only
the exception type would have missed it. One test reads
`docs/threshold_demo.json` and pins its eight rows so the severity claim is
anchored rather than asserted in prose, and the `0.0` boundary gets its own
test since the guard rejects `< 0` and not `<= 0`, and `0.0` is the first row
of the committed default sweep.

No upper bound was added. Entropy has no natural ceiling here and inventing
one would be a guess; non-finite and negative are the two values the code
demonstrably cannot use.
