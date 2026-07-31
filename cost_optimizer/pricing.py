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
from typing import Any


def _coerce_token_count(value: Any) -> int:
    """Best-effort non-negative ``int`` token count from a usage field.

    Token usage is best-effort observability accounting, gathered *after* a
    request (``messages.create`` in the cache wrapper, a completed batch row in
    ``batch.py``) has already produced a valid response. A malformed usage
    field must therefore **abstain** (→ ``0``) rather than crash and destroy
    that successful response — the same "abstain, don't crash on malformed SDK
    shapes" contract #94/#106/#112 set for ``_extract_text`` and the logprob
    extractor, and #114 set for the cache-wrapper usage tokens.

    The bare ``int(value or 0)`` this replaces crashed on a present-but-
    malformed value: ``int(NaN)`` → ``ValueError``, ``int(inf)`` →
    ``OverflowError``, ``int("abc")`` → ``ValueError``. A finite, non-negative
    numeric (including a numeric string like ``"5"``) still coerces to ``int``
    unchanged; ``None``/falsy → ``0``; anything non-coercible or negative
    abstains to ``0`` (a negative token count is malformed and would otherwise
    poison ``tokens_cached`` / ``dollars_saved`` and the batch token totals).

    Lives here in the shared token-domain module so the cache wrapper (#114)
    and the batch-result parser (#136) share one implementation rather than
    diverging.
    """
    try:
        n = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return n if n >= 0 else 0


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
            # `isinstance` first (short-circuits before `math.isfinite`): a
            # present-but-non-numeric rate (a str/None from a JSON-decoded or
            # config-supplied pricing table) hit the bare `math.isfinite(value)`
            # and raised a raw TypeError instead of this field-named ValueError —
            # the pricing-layer sibling of the #142 `_validate_embedding` gap.
            # `bool` is excluded explicitly: it subclasses `int`, so a JSON
            # `true`/`false` passes `isinstance((int, float))` + `isfinite` +
            # `>= 0.0` and fabricates a rate (`True`→$1/MTok or a 1.0× multiplier,
            # `False`→a 0.0× free-cache multiplier) — the bool-is-int-subclass
            # class the portfolio-wide sweep already applied to the int fields in
            # `batch.py` and the config seams in the router (#158).
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0.0
            ):
                raise ValueError(f"{name} must be a finite number >= 0.0; got {value!r}")


# Input $/MTok. Update when Anthropic publishes new pricing; the rule is
# "cite the docs in the commit" rather than guess. The cache-write (1.25x)
# and cache-read (0.10x) multipliers are the documented Anthropic defaults
# (5-minute-TTL ephemeral caching) and ride on ModelPricing's field defaults.
#
# All Anthropic entries are taken from the published-pricing reference
# (current-models table, cached 2026-06-04): the whole Opus 4.6/4.7/4.8 family
# is $5.00 in / $25.00 out, Fable 5 $10.00 in / $50.00 out. Output rates are
# intentionally not modeled here (the caching math is input-only).
# claude-opus-4-8 and claude-fable-5 were added 2026-06 (#89); the opus-4-7 /
# opus-4-6 input price was refreshed from the stale $15.00 to the current
# $5.00 in 2026-06 (#90). opus-4-7 is the escalation target in
# scripts/bench_savings.py, so that refresh regenerated the savings benchmark
# and its README snapshot, not just this table.
_PRICING: dict[str, ModelPricing] = {
    "claude-fable-5": ModelPricing("claude-fable-5", 10.00),
    "claude-opus-4-8": ModelPricing("claude-opus-4-8", 5.00),
    "claude-opus-4-7": ModelPricing("claude-opus-4-7", 5.00),
    "claude-opus-4-6": ModelPricing("claude-opus-4-6", 5.00),
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
