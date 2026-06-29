"""Uncertainty-routed cheap → strong model fallback (issue #3).

The shape:

```
                   ┌──────────────────────────────┐
   request ────▶   │  UncertaintyRouter.route()   │ ───▶ RouterDecision
                   │   1. ask cheap model         │      (model_id, signal_values,
                   │   2. evaluate each signal    │       triggered_signal | None,
                   │   3. if any trips → escalate │       cheap_response | None)
                   └──────────────────────────────┘
```

The router runs the cheap model first, evaluates the configured
`EscalationSignal`s on the response, and returns the strong model id
when any signal trips. Signals are evaluated in order; the *first*
signal that returns a value past its threshold wins. A signal that
returns `None` is treated as "couldn't measure" — not as "didn't trip"
— so models without logprob support don't accidentally skip the entropy
check entirely.

Two signals ship here:

- `EntropySignal` — Shannon entropy over the cheap model's first-token
  logprobs. The seam reads logprobs off a duck-typed response object;
  if the field is absent, the signal returns `None` and the router
  moves to the next signal.
- `JudgeConfidenceSignal` — runs an `eval_harness.Judge`-shaped object
  against the cheap output's text, escalates on score below threshold.
  This is the cross-repo seam to `llm-eval-harness`.

Adding signals later (refusal-flag, output-length, custom rules) is a
one-class change implementing the `EscalationSignal` Protocol.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cost_optimizer.io_utils import atomic_write_text


@dataclass(frozen=True)
class RouterDecision:
    """What the router decided + why.

    `triggered_signal` is the name of the first signal that crossed its
    threshold, or `None` when no signal tripped (in which case
    `model_id == cheap_model`). `signal_values` is the raw value each
    signal returned (or `None` for signals that couldn't measure) —
    used by the savings-dashboard (#5) to attribute escalation cost.
    """

    model_id: str
    triggered_signal: str | None
    signal_values: dict[str, float | None]
    cheap_response: Any | None
    """The cheap model's response, when one was obtained. None on the
    rare path where the router decides to escalate before calling the
    cheap model (e.g., a future "always-escalate-on-large-input" signal).
    """


@dataclass(frozen=True)
class SignalReading:
    """What an `EscalationSignal` returns.

    `value` is the measurement (entropy, judge score, etc); `None` when
    the signal couldn't measure. `trip` is the boolean decision
    delegated to the signal itself — the signal owns the
    threshold-comparison semantics (some signals trip *above* their
    threshold, like entropy; others *below*, like a judge score).
    """

    value: float | None
    trip: bool

    def __post_init__(self) -> None:
        # A reading that couldn't measure (`value is None`, per the field docs)
        # must not trip. `route()` only counts non-None readings in
        # `per_signal_measured` (guarded on `reading.value is not None`) but
        # counts every trip in `per_signal_trips`, so a `value=None, trip=True`
        # reading would increment trips without measured — breaking the
        # `per_signal_trips[s] <= per_signal_measured[s]` invariant the two
        # counters uphold and dividing the dashboard's `trip_rate` by a
        # `measured` that omits the trip it's rating. The built-in signals
        # already honor this (EntropySignal/JudgeConfidenceSignal return
        # trip=False when they can't measure); enforce it at the type boundary
        # for third-party EscalationSignal implementations too, matching the
        # module's other contract-tightening __post_init__ guards.
        if self.value is None and self.trip:
            raise ValueError(
                "SignalReading(value=None, trip=True) is invalid: a signal that "
                "couldn't measure (value=None) must not trip — return trip=False "
                "when no measurement is available"
            )


@dataclass
class RouterStats:
    """Cumulative router activity across `route()` calls.

    Sibling of `CacheTelemetry` (prompt-cache layer) and `CacheStats`
    (semantic-cache layer): the runtime layer's roll-up state for an
    observability sink. `to_dict` returns a JSON-stable shape with the
    derived `escalation_rate` included so log consumers don't have to
    recompute it.

    `per_signal_trips` counts the *first* signal that tripped on each
    `route()` (per RouterDecision.triggered_signal — first-trip-wins).
    `per_signal_measured` counts every signal that returned a non-None
    reading, so a consumer can distinguish "didn't trip" from "couldn't
    measure" — the same distinction `RouterDecision.signal_values`
    preserves at the per-call layer.
    """

    total_routes: int = 0
    escalations: int = 0
    cheap_only: int = 0
    per_signal_trips: dict[str, int] = field(default_factory=dict)
    per_signal_measured: dict[str, int] = field(default_factory=dict)

    @property
    def escalation_rate(self) -> float:
        return self.escalations / self.total_routes if self.total_routes > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        """JSON-stable dict for observability/logging sinks.

        Mirrors `CacheTelemetry.to_dict` and `CacheStats.to_dict`: the
        raw counter fields plus the derived `escalation_rate` so log
        consumers don't have to recompute it (and risk drift if the
        formula ever changes). Pairs with
        `UncertaintyRouter.dump_stats_json` for the on-disk path;
        metric backends consume the in-process dict directly.
        """
        return {
            "total_routes": self.total_routes,
            "escalations": self.escalations,
            "cheap_only": self.cheap_only,
            "per_signal_trips": dict(self.per_signal_trips),
            "per_signal_measured": dict(self.per_signal_measured),
            "escalation_rate": self.escalation_rate,
        }


class EscalationSignal(Protocol):
    """One-method Protocol matching the portfolio's other one-method seams.

    `name` is what shows up in `RouterDecision.triggered_signal`; keep
    it short and stable. `measure(response)` is called with whatever
    the cheap-model adapter returned (a duck-typed object the signal
    interprets). The signal returns `SignalReading(value, trip)`.
    """

    name: str

    def measure(self, response: Any) -> SignalReading: ...


# ---------------------------------------------------------------------
# Shipped signals
# ---------------------------------------------------------------------


@dataclass
class EntropySignal:
    """Shannon entropy over the cheap model's first-token logprobs.

    High entropy on the first token correlates with model uncertainty
    on the overall answer. The signal trips when entropy ≥ `threshold`.
    """

    threshold: float = 1.5  # ~4-5 equal-mass tokens (e^1.5 ≈ 4.48; ln4≈1.39, ln5≈1.61)
    name: str = "entropy"

    def __post_init__(self) -> None:
        # Entropy is bounded [0, +inf), so threshold should be finite and >= 0.
        # NaN passes `>= threshold` always-false → silent disable (escalation
        # gate never fires); negative makes every reading >= threshold → silent
        # always-trip (D-009 savings dashboard reports cost as if cheap-model
        # path was taken when in fact the strong model ran on every request);
        # +Infinity is silent-disable as well. Mirrors the contract-tightening
        # sweep across the portfolio (#36).
        if not math.isfinite(self.threshold) or self.threshold < 0.0:
            raise ValueError(
                f"EntropySignal.threshold must be a finite number >= 0.0; got {self.threshold}"
            )

    def measure(self, response: Any) -> SignalReading:
        # Anthropic-style: response.content is a list of blocks, each
        # with an optional `logprobs` field. We look at the first text
        # block's first token's distribution.
        first_token_logprobs = _extract_first_token_logprobs(response)
        if first_token_logprobs is None or len(first_token_logprobs) == 0:
            return SignalReading(value=None, trip=False)
        entropy = _shannon_entropy_nats(first_token_logprobs)
        return SignalReading(value=entropy, trip=entropy >= self.threshold)


def _extract_first_token_logprobs(response: Any) -> list[float] | None:
    """Pull `[logp1, logp2, ...]` from a response, or None if absent.

    Accepts the shape `response.first_token_logprobs` directly (set by
    test fakes and by adapters that pre-extract), and also the nested
    shape `response.content[0].logprobs` used by some SDKs. Returns
    None for anything else so signals can stay defensive.
    """
    direct = getattr(response, "first_token_logprobs", None)
    if isinstance(direct, list):
        # A present-but-None element (a malformed/truncated SDK distribution) hit
        # `float(None)` and raised a raw TypeError that escaped `measure` and
        # `route()` — aborting the request instead of abstaining. The nested path
        # below already returns None on a missing logprob (#94); mirror that here
        # so both paths handle identical bad input the same way, per this
        # function's "returns None for anything else" defensive contract and
        # `measure`'s value=None ⟹ not-trip rule (#82, #73). (#106)
        if any(v is None for v in direct):
            return None
        floats = [float(v) for v in direct]
        # A non-finite (NaN/±Inf) logprob — a numerically unstable or malformed
        # SDK distribution — slips `_shannon_entropy_nats`'s `total <= 0` guard
        # (`NaN <= 0` is False), so the entropy silently reads 0.0 and the
        # request never escalates. Abstain on it, exactly as the nested path
        # abstains on a missing logprob (#94) and per `measure`'s value=None ⟹
        # not-trip rule (#82, #73). A finite 0.0 logprob is preserved. (#95)
        if any(not math.isfinite(f) for f in floats):
            return None
        return floats
    content = getattr(response, "content", None)
    if isinstance(content, list) and content:
        first = content[0]
        logprobs = getattr(first, "logprobs", None)
        if isinstance(logprobs, list) and logprobs:
            top = logprobs[0]
            top_logprobs = _read_field(top, "top_logprobs")
            if isinstance(top_logprobs, list):
                # A `top_logprobs` entry without a `logprob` field is a malformed
                # or truncated SDK node. Defaulting it to 0.0 fabricated a token
                # with probability exp(0)=1.0, which `_shannon_entropy_nats`
                # normalizes into the distribution and skews the entropy (and the
                # `trip` decision built on it). Per this function's defensive
                # contract — and `EntropySignal.measure`'s value=None ⟹ not-trip
                # rule (#82, #73) — abstain (return None) on a missing logprob
                # rather than measuring corrupt data. A present 0.0 is preserved.
                values = [_read_field(v, "logprob") for v in top_logprobs]
                if any(lp is None for lp in values):
                    return None
                floats = [float(lp) for lp in values]
                # Same finiteness abstain as the direct path above (#95): a
                # present-but-non-finite logprob would read entropy 0.0 and
                # suppress escalation, so abstain rather than measure corrupt data.
                if any(not math.isfinite(f) for f in floats):
                    return None
                return floats
    return None


def _read_field(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off either an attribute or a dict key, else `default`.

    The SDK-shape logprob path mixes object-typed and dict-typed nodes
    depending on the client. A bare `getattr(...) or obj.get(...)` raised
    `AttributeError` when `obj` was an object that had neither the attribute
    nor a `.get` method — defeating the "returns None for anything else so
    signals can stay defensive" contract of `_extract_first_token_logprobs`
    (#69). This reads attr-first, then dict-key only when `obj` is actually a
    `dict`, and never calls `.get` on a non-dict.
    """
    attr = getattr(obj, name, None)
    if attr is not None:
        return attr
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


def _shannon_entropy_nats(logprobs: list[float]) -> float:
    # Logprobs are log-base-e probabilities. H = -sum(p * log p).
    # Logprobs may not sum to 1 (top-k truncated); normalize first.
    probs = [math.exp(lp) for lp in logprobs]
    total = sum(probs)
    if total <= 0:
        return 0.0
    probs = [p / total for p in probs]
    entropy = 0.0
    for p in probs:
        if p > 0:
            entropy -= p * math.log(p)
    return entropy


# ---------------------------------------------------------------------


@dataclass
class JudgeConfidenceSignal:
    """Run an `eval_harness.Judge`-shaped object on the cheap response.

    Escalates when the judge score < `threshold`. The judge is
    duck-typed: the signal calls `judge.score(prompt, response_text,
    rubric=...)` and reads `.score`. Any object that satisfies that
    contract works — including the deterministic stub used in tests.

    The cross-repo wiring to `llm-eval-harness` is intentional: the
    same `Judge.score` API the regression runner uses (#3 in
    eval-harness) is the signal the router uses to decide when a cheap
    model's output isn't trusted enough.
    """

    judge: Any
    rubric: str
    threshold: float = 0.7
    name: str = "judge"

    def __post_init__(self) -> None:
        # Judge score is bounded [0, 1] per llm-eval-harness JudgeScore. NaN
        # makes `score < threshold` always-false → silent disable; > 1 makes
        # every score < threshold → silent always-trip → strong model on every
        # request; < 0 → silent disable (no real score satisfies score < neg).
        # Same harm class as EntropySignal above (#36).
        if not math.isfinite(self.threshold) or not (0.0 <= self.threshold <= 1.0):
            raise ValueError(
                f"JudgeConfidenceSignal.threshold must be a finite number in [0.0, 1.0]; "
                f"got {self.threshold}"
            )

    def measure(self, response: Any) -> SignalReading:
        text = _extract_text(response)
        prompt = getattr(response, "prompt", None)
        if not text:
            return SignalReading(value=None, trip=False)
        verdict = self.judge.score(prompt or "", text, rubric=self.rubric)
        # A judge that returns a verdict without a usable `.score` (missing,
        # None, or non-finite) means "couldn't measure" — not "scored zero".
        # The old `float(getattr(...) or 0.0)` collapsed a missing score to
        # 0.0, which then tripped (`0.0 < threshold`) and silently escalated
        # *every* request to the expensive model — the opposite of this tool's
        # purpose, and inconsistent with the `value=None, trip=False` contract
        # that EntropySignal (logprobs absent) and the empty-text guard above
        # both honor. A genuine finite 0.0 is a real measurement and still
        # trips. Non-finite is rejected for the same reason as the #36/#71
        # finiteness sweeps: NaN/inf isn't a valid [0, 1] judge score.
        raw = getattr(verdict, "score", None)
        if raw is None:
            return SignalReading(value=None, trip=False)
        score = float(raw)
        if not math.isfinite(score):
            return SignalReading(value=None, trip=False)
        return SignalReading(value=score, trip=score < self.threshold)


def _extract_text(response: Any) -> str:
    """Pull the assistant text out of a duck-typed response.

    Accepts `response.text` directly (test fakes), and `response.content[i].text`
    for the SDK shape. Returns "" for anything else.
    """
    direct = getattr(response, "text", None)
    if isinstance(direct, str):
        return direct
    content = getattr(response, "content", None)
    if isinstance(content, list):
        parts = [getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text"]
        return "".join(parts)
    return ""


# ---------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------


class CheapAdapter(Protocol):
    """A one-method seam to whatever cheap-model call is appropriate.

    `request` is opaque to the router; it's whatever shape the consumer
    passes through. The adapter is responsible for invoking the model
    and returning a response a signal can inspect.
    """

    def call_cheap(self, request: Any) -> Any: ...


@dataclass
class UncertaintyRouter:
    """`route(request)` returns a `RouterDecision` per the loop above.

    `signals` is evaluated in order. The first signal that returns
    `SignalReading(trip=True)` wins; if no signal trips, the router
    sticks with `cheap_model`. The cheap response is always obtained
    (the router needs *something* to inspect); a future "always escalate
    on large input" signal would short-circuit before the cheap call,
    at which point the `cheap_response` field of the decision is None.
    """

    cheap_model: str
    strong_model: str
    cheap_adapter: CheapAdapter
    signals: list[EscalationSignal] = field(default_factory=list)
    stats: RouterStats = field(default_factory=RouterStats)

    def __post_init__(self) -> None:
        # Duplicate `name` values in `signals` would silently overwrite each
        # other in `RouterDecision.signal_values` (which D-009 designates as
        # cost-attribution telemetry for the savings dashboard). Catch it at
        # construction so the failure is loud and surfaces the offending name,
        # rather than a quietly truncated readings dict at every `route()` call.
        names = [s.name for s in self.signals]
        seen: set[str] = set()
        dups: set[str] = set()
        for n in names:
            if n in seen:
                dups.add(n)
            seen.add(n)
        if dups:
            raise ValueError(f"duplicate signal names: {sorted(dups)}")

    def route(self, request: Any) -> RouterDecision:
        cheap_response = self.cheap_adapter.call_cheap(request)
        readings: dict[str, float | None] = {}
        triggered: str | None = None
        chosen = self.cheap_model
        for sig in self.signals:
            reading = sig.measure(cheap_response)
            readings[sig.name] = reading.value
            if reading.value is not None:
                self.stats.per_signal_measured[sig.name] = (
                    self.stats.per_signal_measured.get(sig.name, 0) + 1
                )
            if reading.trip and triggered is None:
                # First-trip-wins: lock in escalation but keep measuring
                # the remaining signals so telemetry sees every value.
                triggered = sig.name
                chosen = self.strong_model
        self.stats.total_routes += 1
        if triggered is not None:
            self.stats.escalations += 1
            self.stats.per_signal_trips[triggered] = (
                self.stats.per_signal_trips.get(triggered, 0) + 1
            )
        else:
            self.stats.cheap_only += 1
        return RouterDecision(
            model_id=chosen,
            triggered_signal=triggered,
            signal_values=readings,
            cheap_response=cheap_response,
        )

    def dump_stats_json(self, path: str | Path) -> None:
        """Write the current router stats to ``path`` as JSON.

        Atomic on POSIX — uses ``cost_optimizer.io_utils.atomic_write_text``
        so a Ctrl-C / disk-full / OOM between truncate and flush can't
        leave a log-tailer reading a half-written file. Byte-shape parity
        with ``PromptCacheWrapper.dump_aggregate_json`` and
        ``SemanticCache.dump_stats_json``: sorted keys, indent=2,
        trailing newline. Operators can tail / diff the file across
        restarts.
        """
        payload = json.dumps(self.stats.to_dict(), sort_keys=True, indent=2) + "\n"
        atomic_write_text(path, payload)
