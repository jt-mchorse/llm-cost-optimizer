# Core Decisions

Strategic decisions for this repo, with reasoning. Append-only — superseded decisions are marked, not removed.

## D-001 — Scope locked to portfolio handoff §2 (2026-05-10)
**Decision:** Scope of this repo is fixed by the portfolio handoff document, section 2.

**Why:** The handoff spec was deliberated; ad-hoc scope expansion within a session is the failure mode this prevents.

**Alternatives considered:** None — this is a baseline.

**Reversibility:** Expensive. Scope changes require a deliberate revisit and a new decision entry.

**Related issues:** —

## D-002 — Dependency-free wrapper layer; Anthropic SDK is duck-typed (2026-05-14)
**Decision:** The `cost_optimizer` wrapper layer never imports `anthropic`. Clients are duck-typed against `client.messages.create(...)`.

**Why:** Three concrete benefits. (1) Tests run in CI with no API key — the suite is hermetic. (2) Downstream portfolio repos (`rag-production-kit`, `agent-orchestration-platform`) can embed the wrapper without inheriting an SDK dependency. (3) Mirrors the precedent set in `llm-eval-harness` PR #8, where the dataset layer is dep-free for the same reasons; keeping the portfolio internally consistent is itself a small but real win.

**Alternatives considered:**
- Hard-import the `anthropic` SDK — rejected because it forces a dep on every consumer of the wrapper and complicates testing without API keys.
- Vendor a typed `Protocol` module that explicitly mirrors the SDK shape — rejected as premature; the duck-typed `Protocol` already declared in `cache_wrapper.py` is sufficient and stays out of users' way.

**Reversibility:** Cheap. If the SDK adds features the wrapper needs to introspect directly, we can take a soft import dependency later without breaking callers.

**Related issues:** #1

## D-003 — In-repo pricing table; unknown models raise (2026-05-14)
**Decision:** Per-model input pricing and the cache write/read multipliers live in `cost_optimizer/pricing.py` as a small hand-curated table. `get_pricing()` raises `UnknownModelError` for any model not in the table rather than guessing.

**Why:** The whole point of this repo is producing dollar numbers a client can defend. A fabricated price quietly contaminates every downstream dashboard. Failing loud on unknown models forces the operator to add the price to the table — and to cite Anthropic's docs in the commit doing it — before any number ships.

**Alternatives considered:**
- Fetch pricing from Anthropic at runtime — rejected; no public pricing API exists and a scraped page would be a worse source of truth than a versioned file.
- Infer pricing from the model name prefix (e.g., everything starting with `claude-haiku-*` is $1/MTok) — rejected; brittle, and Anthropic has shipped same-family models at different prices before.

**Reversibility:** Cheap. The table is one file; swapping the lookup with an external source later is mechanical.

**Related issues:** #1

## D-004 — Semantic cache uses pluggable `Embedder` and `Storage` Protocols with dep-free defaults (2026-05-15)
**Decision:** `SemanticCache` takes an `Embedder` (single-method `embed(text) -> list[float]`) and a `Storage` (single-method-each `put`/`find_nearest`/`invalidate_by_tag`/`purge_expired`/`__len__`). Two implementations of each ship: `HashEmbedder` + `InMemoryStorage` are dep-free, ship in the base install, and let CI exercise the cache flow hermetically; `RedisStorage` is behind the new `[redis]` extra; production embedders are BYO via the Protocol.

**Why:** Same single-method Protocol seam adopted in `rag-production-kit` (Reranker, Embedder), `llm-eval-harness` (Backend), and `agent-orchestration-platform`'s use-case. The pattern is now portfolio-standard for test-substitution seams. The dep-free defaults are load-bearing: without them, every test that touches the cache would need a Redis container and a real embedder, which is exactly the friction the test substitution pattern is supposed to eliminate.

**Alternatives considered:**
- Hard-coded OpenAI embedder — rejected; locks consumers into one vendor and forces an SDK dep on tests.
- Hard-coded Redis storage — rejected; same lock-in plus tests need a container.
- `HashEmbedder` only, no Protocol — rejected; not real-quality and consumers can't swap in their production embedder.

**Reversibility:** Cheap. Both Protocols are single-method; adding optional methods is backwards-compatible.

**Related issues:** #2, #3, #5

## D-005 — Cache keys include the model id; separate cache entries per model (2026-05-15)
**Decision:** The cache's synthetic key is computed from `sha256(f"{model} {prompt}")` (and the embedding input is prefixed with `[model=...]` so similarity itself respects the model). The same prompt to two different models produces two cache entries.

**Why:** Different models give different responses. Serving a Haiku response to an Opus caller is a quality regression that the client never asked for. Model-scoped keys also mean a model upgrade automatically invalidates the cache for the entries it touches — no full flush, no stale-for-the-new-model entries.

**Alternatives considered:**
- Model-agnostic global pool — rejected; serves wrong-model responses.
- Separate `SemanticCache` instance per model — rejected; pushes the bookkeeping onto every consumer and breaks the offline false-positive measurement helper, which is single-cache by design.

**Reversibility:** Cheap. The key derivation is one method.

**Related issues:** #2

## D-006 — Default similarity threshold 0.95 (high on purpose) (2026-05-15)
**Decision:** `SemanticCache` defaults `similarity_threshold` to 0.95. Operators can lower it, but the default is conservative.

**Why:** False positives are user-visible bugs (cached answer served to a different question, agent acts on wrong data, etc.). False negatives are just cache misses — additional cost, but no quality regression. Tuning the default toward the safer failure mode means out-of-the-box behavior is "occasionally pay for a model call I didn't strictly need" rather than "occasionally serve wrong data." Operators who measure their false-positive rate and want more hits can lower the threshold; default users get the safer setting.

**Alternatives considered:**
- Default 0.85 for higher hit rate — rejected; trades quality for cost-savings in the default config, which is the wrong direction for a "production cost optimizer."
- No threshold (always serve the nearest) — rejected; pathological at low similarity (serving "what's the capital of Spain?" responses to "what's the weather?" queries).

**Reversibility:** Cheap. One constructor arg.

**Related issues:** #2

## D-007 — False-positive rate measured offline via helper, not online sampling (2026-05-15)
**Decision:** `measure_false_positive_rate(cache, held_out, model, call_model)` is run by the operator on a held-out set; the cache itself never samples cache hits and re-calls the model "just to check." False-positive rate is measured deliberately, not continuously.

**Why:** Online sampling (e.g., 5% of cache hits also call the model and compare) silently bleeds the cost savings the cache exists to deliver — and the savings rate compounds over time as cache hit-rate goes up. The honest design is to run the false-positive measurement explicitly as an operator-initiated step on a held-out evaluation set. The output is a number the operator commits to the dashboard repo (#5) alongside the savings number, both computed deliberately.

**Alternatives considered:**
- Online random sampling at X% — rejected; bleeds savings, hides cost in a place operators don't see.
- No false-positive metric — rejected; without measurement the cache is a black box and operators can't tune the threshold.

**Reversibility:** Cheap. The helper is small; online sampling can be added later as an opt-in mode.

**Related issues:** #2, #5

## D-008 — `EscalationSignal` is a one-method Protocol (2026-05-16)
**Decision:** Signals plug into the router via a `name` attribute and a `measure(response) -> SignalReading` method. Matches the same single-method-Protocol pattern used everywhere else in the portfolio (`Tool`, `Reranker`, `Embedder`, `Backend`, `AnswerSource`, `Storage`).

**Why:** Consumers should be able to bring their own signal without inheriting from an ABC or registering via a decorator. One method per Protocol is the smallest interface that still carries the signal's name (needed for `RouterDecision.triggered_signal` telemetry).

**Alternatives considered:**
- ABC with inheritance — rejected: heavier than needed; the portfolio standardized on Protocols precisely to avoid this.
- Callable function alias — rejected: loses the `name` metadata that `RouterDecision` needs.
- Plugin registration via decorator — rejected: too much overhead for what's effectively a two-line class.

**Reversibility:** Cheap. The Protocol has one method; adding fields is backwards-compatible.

**Related issues:** #3

## D-009 — Router returns a `RouterDecision` dataclass, not just a `model_id` string (2026-05-16)
**Decision:** `UncertaintyRouter.route()` returns a `RouterDecision` carrying `model_id`, `triggered_signal` (name of the signal that won, or `None`), `signal_values` (every signal's measurement, even after first-trip), and `cheap_response` (the cheap model's output, for inspection).

**Why:** The signal values are *telemetry*. The future savings dashboard (#5) attributes cost to specific signals — "75% of escalations were entropy-driven, 25% judge-driven, here's the cost breakdown" — and that's impossible if the router only returns the chosen model id. The first-trip-wins decision happens at runtime; the remaining signals are still measured because not measuring them would mean discarding free observability data.

**Alternatives considered:**
- Return just a `model_id` string — rejected: collapses the telemetry surface for no callsite simplification.
- Return a tuple — rejected: brittle, ambiguous, doesn't extend cleanly.
- Return only the tripped signal (None if none) — rejected: loses the non-tripping signals' values, which the dashboard needs.

**Reversibility:** Cheap. The dataclass can grow fields without breaking callers.

**Related issues:** #3, #5
