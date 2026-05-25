"""Tests for the Anthropic prompt-caching wrapper.

The wrapper is duck-typed against the Anthropic SDK, so these tests use a
hand-rolled ``FakeClient`` and never touch the network. That keeps the
suite hermetic — runnable in CI without secrets — which is also the
acceptance bar for issue #1.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from cost_optimizer import CacheTelemetry, PromptCacheWrapper
from cost_optimizer.cache_wrapper import (
    _mark_messages_prefix,
    _mark_system,
    _mark_tools,
)
from cost_optimizer.pricing import ModelPricing, UnknownModelError, get_pricing

# ---------- fake client ----------


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class _FakeMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._script: list[_Usage] = []

    def queue(self, usage: _Usage) -> None:
        self._script.append(usage)

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        usage = self._script.pop(0) if self._script else _Usage()
        return SimpleNamespace(usage=usage, content="ok")


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessages()


# ---------- cache_control injection ----------


def test_marks_system_string_as_single_cached_block():
    out = _mark_system("you are a precise assistant")
    assert out == [
        {
            "type": "text",
            "text": "you are a precise assistant",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_marks_system_blocks_on_last_block_only():
    blocks = [
        {"type": "text", "text": "general system"},
        {"type": "text", "text": "long policy text"},
    ]
    out = _mark_system(blocks)
    assert "cache_control" not in out[0]
    assert out[1]["cache_control"] == {"type": "ephemeral"}
    # input not mutated
    assert "cache_control" not in blocks[1]


def test_marks_tools_on_last_tool():
    tools = [{"name": "t1"}, {"name": "t2"}]
    out = _mark_tools(tools)
    assert "cache_control" not in out[0]
    assert out[1]["cache_control"] == {"type": "ephemeral"}


def test_marks_messages_prefix_string_content():
    msgs = [{"role": "user", "content": "hello"}]
    out = _mark_messages_prefix(msgs)
    assert out[0]["content"] == [
        {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}
    ]


def test_marks_messages_prefix_block_content_marks_last_block():
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "last"},
            ],
        }
    ]
    out = _mark_messages_prefix(msgs)
    assert "cache_control" not in out[0]["content"][0]
    assert out[0]["content"][1]["cache_control"] == {"type": "ephemeral"}


def test_wrapper_injects_cache_control_on_system_by_default():
    client = _FakeClient()
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    w.create(system="sysprompt", messages=[{"role": "user", "content": "hi"}])
    sent = client.messages.calls[0]
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    # messages_prefix is OFF by default, so messages are untouched
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


def test_wrapper_can_mark_multiple_segments():
    client = _FakeClient()
    w = PromptCacheWrapper(
        client,
        model="claude-haiku-4-5",
        cache_segments=("system", "tools", "messages_prefix"),
    )
    w.create(
        system="sys",
        tools=[{"name": "t"}],
        messages=[{"role": "user", "content": "hi"}],
    )
    sent = client.messages.calls[0]
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_wrapper_rejects_unknown_segments():
    client = _FakeClient()
    with pytest.raises(ValueError, match="Unknown cache_segments"):
        PromptCacheWrapper(client, model="claude-haiku-4-5", cache_segments=("bogus",))


def test_wrapper_defaults_model_into_kwargs():
    client = _FakeClient()
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    w.create(messages=[{"role": "user", "content": "hi"}])
    assert client.messages.calls[0]["model"] == "claude-haiku-4-5"


# ---------- telemetry: miss / hit / both ----------


def test_first_call_is_a_miss_with_tokens_written():
    client = _FakeClient()
    client.messages.queue(
        _Usage(input_tokens=100, cache_creation_input_tokens=2000, cache_read_input_tokens=0)
    )
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    result = w.create(system="sys", messages=[{"role": "user", "content": "hi"}])
    t = result.telemetry
    assert t.misses == 1
    assert t.hits == 0
    assert t.tokens_written == 2000
    assert t.tokens_cached == 0
    assert t.dollars_saved == 0.0  # writes are not savings


def test_warm_call_is_a_hit_with_dollars_saved():
    # haiku 4.5 input price = $1/MTok, read multiplier 0.1 → savings = 0.9/MTok of cached reads.
    client = _FakeClient()
    client.messages.queue(
        _Usage(input_tokens=20, cache_creation_input_tokens=0, cache_read_input_tokens=2000)
    )
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    result = w.create(system="sys", messages=[{"role": "user", "content": "hi"}])
    t = result.telemetry
    assert t.misses == 0
    assert t.hits == 1
    assert t.tokens_cached == 2000
    assert t.tokens_written == 0
    # savings = 2000 * (1/1e6) * (1 - 0.1) = $0.0018
    assert t.dollars_saved == pytest.approx(0.0018, rel=1e-6)


def test_partial_cache_call_counts_as_both_hit_and_miss():
    """A call that reads from cache AND writes new prefix is hit+miss."""
    client = _FakeClient()
    client.messages.queue(
        _Usage(
            input_tokens=50,
            cache_creation_input_tokens=500,
            cache_read_input_tokens=1500,
        )
    )
    w = PromptCacheWrapper(client, model="claude-sonnet-4-6")
    result = w.create(system="sys", messages=[{"role": "user", "content": "hi"}])
    t = result.telemetry
    assert t.hits == 1
    assert t.misses == 1
    assert t.tokens_cached == 1500
    assert t.tokens_written == 500


def test_aggregate_telemetry_accumulates_across_calls():
    client = _FakeClient()
    client.messages.queue(_Usage(cache_creation_input_tokens=1000))
    client.messages.queue(_Usage(cache_read_input_tokens=1000))
    client.messages.queue(_Usage(cache_read_input_tokens=2000))
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    for _ in range(3):
        w.create(system="sys", messages=[{"role": "user", "content": "x"}])
    agg = w.aggregate
    assert agg.misses == 1
    assert agg.hits == 2
    assert agg.tokens_cached == 3000
    assert agg.tokens_written == 1000
    # savings = 3000 * (1/1e6) * 0.9 = $0.0027
    assert agg.dollars_saved == pytest.approx(0.0027, rel=1e-6)


def test_reset_clears_aggregate_only():
    client = _FakeClient()
    client.messages.queue(_Usage(cache_read_input_tokens=1000))
    w = PromptCacheWrapper(client, model="claude-haiku-4-5")
    w.create(system="sys", messages=[{"role": "user", "content": "x"}])
    assert w.aggregate.hits == 1
    w.reset()
    assert w.aggregate == CacheTelemetry.zero()


def test_dict_shaped_response_usage_is_read():
    """Some HTTP-style clients return dicts rather than SDK objects."""

    class DictClient:
        def __init__(self) -> None:
            self.messages = self  # self.messages.create(...)

        def create(self, **kwargs: Any) -> dict[str, Any]:
            return {"usage": {"cache_read_input_tokens": 500}, "content": "ok"}

    w = PromptCacheWrapper(DictClient(), model="claude-haiku-4-5")
    result = w.create(system="sys", messages=[{"role": "user", "content": "x"}])
    assert result.telemetry.tokens_cached == 500
    assert result.telemetry.hits == 1


# ---------- pricing ----------


def test_get_pricing_returns_known_model():
    p = get_pricing("claude-haiku-4-5")
    assert p.input_per_mtok == 1.00
    assert p.cache_read_multiplier == 0.10
    assert p.cache_write_multiplier == 1.25


def test_get_pricing_raises_on_unknown_model():
    with pytest.raises(UnknownModelError, match="No pricing recorded"):
        get_pricing("totally-fake-model-9000")


def test_wrapper_accepts_explicit_pricing_override():
    client = _FakeClient()
    client.messages.queue(_Usage(cache_read_input_tokens=1_000_000))
    custom = ModelPricing(model="custom-x", input_per_mtok=10.0)
    w = PromptCacheWrapper(client, model="custom-x", pricing=custom)
    result = w.create(system="sys", messages=[{"role": "user", "content": "x"}])
    # 1M cached tokens at $10/MTok input, 90% off = $9 saved
    assert result.telemetry.dollars_saved == pytest.approx(9.0, rel=1e-9)


# Issue #34: ModelPricing rejects negative rates/multipliers and empty model.
# Negative values silently invert the sign of dollars_saved at
# cache_wrapper.py:177-179; D-003 requires savings math be traceable to a
# documented rate, never fabricated — extend that from "no invented model"
# to "no invented numbers within a known model".
@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("input_per_mtok", -0.01),
        ("input_per_mtok", -100.0),
        ("cache_write_multiplier", -0.01),
        ("cache_write_multiplier", -2.0),
        ("cache_read_multiplier", -0.01),
        ("cache_read_multiplier", -1.0),
    ],
)
def test_model_pricing_rejects_negative_numeric_field(field: str, bad_value: float):
    kwargs: dict[str, float | str] = {
        "model": "custom-x",
        "input_per_mtok": 1.0,
    }
    kwargs[field] = bad_value
    with pytest.raises(ValueError, match=rf"{field} must be >= 0\.0"):
        ModelPricing(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_model", ["", None, 123])
def test_model_pricing_rejects_invalid_model_string(bad_model: object):
    with pytest.raises(ValueError, match="model must be a non-empty string"):
        ModelPricing(model=bad_model, input_per_mtok=1.0)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    ["input_per_mtok", "cache_write_multiplier", "cache_read_multiplier"],
)
def test_model_pricing_accepts_zero_for_numeric_fields(field: str):
    # Zero is meaningful: free inputs / zero-cost cache writes / zero-cost
    # cache reads are all valid for synthetic-workload testing scenarios.
    kwargs: dict[str, float | str] = {
        "model": "custom-x",
        "input_per_mtok": 1.0,
    }
    kwargs[field] = 0.0
    # No raise; constructor returns a valid instance.
    p = ModelPricing(**kwargs)  # type: ignore[arg-type]
    assert getattr(p, field) == 0.0


def test_model_pricing_builtin_table_loads_under_new_validator():
    # Smoke-test that the four built-in entries at pricing.py:42-45 still
    # construct cleanly under __post_init__. Pins against accidental
    # regression of the table's literals.
    for model in ("claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"):
        p = get_pricing(model)
        assert p.model == model
        assert p.input_per_mtok >= 0.0
