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

## Benchmarks / Results
*Real-API savings benchmark pending — to be filed as a follow-up issue. The wrapper's `dollars_saved` math is unit-tested against the published Anthropic multipliers (`tests/test_cache_wrapper.py`).*

## Demo
*60-second demo pending.*

## Why these decisions
See [MEMORY/core_decisions_human.md](MEMORY/core_decisions_human.md).

## License
MIT
