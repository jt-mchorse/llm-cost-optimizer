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

Both sides of the caching trade are priced. A cache *read* bills at
``cache_read_multiplier`` times the input rate (a saving) and a cache *write*
at ``cache_write_multiplier`` times it (a surcharge), so a workload that writes
more than it reads costs *more* than not caching -- and ``dollars_saved``,
being ``read * rate * positive_discount``, can never say so. ``net_dollars_saved``
is the one to put on a dashboard (#196).

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
from cost_optimizer.pricing import ModelPricing, _coerce_token_count, get_pricing


@dataclass(frozen=True)
class CacheTelemetry:
    """Cache activity for a single call (or an aggregate across calls)."""

    hits: int
    misses: int
    tokens_cached: int
    tokens_written: int
    dollars_saved: float
    dollars_write_premium: float = 0.0
    """Extra dollars paid to *write* ``tokens_written`` into the cache (#196).

    ``cache_creation_input_tokens`` bills at ``cache_write_multiplier`` times the
    input rate **instead of** 1x, so the extra is ``(multiplier - 1.0)`` per
    token -- 0.25x at Anthropic's documented 1.25x.

    Defaulted so every existing construction of this dataclass, in this repo or
    a caller's, stays valid. It is the counterweight to ``dollars_saved``, which
    is gross read-side savings and, being ``read * rate * positive_discount``,
    can never be negative -- so on its own it cannot express the case where the
    caching lost money.
    """

    @property
    def net_dollars_saved(self) -> float:
        """``dollars_saved`` minus the write premium: the number an operator wants.

        A **derived** property rather than a stored field, so it cannot drift
        from its two inputs across ``merge`` / ``zero`` / a hand-built instance.

        This is exactly ``scripts/bench_savings.py``'s cost model. That bench
        prices a cached call as ``written * rate * write_mult + read * rate *
        read_mult`` against an uncached baseline of ``(written + read) * rate``,
        which rearranges to ``read * rate * (1 - read_mult) - written * rate *
        (write_mult - 1)`` -- this expression, term for term.
        ``tests/test_cache_wrapper_net_savings.py`` asserts the identity over a
        table of streams rather than restating it.
        """
        return self.dollars_saved - self.dollars_write_premium

    @classmethod
    def zero(cls) -> CacheTelemetry:
        return cls(0, 0, 0, 0, 0.0, 0.0)

    def merge(self, other: CacheTelemetry) -> CacheTelemetry:
        return CacheTelemetry(
            hits=self.hits + other.hits,
            misses=self.misses + other.misses,
            tokens_cached=self.tokens_cached + other.tokens_cached,
            tokens_written=self.tokens_written + other.tokens_written,
            dollars_saved=self.dollars_saved + other.dollars_saved,
            dollars_write_premium=self.dollars_write_premium + other.dollars_write_premium,
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
            "dollars_write_premium": self.dollars_write_premium,
            # Derived, not a dataclass field -- emitted anyway because this dict
            # is the whole payload a sink receives, and it carries no rate. A
            # consumer holding `dollars_saved` (dollars) and `tokens_written`
            # (tokens) cannot convert between them, so before #196 the net was
            # not merely un-reported here, it was un-derivable.
            "net_dollars_saved": self.net_dollars_saved,
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
            dollars_write_premium=self._dollars_write_premium(written=write),
        )

    def _dollars_saved(self, *, read: int) -> float:
        """Savings vs. the no-cache baseline for the tokens served from cache.

        Each cached token would have cost ``input_per_mtok / 1e6`` without
        caching; with caching it costs ``read_multiplier ×`` that. The
        savings per token is therefore ``(1 - read_multiplier) ×`` the
        input rate.

        **Gross, not net, and deliberately unchanged (#196.)** This used to say
        that cache writes "are reported separately via ``tokens_written``",
        which is true of this method in isolation and false of the pair: the
        benefit was in dollars and the cost was in tokens, and
        ``CacheTelemetry.to_dict`` carries no rate, so a sink could not convert
        one into the other. The write side now has its own dollar figure in
        :meth:`_dollars_write_premium`, and :attr:`CacheTelemetry.net_dollars_saved`
        is the difference. This method keeps its exact meaning and value so
        nothing reading ``dollars_saved`` today changes.
        """
        if read <= 0:
            return 0.0
        rate = self._pricing.input_per_mtok / 1_000_000
        discount = 1.0 - self._pricing.cache_read_multiplier
        return read * rate * discount

    def _dollars_write_premium(self, *, written: int) -> float:
        """Extra dollars paid to write ``written`` tokens into the cache (#196).

        ``cache_creation_input_tokens`` bills at ``cache_write_multiplier`` times
        the input rate **instead of** 1x, so the extra over not caching at all is
        ``(multiplier - 1.0)`` per token -- 0.25x at Anthropic's documented
        1.25x. ``scripts/bench_savings.py`` has charged this since it was
        written; the runtime wrapper did not, so the two halves of this repo
        priced the same call differently off the same ``ModelPricing``.

        Measured on ``claude-opus-4-8`` ($5.00/MTok) with a 20k-token prefix,
        wrapper vs. the bench's own arithmetic::

            1 write, 0 reads    +0.000000   true net -0.025000
            1 write, 9 reads    +0.810000   true net +0.785000
            8 writes, 2 reads   +0.180000   true net -0.020000   <- sign flip

        **No clamp.** ``cache_write_multiplier`` is validated ``>= 0.0``, not
        ``>= 1.0``, and ``tests/test_pricing.py`` already exercises ``0.0``. A
        sub-1.0 multiplier means writing is genuinely *cheaper* than not
        caching, and a ``max(0.0, ...)`` would launder that real saving into a
        wrong zero -- the value would never reach the guard that would have
        questioned it.
        """
        if written <= 0:
            return 0.0
        rate = self._pricing.input_per_mtok / 1_000_000
        return written * rate * (self._pricing.cache_write_multiplier - 1.0)


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


def _get_usage(response: Any) -> Any:
    """Return an attribute-readable usage view, tolerating dict shape at *both* levels.

    "Dict-shaped" is a property of the response and, independently, of the
    ``usage`` it carries. That is four combinations, and until #209 this
    function decided once for both: it returned ``response.usage`` raw off the
    attribute road and wrapped ``response["usage"]`` unconditionally off the
    dict road. Only the two combinations where the levels *agree* read
    correctly; the two mixed ones both reported zero, and neither raised:

    - object response + dict usage -> the dict was returned raw, and the
      caller's ``getattr(usage, "cache_read_input_tokens", 0)`` found no such
      *attribute* and took the default.
    - dict response + object usage -> the object was wrapped in
      :class:`_DictAttr`, whose ``__getattr__`` calls ``self._d.get(...)`` and
      raises ``AttributeError`` on a non-mapping — which the caller's
      three-argument ``getattr`` then **swallows**, landing on the same ``0``.

    Zero is not a diagnostic on this path. ``_coerce_token_count`` is
    documented to *abstain* to ``0`` on a malformed usage field, so a
    well-formed field in the other container is indistinguishable from a call
    that genuinely did no caching: ``hits`` and ``misses`` both ``0``,
    ``dollars_saved`` ``$0.00``, folded into ``aggregate`` (which only ever
    adds, so it never recovers) and out through ``dump_aggregate_json`` onto
    the savings dashboard.

    So resolve ``usage`` by either road first, then make the wrapping decision
    once, against the thing being wrapped. Precedence between the two roads is
    unchanged — attribute first — so a dict subclass carrying a ``.usage``
    attribute still resolves the way it did.

    A ``usage`` that is neither a mapping nor an object carrying the token
    attributes (a list, a string, ``None``, a missing key) still abstains to
    ``0`` via the caller's ``getattr`` default. That is the same
    "abstain, don't crash on malformed SDK shapes" contract #114/#136 set, and
    it is deliberately *not* what this fixes: the bug was a well-formed value
    being read as absent, not a malformed one being tolerated.
    """
    usage: Any = None
    if hasattr(response, "usage"):
        usage = response.usage
    elif isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return _DictAttr({})
    if isinstance(usage, dict):
        return _DictAttr(usage)
    return usage


class _DictAttr:
    """Lightweight attribute view over a dict, for dict-shaped responses."""

    __slots__ = ("_d",)

    def __init__(self, d: dict[str, Any]) -> None:
        self._d = d

    def __getattr__(self, name: str) -> Any:
        return self._d.get(name, 0)
