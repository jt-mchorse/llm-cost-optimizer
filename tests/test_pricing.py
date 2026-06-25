"""Tests for the pricing table and ModelPricing validation.

The interesting invariant (#71): `ModelPricing.__post_init__` rejects both
negative and **non-finite** rates/multipliers. A sign-only check (`value <
0.0`) let `NaN`/`±Inf` through — `NaN < 0.0` and `float("inf") < 0.0` are both
False — and a non-finite rate silently poisons `cache_wrapper._dollars_saved`.
This mirrors the finiteness sweep already applied to `SemanticCache.default_ttl_s`
and the router signal thresholds (#36).
"""

from __future__ import annotations

import math

import pytest

from cost_optimizer.pricing import (
    ModelPricing,
    UnknownModelError,
    get_pricing,
    register_pricing,
)

_FIELDS = ("input_per_mtok", "cache_write_multiplier", "cache_read_multiplier")


class TestModelPricingValidation:
    @pytest.mark.parametrize("field", _FIELDS)
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_non_finite(self, field: str, bad: float) -> None:
        kwargs: dict[str, object] = {"model": "m", "input_per_mtok": 1.0}
        kwargs[field] = bad
        with pytest.raises(ValueError, match=rf"{field} must be a finite number >= 0\.0"):
            ModelPricing(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.parametrize("field", _FIELDS)
    def test_rejects_negative(self, field: str) -> None:
        # No regression: the original sign guard still fires.
        kwargs: dict[str, object] = {"model": "m", "input_per_mtok": 1.0}
        kwargs[field] = -0.01
        with pytest.raises(ValueError, match=rf"{field} must be a finite number >= 0\.0"):
            ModelPricing(**kwargs)  # type: ignore[arg-type]

    def test_rejects_empty_model(self) -> None:
        with pytest.raises(ValueError, match="model must be a non-empty string"):
            ModelPricing(model="", input_per_mtok=1.0)

    def test_accepts_finite_values_including_zero(self) -> None:
        # 0.0 is meaningful (a free model, or a fully-discounted read), so the
        # boundary must construct cleanly — the finiteness widening must not
        # tighten the lower bound past >= 0.0.
        p = ModelPricing(
            model="m",
            input_per_mtok=0.0,
            cache_write_multiplier=0.0,
            cache_read_multiplier=0.0,
        )
        assert p.input_per_mtok == 0.0
        assert p.cache_read_multiplier == 0.0

    def test_accepts_documented_defaults(self) -> None:
        p = ModelPricing(model="claude-haiku-4-5", input_per_mtok=1.0)
        assert math.isfinite(p.input_per_mtok)
        assert p.cache_write_multiplier == 1.25
        assert p.cache_read_multiplier == 0.10


class TestPricingTable:
    def test_get_known_model(self) -> None:
        p = get_pricing("claude-opus-4-7")
        assert p.model == "claude-opus-4-7"
        # Refreshed $15.00 -> $5.00 in #90 (Opus 4.6/4.7/4.8 family rate,
        # current-models reference cached 2026-06-04).
        assert p.input_per_mtok == 5.00

    def test_unknown_model_raises_with_known_list(self) -> None:
        with pytest.raises(UnknownModelError) as exc:
            get_pricing("gpt-made-up")
        # The error lists the known models so the caller can self-correct.
        assert "claude-haiku-4-5" in str(exc.value)

    def test_register_then_get_round_trip(self) -> None:
        register_pricing(ModelPricing(model="custom-test-model", input_per_mtok=2.5))
        p = get_pricing("custom-test-model")
        assert p.input_per_mtok == 2.5


class TestCurrentFrontierEntries:
    """The current-flagship entries added 2026-06 (#89).

    A cost-optimization toolkit must price the model new SDK work defaults
    to (`claude-opus-4-8`). These rates are sourced from the Anthropic
    published-pricing reference (current-models table, cached 2026-06-04):
    Opus 4.8 $5.00/MTok input, Fable 5 $10.00/MTok input. They were
    previously absent, so `get_pricing` raised `UnknownModelError`.
    """

    @pytest.mark.parametrize(
        ("model", "expected_input"),
        [
            ("claude-opus-4-8", 5.00),
            ("claude-fable-5", 10.00),
        ],
    )
    def test_input_rate_matches_published(self, model: str, expected_input: float) -> None:
        p = get_pricing(model)
        assert p.model == model
        assert p.input_per_mtok == expected_input

    @pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-fable-5"])
    def test_cache_multipliers_use_documented_defaults(self, model: str) -> None:
        # Anthropic's documented ephemeral-cache defaults: 1.25x write, 0.10x
        # read. The added entries pass no override, so they inherit the
        # ModelPricing field defaults -- locking that they weren't hand-set
        # to some other (invented) multiplier.
        p = get_pricing(model)
        assert p.cache_write_multiplier == 1.25
        assert p.cache_read_multiplier == 0.10

    def test_unknown_model_error_now_lists_new_models(self) -> None:
        # The known-models list in the error message is how a caller
        # self-corrects; the new flagships must appear there.
        with pytest.raises(UnknownModelError) as exc:
            get_pricing("gpt-made-up")
        msg = str(exc.value)
        assert "claude-opus-4-8" in msg
        assert "claude-fable-5" in msg
