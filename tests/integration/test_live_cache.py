"""Live Anthropic-API integration test for ``PromptCacheWrapper``.

Excluded from the main pytest collection (see ``pyproject.toml``'s
``testpaths`` + the ``conftest.py`` skip helper) because it requires a
real API key and burns budget. Runs only on a manual
``workflow_dispatch`` against the ``integration.yml`` workflow.

Acceptance shape (issue #7):

- Cold call writes some tokens into Anthropic's cache
  (``tokens_written > 0``).
- A follow-up call with the identical system prompt within the cache
  TTL reads those tokens back (``tokens_cached > 0``,
  ``dollars_saved > 0``).
- The test refuses to run if the estimated spend exceeds
  ``LIVE_CACHE_BUDGET_USD`` (default ``$0.10``) so a misconfigured
  environment can't silently burn through real money.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

# Force-skip the entire module when there's no API key. This is the
# load-bearing gate: every entry point — pytest directly, the workflow
# dispatch, an operator running locally — observes the same guard.
if not os.environ.get("ANTHROPIC_API_KEY"):
    pytest.skip(
        "ANTHROPIC_API_KEY not set; skipping live-cache integration test",
        allow_module_level=True,
    )

# Imported lazily after the skip so the module still imports cleanly
# under the unit-test sweep that runs without the SDK installed.
from anthropic import Anthropic  # noqa: E402

from cost_optimizer import PromptCacheWrapper  # noqa: E402
from cost_optimizer.pricing import get_pricing  # noqa: E402

# The API caches nothing shorter than the model's minimum cacheable prefix,
# which is per model and not monotonic: 4096 tokens on Claude Haiku 4.5, the
# default here, 512 on the newest models. A shorter prefix is not an error,
# just `cache_creation_input_tokens: 0`. This said ">= 1024" and shipped ~2.2k
# words, which the default model would not cache (#266). The prompt is now
# sized from the pricing table's minimum for the model under test, in words,
# with a 25% margin: English runs at least one token per word, so the word
# count is a floor on the token count.
_PARAGRAPH = (
    "This is a synthetic test for the prompt-cache wrapper's live path. "
    "Each repetition adds another sentence of plausibly-legal-document "
    "boilerplate so the input clears Anthropic's minimum-cacheable-prefix "
    "threshold. Nothing here is intended to be useful to the model. "
)
_LIVE_USER_PROMPT = "Reply with the single word: OK."

_DEFAULT_MODEL = os.environ.get("LIVE_CACHE_MODEL", "claude-haiku-4-5")
_DEFAULT_BUDGET_USD = float(os.environ.get("LIVE_CACHE_BUDGET_USD", "0.10"))


def _system_prompt_for(model: str) -> str:
    minimum = get_pricing(model).min_cacheable_tokens or 1024
    words_needed = math.ceil(minimum * 1.25)
    reps = math.ceil(words_needed / len(_PARAGRAPH.split()))
    return _PARAGRAPH * reps


_LIVE_SYSTEM_PROMPT = _system_prompt_for(_DEFAULT_MODEL)


def _estimate_max_cost_usd(model: str) -> float:
    """Upper-bound the cost of one cold + one warm call.

    Assumes the worst case of one input token per CHARACTER of the system
    prompt (an extreme over-estimate; real ratios are closer to 0.25), with
    the cold call's prefix at the 1.25x write rate. Measured from the prompt
    actually sent, not from a constant: the old docstring said "~3.6k chars"
    for a prompt of ~15.9k.
    """
    pricing = get_pricing(model)
    chars = len(_LIVE_SYSTEM_PROMPT) + len(_LIVE_USER_PROMPT)
    worst_case_input_tokens = chars * (pricing.cache_write_multiplier + 1)
    return worst_case_input_tokens * (pricing.input_per_mtok / 1_000_000)


def test_live_cache_cold_then_warm_round_trip():
    """Cold → warm round trip writes then reads cache tokens."""
    model = _DEFAULT_MODEL
    estimated = _estimate_max_cost_usd(model)
    budget = _DEFAULT_BUDGET_USD
    assert estimated <= budget, (
        f"Estimated worst-case cost ${estimated:.4f} exceeds LIVE_CACHE_BUDGET_USD "
        f"of ${budget:.4f}. Bump LIVE_CACHE_BUDGET_USD or shrink the prompt."
    )

    client = Anthropic()  # picks up ANTHROPIC_API_KEY from env
    wrapper = PromptCacheWrapper(client=client, model=model)

    # ---- cold call: should write tokens to cache ----
    cold = wrapper.create(
        system=_LIVE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _LIVE_USER_PROMPT}],
        max_tokens=16,
    )
    assert cold.telemetry.tokens_written > 0, (
        f"Expected cold call to write cache tokens, got {cold.telemetry!r}. "
        f"This usually means the system prompt was below {model}'s minimum cacheable "
        f"prefix ({get_pricing(model).min_cacheable_tokens} tokens in the pricing table)."
    )

    # ---- warm call: identical prefix within cache TTL ----
    warm = wrapper.create(
        system=_LIVE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _LIVE_USER_PROMPT}],
        max_tokens=16,
    )
    assert warm.telemetry.tokens_cached > 0, (
        f"Expected warm call to read cache tokens, got {warm.telemetry!r}. "
        f"Possible causes: cache TTL expired between calls (unlikely — the "
        f"calls fire back-to-back), system prompt differed between calls, or "
        f"cache_control wasn't applied. Inspect the wrapper's _apply_cache_control."
    )
    assert warm.telemetry.dollars_saved > 0, (
        f"Expected positive dollars_saved on a warm read, got {warm.telemetry.dollars_saved!r}."
    )

    # Sanity: aggregate counters reflect both calls.
    agg = wrapper.aggregate
    assert agg.misses >= 1
    assert agg.hits >= 1
    assert agg.tokens_written >= cold.telemetry.tokens_written
    assert agg.tokens_cached >= warm.telemetry.tokens_cached


def test_live_cache_budget_guardrail_is_under_default():
    """Module-level sanity: the synthetic prompt's worst-case cost fits the budget."""
    assert _estimate_max_cost_usd(_DEFAULT_MODEL) < _DEFAULT_BUDGET_USD


# Provenance for the reviewer: confirm the file lives where the
# workflow expects it (defense against an accidental rename breaking
# the dispatch run silently).
def test_module_path_is_under_integration():
    here = Path(__file__).resolve()
    assert here.parent.name == "integration"
    assert here.name == "test_live_cache.py"
