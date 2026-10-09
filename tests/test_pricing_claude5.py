"""The Claude 5 models are priced, at their published rates (#289).

`_PRICING` had no row for claude-opus-5-5 (the current default), opus-5,
sonnet-5-5, sonnet-5 or fable-5-1: `get_pricing` raised `UnknownModelError`
for each, so `PromptCacheWrapper` could not be built for them. Rates are from
Anthropic's current-models table and prompt-caching economics (cached
2026-09-25). Two cache-read rates differ from the 0.10x default: Opus 5.5 reads
at $0.20/MTok (0.05x) and Fable 5.1 at $0.25/MTok (0.025x).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cost_optimizer.cache_wrapper import PromptCacheWrapper
from cost_optimizer.pricing import get_pricing

# model, input $/MTok, cache-read $/MTok, minimum cacheable prefix
PUBLISHED = [
    ("claude-fable-5-1", 10.00, 0.25, 512),
    ("claude-opus-5-5", 4.00, 0.20, 512),
    ("claude-opus-5", 5.00, 0.50, 512),
    ("claude-sonnet-5-5", 2.00, 0.20, 512),
    ("claude-sonnet-5", 2.00, 0.20, 1024),
]


@pytest.mark.parametrize(("model", "input_rate", "read_rate", "minimum"), PUBLISHED)
def test_published_rates(model: str, input_rate: float, read_rate: float, minimum: int) -> None:
    p = get_pricing(model)
    assert p.model == model
    assert p.input_per_mtok == input_rate
    assert p.input_per_mtok * p.cache_read_multiplier == pytest.approx(read_rate)
    assert p.cache_write_multiplier == 1.25
    assert p.cache_write_1h_multiplier == 2.0
    assert p.min_cacheable_tokens == minimum


@pytest.mark.parametrize("model", [row[0] for row in PUBLISHED])
def test_the_cache_wrapper_can_be_built_for_it(model: str) -> None:
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: None))
    PromptCacheWrapper(client, model)
