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

## D-010 — Batch idempotency is caller-supplied key + payload content hash; conflict raises (2026-05-16)
**Decision:** `cost_optimizer.batch` deduplicates batch submissions on two coordinates: an `idempotency_key` the caller supplies and a content hash the backend computes over the canonical request payload (request count, custom_ids, prompts, model, max_tokens, system). Resubmitting *the same payload* with *the same key* returns the existing `job_id`. Resubmitting *a different payload* with *the same key* raises `IdempotencyConflict` rather than overwriting.

**Why:** Two failure modes have to be handled at once. The first is **the flaky-retry path**: a caller submits a batch, the network blips, they retry — they should get back the same `job_id`, not double-charge. The second is **the accidental-key-reuse path**: a caller writes `idempotency_key=f"daily-batch-{date}"` and runs a different workload through the same code path tomorrow — they should fail loud (the key is being asked to dedupe something it can't see), not silently overwrite the prior submission's mapping or run two different jobs that share an id. The content hash catches the second case by detecting that the payload changed while the key stayed the same. Together: caller-supplied key dedupes retries (the caller knows the operation is idempotent because they're saying so); content hash defends against the caller being wrong about that.

**Alternatives considered:**
- Server-generated idempotency keys — rejected; couples the local in-memory backend to an Anthropic-specific endpoint surface and removes the caller's ability to express "this is the same logical submission" from the call site.
- Key-only, no content hash — rejected; silently overwrites the prior submission when a key is reused for a different payload, which is exactly the failure mode this contract is meant to prevent.
- Content-hash only, no caller key — rejected; the caller must be able to express idempotency *before* they've serialized the payload (e.g., when computing requests lazily). A caller key is also necessary to dedupe submissions that semantically should be the same workload but happen to produce slightly different payloads.

**Reversibility:** Cheap. The hash is one function (`_canonical_payload_hash`) and the lookup table is one dict on `InMemoryBatchBackend`; changing the canonicalization rule is a localized edit. The Anthropic-backed backend forwards the key via the SDK's standard `Idempotency-Key` header.

**Related issues:** #4

## D-011 — Savings dashboard is Streamlit behind a `[dashboard]` optional extra (2026-05-17)
**Decision:** The savings dashboard is `dashboard/app.py`, a Streamlit page that reads the JSON `scripts/bench_savings.py` produces. Streamlit + pandas live in a new `[dashboard]` optional extra in `pyproject.toml`; nothing in the core `cost_optimizer` package imports either.

**Why:** The pattern is already in the repo — `RedisStorage` is behind a `[redis]` extra (D-004) because production callers BYO that dep; the dep-free defaults are what CI exercises. The savings dashboard fits the same shape: most users of this library will read the README table; the operators who want the interactive view install one more extra. Streamlit was picked over Next.js because the entire package is Python and adding a JS toolchain would double the build matrix for one feature; static HTML was rejected because the acceptance criteria call for an interactive dashboard with charts that update when the operator re-runs the bench. Critically, the dashboard does **no** recomputation — it reads what's on disk. That keeps the README table and the dashboard in sync via the file, not via background re-execution.

**Alternatives considered:**
- Next.js dashboard — rejected; doubles the build matrix for a single feature in a Python-only repo.
- Static HTML report — rejected; acceptance criteria want interactive charts that update without rebuilding HTML.
- Dashboard recomputes savings inline from the workload — rejected; the bench artifacts and the dashboard would drift, and the operator's committed numbers wouldn't be what the page rendered.

**Reversibility:** Cheap. The dashboard reads the JSON schema versioned at `schema_version: 1`; the schema is small and the import surface is single-file.

**Related issues:** #5

## D-012 — Bench workload is hermetic synthetic with documented composition; real-API mode is operator-triggered (2026-05-17)
**Decision:** `scripts/bench_savings.py` runs a deterministic 500-row synthetic workload (60% redundant template paraphrases / 30% easy factual / 10% hard open-ended) through every shipped strategy. Token counts and first-token logprobs are canned in `docs/savings_workload.json` so the numbers are bit-for-bit reproducible. The script supports `--dry` (default, what CI runs) and explicitly errors out if `--dry` is overridden because real-API mode is not implemented in this PR. The operator wires real adapters and commits `docs/savings_real.md` once they've vetted the curve.

**Why:** Two pieces. **First**, the no-fabricated-benchmarks rule (handoff §10) means the README either cites measured numbers or admits the benchmark is pending. The hermetic synthetic gives us the first: every number in the README is what the script actually computed, reconciled in the test suite against two independent derivations (the strategy summary and the cumulative series). **Second**, real-API runs against a real workload are valuable but not appropriate for CI — they require an Anthropic API key, real spend, and an opinion about which dataset is representative. The threshold-sweep script (`scripts/tune_threshold.py`) already established the posture: ship the plumbing, let the operator run the measurement. D-007 is the same posture for false-positive measurement on the semantic cache; D-012 extends it to the savings dashboard.

**Alternatives considered:**
- HF dataset slice at bench time — rejected; breaks hermetic CI and introduces a network dependency on dataset hosting at test time.
- Fabricated savings numbers in the README — rejected; explicitly forbidden by handoff §10.
- Real-API mode in this PR — rejected; requires API key + budget + dataset opinion; pushes the bench off the CI path entirely; future PR can land it without disturbing the synthetic baseline this PR commits.

**Reversibility:** Cheap. Swapping the workload generator is one function (`_build_workload`); the strategy runners and the JSON schema are workload-agnostic. The operator can add a real-API mode by replacing the stub adapters with real ones — the script's flag is already there, it just refuses to run today.

**Related issues:** #5

## D-014 — Non-strict mypy gate as the baseline strictness bar (2026-07-07)
**Decision:** Adopt a non-strict `mypy` gate for `cost_optimizer` as the baseline strictness bar, wired into CI (`ci.yml` lint job) and locked by `tests/test_mypy_clean.py`. Config in `pyproject.toml` `[tool.mypy]`: no blanket `ignore_missing_imports`, a per-module override for the optional `redis` SDK only, and `warn_unused_ignores` + `warn_redundant_casts` on. (Note: D-013 is intentionally reserved for the in-flight #97 batch-idempotency decision-revisit on draft PR #124, so this took the next free id, D-014.)

**Why:** #127 shipped a `py.typed` marker so `cost_optimizer`'s annotations are visible to downstream type-checkers, but nothing machine-checked them in this repo — they could silently drift. A gate keeps them honest. Non-strict is the right starting bar: the 5 pre-existing errors were all redis-py `ResponseT` (`Awaitable[Any] | Any`) stub-noise in `semantic_cache.py`, resolved with narrow `cast()`s at the storage boundary (the sync `redis.Redis` client never takes the async arm) — no correctness bugs needing strict-mode machinery. Declining the blanket `ignore_missing_imports` keeps a mistyped import surfacing; the per-module override scoped to `redis.*` handles the one genuinely-optional dependency and, being config rather than an inline ignore, stays clean whether or not the `redis` extra is installed (verified both ways). Mirrors the sibling gate landed the same session in `llm-eval-harness` (D-016).

**Alternatives considered:**
- Full strict mode now — rejected; churn without correctness value. Tightening is a follow-up.
- Blanket `ignore_missing_imports = true` — rejected; would silently swallow a typo'd import. Only `redis` needs the escape hatch, so a per-module override is more precise.
- Blanket `# type: ignore` on the redis calls instead of casts — rejected; the issue explicitly preferred narrow `cast()`s, which assert the real sync-client return contract rather than suppressing the error.
- pyright instead of mypy — rejected; mypy is the portfolio convention and installs cleanly into the existing `dev` extra.

**Reversibility:** Cheap. The strictness bar is a few config lines; tighten or swap the checker in a follow-up.

**Related issues:** #127, #129

## D-015 — a cache payload must survive a JSON round-trip unchanged (2026-08-25)

**Decision.** `SemanticCache.put` rejects any payload that is not built from
`str`, `int`, `float`, `bool`, `None`, `list`, and `dict` with string keys,
nested arbitrarily. The check runs at the `put` seam, before any backend is
touched.

**Why.** D-004 makes `Storage` a pluggable Protocol with a dep-free default and
a Redis implementation for production. But `put` took `payload: Any` and
validated nothing, and the two backends disagreed about what that meant:
`InMemoryStorage` `deepcopy`s the payload, preserving exact types, while
`RedisStorage` `json.dumps` it. Driving twelve payload shapes through the
identical public API, six diverged. A `tuple` came back as a `list` and an
int-keyed dict came back str-keyed — silently, so `payload[1]` worked on the
default backend and raised `KeyError: 1` on Redis for an entry the cache had
just reported as a *hit*. A `set`, `bytes`, or `datetime` payload raised
`TypeError` from inside `RedisStorage.put`, meaning a request failed *because it
tried to cache its own result* — and only after the operator made the backend
swap that `RedisStorage`'s own docstring recommends. Code developed and tested
against the default backend passed, and broke on the day of the switch.

Validating at the `put` seam is the placement this function already uses for its
other two arguments (`ttl_s`, and the embedder output via `_validate_embedding`),
and it makes every backend fail the same way in the same place.

**Alternatives considered.** *Normalize instead of reject* — run every payload
through a JSON round-trip so both backends agree on `list`. Rejected: it
silently changes what the default backend serves today, and `set`/`bytes`/
`datetime` would still fail. *Document without enforcing* — rejected, because
the failure is backend-conditional, so documentation alone leaves the break in
production. *Validate inside each backend* — rejected; a rule living in two
implementations is exactly what let them diverge. *Use
`json.loads(json.dumps(x)) == x` as the test* — rejected, because `nan != nan`
and `NaN`/`Infinity` payload values already agree across both backends, so that
form would reject shapes that are not part of this defect.

**Reversibility.** Cheap — delete one call site. Worth recording anyway because
it narrows a public input domain that was `Any`: a caller storing a tuple
payload on the in-memory backend now gets a `ValueError`. That is the intended
outcome, not a side effect — that caller was one config change away from being
served a `list`.
