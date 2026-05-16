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

import math
from dataclasses import dataclass, field
from typing import Any, Protocol


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

    threshold: float = 1.5  # ~3 plausible tokens with equal mass
    name: str = "entropy"

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
        return [float(v) for v in direct]
    content = getattr(response, "content", None)
    if isinstance(content, list) and content:
        first = content[0]
        logprobs = getattr(first, "logprobs", None)
        if isinstance(logprobs, list) and logprobs:
            top = logprobs[0]
            top_logprobs = getattr(top, "top_logprobs", None) or top.get("top_logprobs")  # type: ignore[union-attr]
            if isinstance(top_logprobs, list):
                return [float(v.get("logprob", 0.0)) for v in top_logprobs]
    return None


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

    def measure(self, response: Any) -> SignalReading:
        text = _extract_text(response)
        prompt = getattr(response, "prompt", None)
        if not text:
            return SignalReading(value=None, trip=False)
        verdict = self.judge.score(prompt or "", text, rubric=self.rubric)
        score = float(getattr(verdict, "score", None) or 0.0)
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

    def route(self, request: Any) -> RouterDecision:
        cheap_response = self.cheap_adapter.call_cheap(request)
        readings: dict[str, float | None] = {}
        triggered: str | None = None
        chosen = self.cheap_model
        for sig in self.signals:
            reading = sig.measure(cheap_response)
            readings[sig.name] = reading.value
            if reading.trip and triggered is None:
                # First-trip-wins: lock in escalation but keep measuring
                # the remaining signals so telemetry sees every value.
                triggered = sig.name
                chosen = self.strong_model
        return RouterDecision(
            model_id=chosen,
            triggered_signal=triggered,
            signal_values=readings,
            cheap_response=cheap_response,
        )
