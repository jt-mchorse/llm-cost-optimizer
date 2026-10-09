"""1-hour cache TTL: kept when the caller sets it, and priced at 2x (#271).

Two defects in one feature, measured on `main`:

* `_mark_system` / `_mark_tools` / `_mark_messages_prefix` assigned
  `{"type": "ephemeral"}` over a caller's existing
  `{"type": "ephemeral", "ttl": "1h"}`. The client received the 5-minute
  default on all three segments.
* The write premium read only `cache_creation_input_tokens` and priced it at
  1.25x. A 1h write bills at 2x (`usage.cache_creation.ephemeral_1h_input_tokens`),
  so 100k 1h-written tokens on claude-opus-4-8 reported a premium of $0.125
  where the truth is $0.50: the loss was understated 4x.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from cost_optimizer.cache_wrapper import PromptCacheWrapper
from cost_optimizer.pricing import ModelPricing

ONE_HOUR = {"type": "ephemeral", "ttl": "1h"}
MODEL = "claude-opus-4-8"  # $5.00 / MTok


class _Recording:
    def __init__(self, usage: Any = None) -> None:
        self.sent: dict[str, Any] = {}
        self.usage = usage or types.SimpleNamespace(
            cache_creation_input_tokens=0, cache_read_input_tokens=0
        )
        self.messages = self

    def create(self, **kwargs: Any) -> Any:
        self.sent = kwargs
        return types.SimpleNamespace(usage=self.usage)


def test_a_callers_1h_ttl_survives_on_all_three_segments() -> None:
    client = _Recording()
    wrapper = PromptCacheWrapper(
        client, MODEL, cache_segments=("system", "tools", "messages_prefix")
    )
    wrapper.create(
        system=[{"type": "text", "text": "policy", "cache_control": ONE_HOUR}],
        tools=[{"name": "t", "input_schema": {}, "cache_control": ONE_HOUR}],
        messages=[
            {"role": "user", "content": [{"type": "text", "text": "q", "cache_control": ONE_HOUR}]}
        ],
    )
    assert client.sent["system"][-1]["cache_control"] == ONE_HOUR
    assert client.sent["tools"][-1]["cache_control"] == ONE_HOUR
    assert client.sent["messages"][-1]["content"][-1]["cache_control"] == ONE_HOUR


def test_unmarked_blocks_still_get_the_default(  # control
) -> None:
    client = _Recording()
    wrapper = PromptCacheWrapper(
        client, MODEL, cache_segments=("system", "tools", "messages_prefix")
    )
    wrapper.create(
        system="policy",
        tools=[{"name": "t", "input_schema": {}}],
        messages=[{"role": "user", "content": "q"}],
    )
    assert client.sent["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert client.sent["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert client.sent["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}


def _premium(usage: Any) -> float:
    return (
        PromptCacheWrapper(_Recording(usage), MODEL)
        .create(system="s", messages=[])
        .telemetry.dollars_write_premium
    )


@pytest.mark.parametrize("as_dict", [False, True], ids=["object", "dict"])
def test_1h_writes_are_priced_at_the_1h_rate(as_dict: bool) -> None:
    breakdown = {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 100_000}
    usage = types.SimpleNamespace(
        cache_creation_input_tokens=100_000,
        cache_read_input_tokens=0,
        cache_creation=breakdown if as_dict else types.SimpleNamespace(**breakdown),
    )
    assert _premium(usage) == pytest.approx(100_000 * 5e-6 * 1.0)  # (2.0 - 1.0)


def test_a_mixed_write_prices_each_ttl_at_its_own_rate() -> None:
    usage = types.SimpleNamespace(
        cache_creation_input_tokens=100_000,
        cache_read_input_tokens=0,
        cache_creation=types.SimpleNamespace(
            ephemeral_5m_input_tokens=60_000, ephemeral_1h_input_tokens=40_000
        ),
    )
    assert _premium(usage) == pytest.approx(60_000 * 5e-6 * 0.25 + 40_000 * 5e-6 * 1.0)


def test_no_breakdown_prices_every_write_at_the_5m_rate(  # back-compat control
) -> None:
    usage = types.SimpleNamespace(cache_creation_input_tokens=100_000, cache_read_input_tokens=0)
    assert _premium(usage) == pytest.approx(0.125)


@pytest.mark.parametrize("bad", ["lots", None, float("nan"), -5, [1]])
def test_a_malformed_1h_count_abstains_to_the_5m_rate(bad: object) -> None:
    usage = types.SimpleNamespace(
        cache_creation_input_tokens=100_000,
        cache_read_input_tokens=0,
        cache_creation=types.SimpleNamespace(ephemeral_1h_input_tokens=bad),
    )
    assert _premium(usage) == pytest.approx(0.125)


def test_an_over_reported_1h_count_is_clamped_to_what_was_written() -> None:
    usage = types.SimpleNamespace(
        cache_creation_input_tokens=10_000,
        cache_read_input_tokens=0,
        cache_creation=types.SimpleNamespace(ephemeral_1h_input_tokens=1_000_000),
    )
    assert _premium(usage) == pytest.approx(10_000 * 5e-6 * 1.0)


@pytest.mark.parametrize("bad", [-1.0, float("nan"), True, "2"])
def test_the_1h_multiplier_is_validated_like_the_others(bad: object) -> None:
    with pytest.raises(ValueError, match="cache_write_1h_multiplier"):
        ModelPricing(model="m", input_per_mtok=1.0, cache_write_1h_multiplier=bad)  # type: ignore[arg-type]
