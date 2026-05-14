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
