"""Hermetic tests for the uncertainty-routed model fallback (#3).

Three surfaces:

1. `UncertaintyRouter` — first-signal-wins escalation, all signal
   values recorded in telemetry, no-trip case stays on cheap.
2. `EntropySignal` — extracts logprobs from the two response shapes,
   computes Shannon entropy correctly, defaults to `None` value
   when logprobs aren't present.
3. `JudgeConfidenceSignal` — calls the judge with the right shape,
   trips below threshold, defaults to `None` on empty text.

No Anthropic SDK, no eval-harness install needed — everything is
duck-typed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pytest

from cost_optimizer.router import (
    EntropySignal,
    JudgeConfidenceSignal,
    RouterDecision,
    SignalReading,
    UncertaintyRouter,
    _shannon_entropy_nats,
)

# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------


@dataclass
class FakeResponse:
    """Duck-typed response stand-in. Used by stub adapters + signals."""

    text: str = ""
    first_token_logprobs: list[float] | None = None
    prompt: str = ""


@dataclass
class StubAdapter:
    """Returns a pre-built response. Records every call for assertions."""

    response: FakeResponse
    calls: list[Any] = field(default_factory=list)

    def call_cheap(self, request: Any) -> Any:
        self.calls.append(request)
        return self.response


@dataclass
class ConstantSignal:
    """Always returns the same reading. Lets tests pin first-trip-wins."""

    name: str
    reading: SignalReading

    def measure(self, response: Any) -> SignalReading:  # noqa: ARG002 - signature
        return self.reading


@dataclass
class StubJudge:
    """`judge.score(...)` returns an object with a `.score` attribute."""

    canned_score: float
    last_call: tuple[str, str, str] | None = None

    def score(self, prompt: str, response_text: str, *, rubric: str) -> Any:
        self.last_call = (prompt, response_text, rubric)
        return SimpleVerdict(score=self.canned_score)


@dataclass
class SimpleVerdict:
    score: float


# ----------------------------------------------------------------------
# UncertaintyRouter
# ----------------------------------------------------------------------


def _make_router(adapter: StubAdapter, signals) -> UncertaintyRouter:
    return UncertaintyRouter(
        cheap_model="claude-haiku-4-5",
        strong_model="claude-opus-4-7",
        cheap_adapter=adapter,
        signals=signals,
    )


def test_no_signals_means_no_escalation() -> None:
    adapter = StubAdapter(response=FakeResponse(text="ok"))
    router = _make_router(adapter, signals=[])
    decision = router.route({"prompt": "anything"})
    assert isinstance(decision, RouterDecision)
    assert decision.model_id == "claude-haiku-4-5"
    assert decision.triggered_signal is None
    assert decision.signal_values == {}
    assert decision.cheap_response is adapter.response
    assert adapter.calls == [{"prompt": "anything"}]


def test_no_signal_trips_means_cheap_wins() -> None:
    adapter = StubAdapter(response=FakeResponse(text="ok"))
    router = _make_router(
        adapter,
        signals=[
            ConstantSignal("entropy", SignalReading(value=0.1, trip=False)),
            ConstantSignal("judge", SignalReading(value=0.9, trip=False)),
        ],
    )
    decision = router.route({"prompt": "hi"})
    assert decision.model_id == "claude-haiku-4-5"
    assert decision.triggered_signal is None
    assert decision.signal_values == {"entropy": 0.1, "judge": 0.9}


def test_first_tripping_signal_wins() -> None:
    adapter = StubAdapter(response=FakeResponse(text="ok"))
    router = _make_router(
        adapter,
        signals=[
            ConstantSignal("entropy", SignalReading(value=2.0, trip=True)),
            ConstantSignal("judge", SignalReading(value=0.4, trip=True)),
        ],
    )
    decision = router.route({"prompt": "hi"})
    assert decision.model_id == "claude-opus-4-7"
    assert decision.triggered_signal == "entropy"
    # Both signals still measured for telemetry, even after first trip.
    assert decision.signal_values == {"entropy": 2.0, "judge": 0.4}


def test_second_signal_can_trip_when_first_does_not() -> None:
    adapter = StubAdapter(response=FakeResponse(text="ok"))
    router = _make_router(
        adapter,
        signals=[
            ConstantSignal("entropy", SignalReading(value=0.1, trip=False)),
            ConstantSignal("judge", SignalReading(value=0.4, trip=True)),
        ],
    )
    decision = router.route({"prompt": "hi"})
    assert decision.model_id == "claude-opus-4-7"
    assert decision.triggered_signal == "judge"
    assert decision.signal_values == {"entropy": 0.1, "judge": 0.4}


def test_signal_returning_none_value_does_not_trip() -> None:
    # A signal that couldn't measure (e.g., logprobs absent) returns
    # `value=None, trip=False` — the router records the None and moves on.
    adapter = StubAdapter(response=FakeResponse(text="ok"))
    router = _make_router(
        adapter,
        signals=[
            ConstantSignal("entropy", SignalReading(value=None, trip=False)),
            ConstantSignal("judge", SignalReading(value=0.4, trip=True)),
        ],
    )
    decision = router.route({"prompt": "hi"})
    assert decision.signal_values["entropy"] is None
    assert decision.triggered_signal == "judge"


# ----------------------------------------------------------------------
# EntropySignal
# ----------------------------------------------------------------------


def test_entropy_zero_for_pinned_distribution() -> None:
    # logprob 0.0 == prob 1.0; everything else negligible.
    reading = EntropySignal(threshold=1.0).measure(
        FakeResponse(first_token_logprobs=[0.0, -100.0, -100.0])
    )
    assert reading.value is not None
    assert reading.value < 1e-3
    assert reading.trip is False


def test_entropy_uniform_three_tokens_is_log3() -> None:
    # Three equiprobable tokens; entropy in nats = ln(3) ≈ 1.0986.
    logp = math.log(1 / 3)
    reading = EntropySignal(threshold=1.5).measure(
        FakeResponse(first_token_logprobs=[logp, logp, logp])
    )
    assert reading.value is not None
    assert reading.value == pytest.approx(math.log(3), rel=1e-6)
    # Below 1.5 threshold => no trip.
    assert reading.trip is False


def test_entropy_trips_at_or_above_threshold() -> None:
    # Five equiprobable tokens; entropy = ln(5) ≈ 1.609.
    logp = math.log(1 / 5)
    reading = EntropySignal(threshold=1.5).measure(FakeResponse(first_token_logprobs=[logp] * 5))
    assert reading.value == pytest.approx(math.log(5), rel=1e-6)
    assert reading.trip is True


def test_entropy_handles_truncated_logprobs() -> None:
    # Top-5 logprobs that don't sum to 1; entropy is computed on the
    # normalized distribution, not the raw probs.
    reading = EntropySignal(threshold=0.5).measure(
        FakeResponse(first_token_logprobs=[math.log(0.4), math.log(0.3), math.log(0.05)])
    )
    # Normalized: (0.4, 0.3, 0.05) / 0.75 ≈ (0.533, 0.4, 0.067)
    # Entropy = -sum(p ln p) for those three.
    assert reading.value is not None
    assert 0.5 < reading.value < 1.2


def test_entropy_returns_none_when_logprobs_absent() -> None:
    reading = EntropySignal().measure(FakeResponse())
    assert reading.value is None
    assert reading.trip is False


def test_entropy_helper_zero_for_empty_input() -> None:
    assert _shannon_entropy_nats([]) == 0.0


def test_entropy_handles_sdk_shape_with_content_blocks() -> None:
    # Mimic the SDK-style nested logprobs payload: response.content[0]
    # is a block; block.logprobs[0].top_logprobs is a list of dicts
    # carrying `{"token": "...", "logprob": <float>}`.
    class Block:
        type = "text"
        logprobs = [{"top_logprobs": [{"logprob": math.log(0.5)}, {"logprob": math.log(0.5)}]}]

    class SdkResponse:
        content = [Block()]

    reading = EntropySignal(threshold=0.5).measure(SdkResponse())
    assert reading.value is not None
    # ln(2) in nats ≈ 0.693; trips at threshold 0.5.
    assert reading.value == pytest.approx(math.log(2), rel=1e-6)
    assert reading.trip is True


# ----------------------------------------------------------------------
# JudgeConfidenceSignal
# ----------------------------------------------------------------------


def test_judge_signal_trips_below_threshold() -> None:
    judge = StubJudge(canned_score=0.5)
    signal = JudgeConfidenceSignal(judge=judge, rubric="faithfulness", threshold=0.7)
    reading = signal.measure(FakeResponse(text="answer", prompt="q"))
    assert reading.value == 0.5
    assert reading.trip is True
    assert judge.last_call == ("q", "answer", "faithfulness")


def test_judge_signal_no_trip_at_or_above_threshold() -> None:
    judge = StubJudge(canned_score=0.8)
    signal = JudgeConfidenceSignal(judge=judge, rubric="faithfulness", threshold=0.7)
    reading = signal.measure(FakeResponse(text="answer", prompt="q"))
    assert reading.value == 0.8
    assert reading.trip is False


def test_judge_signal_handles_empty_response_text() -> None:
    judge = StubJudge(canned_score=0.5)
    signal = JudgeConfidenceSignal(judge=judge, rubric="r", threshold=0.7)
    reading = signal.measure(FakeResponse(text="", prompt="q"))
    assert reading.value is None
    assert reading.trip is False
    # The judge was never called — we don't waste a score on nothing.
    assert judge.last_call is None


def test_judge_signal_extracts_text_from_sdk_shape() -> None:
    class Block:
        type = "text"
        text = "answer from sdk"

    class SdkResponse:
        content = [Block()]
        prompt = "q"

    judge = StubJudge(canned_score=0.4)
    reading = JudgeConfidenceSignal(judge=judge, rubric="r", threshold=0.7).measure(SdkResponse())
    assert reading.value == 0.4
    assert reading.trip is True
    assert judge.last_call == ("q", "answer from sdk", "r")


# ----------------------------------------------------------------------
# End-to-end: router + real signals
# ----------------------------------------------------------------------


def test_router_with_entropy_signal_against_uniform_response_escalates() -> None:
    # Cheap model returns a uniform-3-token logprobs; entropy ≈ ln(3) ≈ 1.099
    # is below the default threshold (1.5), so no escalation.
    logp = math.log(1 / 3)
    adapter = StubAdapter(response=FakeResponse(text="ok", first_token_logprobs=[logp, logp, logp]))
    decision = _make_router(adapter, signals=[EntropySignal()]).route({})
    assert decision.triggered_signal is None
    assert decision.model_id == "claude-haiku-4-5"

    # Five-token uniform: entropy ≈ ln(5) ≈ 1.61, above 1.5 — escalates.
    logp5 = math.log(1 / 5)
    adapter5 = StubAdapter(response=FakeResponse(text="ok", first_token_logprobs=[logp5] * 5))
    decision5 = _make_router(adapter5, signals=[EntropySignal()]).route({})
    assert decision5.triggered_signal == "entropy"
    assert decision5.model_id == "claude-opus-4-7"


def test_router_with_both_signals_records_all_values() -> None:
    # Entropy trips (5-token uniform); judge would also have tripped (0.3).
    # First-trip-wins, but both values land in `signal_values`.
    logp5 = math.log(1 / 5)
    adapter = StubAdapter(
        response=FakeResponse(text="answer", first_token_logprobs=[logp5] * 5, prompt="q")
    )
    judge = StubJudge(canned_score=0.3)
    router = _make_router(
        adapter,
        signals=[
            EntropySignal(threshold=1.5),
            JudgeConfidenceSignal(judge=judge, rubric="faithfulness", threshold=0.7),
        ],
    )
    decision = router.route({})
    assert decision.triggered_signal == "entropy"
    assert decision.model_id == "claude-opus-4-7"
    assert decision.signal_values["entropy"] == pytest.approx(math.log(5), rel=1e-6)
    assert decision.signal_values["judge"] == 0.3
