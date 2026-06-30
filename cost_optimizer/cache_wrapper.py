"""Anthropic prompt-caching wrapper with savings telemetry.

This wraps the ``messages.create`` call on an Anthropic-style client. The
wrapper:

1. **Marks** caller-chosen prefix segments with
   ``cache_control: {"type": "ephemeral"}`` so Anthropic can cache them.
2. **Reads** ``usage.cache_creation_input_tokens`` and
   ``usage.cache_read_input_tokens`` off the response and surfaces them as
   a structured :class:`CacheTelemetry` value per call.
3. **Aggregates** telemetry across all calls made through the wrapper, so
   callers can read a single rolled-up number for dashboards or logs.

The client is duck-typed — the wrapper never imports ``anthropic``. Any
object exposing ``client.messages.create(...)`` works, which keeps the
wrapper testable with a fake client and importable without an API key.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cost_optimizer.io_utils import atomic_write_text
from cost_optimizer.pricing import ModelPricing, get_pricing


@dataclass(frozen=True)
class CacheTelemetry:
    """Cache activity for a single call (or an aggregate across calls)."""

    hits: int
    misses: int
    tokens_cached: int
    tokens_written: int
    dollars_saved: float

    @classmethod
    def zero(cls) -> CacheTelemetry:
        return cls(0, 0, 0, 0, 0.0)

    def merge(self, other: CacheTelemetry) -> CacheTelemetry:
        return CacheTelemetry(
            hits=self.hits + other.hits,
            misses=self.misses + other.misses,
            tokens_cached=self.tokens_cached + other.tokens_cached,
            tokens_written=self.tokens_written + other.tokens_written,
            dollars_saved=self.dollars_saved + other.dollars_saved,
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-stable dict for observability/logging sinks.

        Locked shape so downstream consumers can parse without knowing
        the dataclass field order. Pairs with
        ``PromptCacheWrapper.dump_aggregate_json`` for the on-disk
        path; metric backends like statsd/prometheus consume the
        in-process dict directly.
        """
        return {
            "hits": self.hits,
            "misses": self.misses,
            "tokens_cached": self.tokens_cached,
            "tokens_written": self.tokens_written,
            "dollars_saved": self.dollars_saved,
        }


@dataclass(frozen=True)
class CallResult:
    """The underlying response plus the cache telemetry for that one call."""

    response: Any
    telemetry: CacheTelemetry


class _MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


class _AnthropicLike(Protocol):
    messages: _MessagesAPI


CacheSegment = str  # "system" | "tools" | "messages_prefix"


_DEFAULT_SEGMENTS: tuple[CacheSegment, ...] = ("system",)
_VALID_SEGMENTS: frozenset[CacheSegment] = frozenset({"system", "tools", "messages_prefix"})


class PromptCacheWrapper:
    """Thin wrapper around an Anthropic-style client that opts content into
    prompt caching and surfaces cache telemetry.

    Parameters
    ----------
    client:
        Any object exposing ``client.messages.create(...)``. The Anthropic
        Python SDK works as-is; tests can pass a fake.
    model:
        The model name to pass through to ``messages.create`` and to look
        up pricing.
    cache_segments:
        Which prefix segments to mark as cacheable. Defaults to
        ``("system",)`` because the system prompt is the highest-leverage
        cacheable surface for most apps. Supported values: ``"system"``,
        ``"tools"``, ``"messages_prefix"``.
    pricing:
        Optional override; if ``None``, looked up from the pricing table.
    """

    def __init__(
        self,
        client: _AnthropicLike,
        model: str,
        *,
        cache_segments: Sequence[CacheSegment] = _DEFAULT_SEGMENTS,
        pricing: ModelPricing | None = None,
    ) -> None:
        unknown = set(cache_segments) - _VALID_SEGMENTS
        if unknown:
            raise ValueError(
                f"Unknown cache_segments: {sorted(unknown)}. Valid: {sorted(_VALID_SEGMENTS)}."
            )
        self._client = client
        self._model = model
        self._cache_segments = tuple(cache_segments)
        self._pricing = pricing or get_pricing(model)
        self._aggregate = CacheTelemetry.zero()

    # ----- public API -----

    @property
    def aggregate(self) -> CacheTelemetry:
        """Cumulative telemetry across every call made through this wrapper."""
        return self._aggregate

    def reset(self) -> None:
        """Clear the aggregate counters. Per-call telemetry is unaffected."""
        self._aggregate = CacheTelemetry.zero()

    def dump_aggregate_json(self, path: str | Path) -> None:
        """Write the current aggregate telemetry to ``path`` as JSON.

        Atomic on POSIX — uses ``cost_optimizer.io_utils.atomic_write_text``
        so a Ctrl-C / disk-full / OOM between truncate and flush can't
        leave the consumer reading a half-written file. Same pattern the
        bench writer uses for ``docs/savings.json``; this surface is for
        runtime aggregation against a long-lived wrapper.

        The on-disk shape is ``CacheTelemetry.to_dict()`` with sorted
        keys and a final newline. Operators can tail / diff the file
        across restarts.
        """
        payload = json.dumps(self._aggregate.to_dict(), sort_keys=True, indent=2) + "\n"
        atomic_write_text(path, payload)

    def create(self, **kwargs: Any) -> CallResult:
        """Call the underlying ``messages.create`` with cache_control applied.

        ``kwargs`` are passed through; ``model`` defaults to the wrapper's
        configured model if the caller didn't supply one.
        """
        kwargs.setdefault("model", self._model)
        prepared = self._apply_cache_control(kwargs)
        response = self._client.messages.create(**prepared)
        telem = self._read_telemetry(response)
        self._aggregate = self._aggregate.merge(telem)
        return CallResult(response=response, telemetry=telem)

    # ----- internals -----

    def _apply_cache_control(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        out = dict(kwargs)
        if "system" in self._cache_segments and "system" in out:
            out["system"] = _mark_system(out["system"])
        if "tools" in self._cache_segments and out.get("tools"):
            out["tools"] = _mark_tools(out["tools"])
        if "messages_prefix" in self._cache_segments and out.get("messages"):
            out["messages"] = _mark_messages_prefix(out["messages"])
        return out

    def _read_telemetry(self, response: Any) -> CacheTelemetry:
        usage = _get_usage(response)
        write = _coerce_token_count(getattr(usage, "cache_creation_input_tokens", 0))
        read = _coerce_token_count(getattr(usage, "cache_read_input_tokens", 0))
        # Cache hit ⇔ at least one token was served from cache on this call.
        # Cache miss ⇔ at least one token was written into the cache this call.
        # A single call may be both (cold path: warming a new suffix segment).
        return CacheTelemetry(
            hits=1 if read > 0 else 0,
            misses=1 if write > 0 else 0,
            tokens_cached=read,
            tokens_written=write,
            dollars_saved=self._dollars_saved(read=read),
        )

    def _dollars_saved(self, *, read: int) -> float:
        """Savings vs. the no-cache baseline for the tokens served from cache.

        Each cached token would have cost ``input_per_mtok / 1e6`` without
        caching; with caching it costs ``read_multiplier ×`` that. The
        savings per token is therefore ``(1 - read_multiplier) ×`` the
        input rate. Cache *writes* are a cost (1.25×), not a saving, and
        are reported separately via ``tokens_written``.
        """
        if read <= 0:
            return 0.0
        rate = self._pricing.input_per_mtok / 1_000_000
        discount = 1.0 - self._pricing.cache_read_multiplier
        return read * rate * discount


# ----- segment-marking helpers (no client coupling) -----


def _ephemeral_cache_control() -> dict[str, str]:
    return {"type": "ephemeral"}


def _mark_system(system: Any) -> Any:
    """Mark the system prompt as cacheable.

    Anthropic accepts ``system`` either as a string or a list of content
    blocks. For strings we promote to a single text block with
    ``cache_control``; for blocks we add ``cache_control`` to the last
    block (the conventional "cache up to here" marker).
    """
    if isinstance(system, str):
        return [{"type": "text", "text": system, "cache_control": _ephemeral_cache_control()}]
    if isinstance(system, list) and system:
        new = [dict(b) for b in system]
        new[-1] = {**new[-1], "cache_control": _ephemeral_cache_control()}
        return new
    return system


def _mark_tools(tools: list[Any]) -> list[Any]:
    if not tools:
        return tools
    new = [dict(t) for t in tools]
    new[-1] = {**new[-1], "cache_control": _ephemeral_cache_control()}
    return new


def _mark_messages_prefix(messages: list[Any]) -> list[Any]:
    """Cache up to and including the last user message in the prefix.

    The convention is that the most recent prefix turn carries the
    cache_control marker; everything before it inherits cacheability.
    """
    if not messages:
        return messages
    new = [dict(m) for m in messages]
    target = new[-1]
    content = target.get("content")
    if isinstance(content, str):
        target["content"] = [
            {"type": "text", "text": content, "cache_control": _ephemeral_cache_control()}
        ]
    elif isinstance(content, list) and content:
        new_content = [dict(b) for b in content]
        new_content[-1] = {**new_content[-1], "cache_control": _ephemeral_cache_control()}
        target["content"] = new_content
    new[-1] = target
    return new


def _coerce_token_count(value: Any) -> int:
    """Best-effort non-negative ``int`` token count from a usage field.

    Cache telemetry is best-effort observability accounting, gathered *after*
    ``messages.create`` has already returned a valid response. A malformed
    usage field must therefore **abstain** (→ ``0``) rather than crash and
    destroy that successful response — the same "abstain, don't crash on
    malformed SDK shapes" contract #94/#106/#112 set for ``_extract_text`` and
    the logprob extractor.

    The bare ``int(value or 0)`` this replaces crashed on a present-but-
    malformed value: ``int(NaN)`` → ``ValueError``, ``int(inf)`` →
    ``OverflowError``, ``int("abc")`` → ``ValueError``. A finite, non-negative
    numeric (including a numeric string like ``"5"``) still coerces to ``int``
    unchanged; ``None``/falsy → ``0``; anything non-coercible or negative
    abstains to ``0`` (a negative token count is malformed and would otherwise
    poison ``tokens_cached`` / ``dollars_saved``).
    """
    try:
        n = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return n if n >= 0 else 0


def _get_usage(response: Any) -> Any:
    """Return the usage object from the response, tolerating dict shape."""
    if hasattr(response, "usage"):
        return response.usage
    if isinstance(response, dict) and "usage" in response:
        return _DictAttr(response["usage"])
    return _DictAttr({})


class _DictAttr:
    """Lightweight attribute view over a dict, for dict-shaped responses."""

    __slots__ = ("_d",)

    def __init__(self, d: dict[str, Any]) -> None:
        self._d = d

    def __getattr__(self, name: str) -> Any:
        return self._d.get(name, 0)
