"""Model pricing table for cache savings math.

Anthropic prompt caching has two cost multipliers vs. baseline input price:

- Cache **write** (a.k.a. cache creation): 1.25× the input rate. This is the
  surcharge paid the first time a prefix is cached.
- Cache **read** (a.k.a. cache hit): 0.10× the input rate. This is the
  90%-discounted rate on subsequent reads of the cached prefix.

The pricing is the model's standard *input* per-MTok price; the multipliers
above apply uniformly across the current Claude family. Output tokens are
unrelated to caching and intentionally not modeled here.

Sources: https://docs.anthropic.com/en/docs/prompt-caching (verify at use
time — these numbers move). The table below is a small, hand-curated set;
unknown models raise so callers don't accidentally compute savings against
an invented price.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    """Per-million-token pricing for a single model.

    `input_per_mtok` is in USD. The cache multipliers default to Anthropic's
    documented values and can be overridden if a future model deviates.
    """

    model: str
    input_per_mtok: float
    cache_write_multiplier: float = 1.25
    cache_read_multiplier: float = 0.10

    def __post_init__(self) -> None:
        # D-003 extends from "no invented model" to "no invented numbers within
        # a known model": a negative rate or multiplier silently inverts the
        # sign of dollars_saved at cache_wrapper.py:177-179.
        #
        # The sign-only check is also widened to finiteness (#71), matching the
        # portfolio-wide sweep already applied to SemanticCache.default_ttl_s
        # and the router signal thresholds (#36): `NaN < 0.0` and
        # `float("inf") < 0.0` are both False, so a non-finite rate or
        # multiplier slipped past the negative guard and poisoned
        # `_dollars_saved` — a NaN rate makes dollars_saved NaN, +Inf makes it
        # Inf — propagating silently through the aggregate into the savings
        # dashboard with no diagnostic.
        if not isinstance(self.model, str) or not self.model:
            raise ValueError(f"model must be a non-empty string; got {self.model!r}")
        for name, value in (
            ("input_per_mtok", self.input_per_mtok),
            ("cache_write_multiplier", self.cache_write_multiplier),
            ("cache_read_multiplier", self.cache_read_multiplier),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be a finite number >= 0.0; got {value}")


# Input $/MTok. Update when Anthropic publishes new pricing; the rule is
# "cite the docs in the commit" rather than guess. The cache-write (1.25x)
# and cache-read (0.10x) multipliers are the documented Anthropic defaults
# (5-minute-TTL ephemeral caching) and ride on ModelPricing's field defaults.
#
# claude-opus-4-8 and claude-fable-5 added 2026-06 from the Anthropic
# published-pricing reference (current-models table, cached 2026-06-04):
# Opus 4.8 $5.00 in / $25.00 out, Fable 5 $10.00 in / $50.00 out. Output
# rates are intentionally not modeled here (the caching math is input-only).
#
# NOTE: the claude-opus-4-7 / claude-opus-4-6 entries below predate that
# reference and are stale -- current published input pricing for the whole
# Opus 4.6/4.7/4.8 family is $5.00/MTok, not $15.00. Correcting them is
# deferred to a separate issue because claude-opus-4-7 is the escalation
# target in scripts/bench_savings.py, so the change regenerates the savings
# benchmark and its README snapshot rather than being a one-line edit.
_PRICING: dict[str, ModelPricing] = {
    "claude-fable-5": ModelPricing("claude-fable-5", 10.00),
    "claude-opus-4-8": ModelPricing("claude-opus-4-8", 5.00),
    "claude-opus-4-7": ModelPricing("claude-opus-4-7", 15.00),
    "claude-opus-4-6": ModelPricing("claude-opus-4-6", 15.00),
    "claude-sonnet-4-6": ModelPricing("claude-sonnet-4-6", 3.00),
    "claude-haiku-4-5": ModelPricing("claude-haiku-4-5", 1.00),
}


class UnknownModelError(KeyError):
    """Raised when pricing is requested for a model not in the table."""


def get_pricing(model: str) -> ModelPricing:
    """Return the pricing entry for `model`, or raise `UnknownModelError`.

    We refuse to invent a price for an unknown model so cost numbers
    surfaced to users are always backed by a recorded rate.
    """
    try:
        return _PRICING[model]
    except KeyError as exc:
        known = ", ".join(sorted(_PRICING))
        raise UnknownModelError(
            f"No pricing recorded for model {model!r}. Known: {known}. "
            f"Pass an explicit ModelPricing to PromptCacheWrapper to override."
        ) from exc


def register_pricing(pricing: ModelPricing) -> None:
    """Register a custom pricing entry (e.g., for a not-yet-listed model).

    Intentionally process-local: callers wire their own price rather than
    monkey-patching production state across imports.
    """
    _PRICING[pricing.model] = pricing
