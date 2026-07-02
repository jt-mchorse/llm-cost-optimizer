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
    _extract_first_token_logprobs,
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


def test_entropy_default_threshold_maps_to_four_to_five_equal_mass_tokens() -> None:
    # Pins the corrected `EntropySignal.threshold = 1.5` comment (#74) to
    # executable math. Shannon entropy of N equal-mass tokens is ln(N) nats, so
    # the default 1.5-nat threshold corresponds to e^1.5 ≈ 4.48 equal-mass
    # tokens — i.e. it trips at ~4-5 plausible tokens, NOT 3 (ln(3) ≈ 1.10 is
    # below the threshold). The old comment said "~3 plausible tokens", which is
    # numerically off and would mislead anyone tuning the threshold.
    default_threshold = EntropySignal().threshold
    assert default_threshold == 1.5
    # The boundary sits strictly between 4 and 5 equal-mass tokens.
    assert math.log(4) < default_threshold < math.log(5)

    # A 4-equal-mass-token distribution (ln4 ≈ 1.386) does NOT trip the default.
    logp4 = math.log(1 / 4)
    four = EntropySignal().measure(FakeResponse(first_token_logprobs=[logp4] * 4))
    assert four.value == pytest.approx(math.log(4), rel=1e-6)
    assert four.trip is False

    # A 5-equal-mass-token distribution (ln5 ≈ 1.609) does trip it. Together
    # with the 4-token case this brackets the threshold at 4-5 tokens, the
    # claim the corrected comment now makes.
    logp5 = math.log(1 / 5)
    five = EntropySignal().measure(FakeResponse(first_token_logprobs=[logp5] * 5))
    assert five.value == pytest.approx(math.log(5), rel=1e-6)
    assert five.trip is True


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


def test_entropy_helper_matches_reference_for_valid_logprobs() -> None:
    # Regression-lock: the softmax stabilization (#118) must be bit-identical
    # to the pre-fix formula for valid (<= 0) logprobs — subtracting a constant
    # from every logprob is exactly shift-invariant over the normalized
    # distribution. `[0.0, -1.0]` normalizes to (e^0, e^-1)/(1+e^-1); its
    # entropy is a fixed reference value the fix must not perturb.
    assert _shannon_entropy_nats([0.0, -1.0]) == pytest.approx(0.5822031088882179)


@pytest.mark.parametrize("logprobs", [[710.0, -1.0], [800.0], [1e6, -1.0], [1000.0, 1000.0]])
def test_entropy_helper_no_overflow_on_large_finite_logprob(logprobs: list[float]) -> None:
    # `math.exp` raises OverflowError for arguments ≳ 709.78, so a finite-but-
    # large logprob (a corrupt SDK distribution — a positive value is not a valid
    # log-probability) crashed `_shannon_entropy_nats` pre-#118. The softmax
    # max-subtraction makes every exponent <= 0, so it computes a finite entropy
    # for any finite input instead of raising. `[1000, 1000]` (two equal masses)
    # must give ln(2); a single dominant token gives ≈ 0.
    value = _shannon_entropy_nats(logprobs)
    assert math.isfinite(value)
    assert value >= 0.0
    if logprobs == [1000.0, 1000.0]:
        assert value == pytest.approx(math.log(2))


def test_entropy_signal_abstains_gracefully_on_large_finite_logprob() -> None:
    # End-to-end at the signal layer: a large finite logprob must not raise out
    # of measure() — it degrades to a finite reading (one dominant token →
    # entropy ≈ 0, below threshold → no trip), not an OverflowError (#118).
    reading = EntropySignal(threshold=1.5).measure(FakeResponse(first_token_logprobs=[800.0, -1.0]))
    assert reading.value is not None
    assert math.isfinite(reading.value)
    assert reading.trip is False


def test_route_does_not_propagate_overflow_on_large_finite_logprob() -> None:
    # The whole point: pre-#118 a corrupt logprob crashed the entire routing
    # request with OverflowError. route() must complete and return a decision.
    adapter = StubAdapter(response=FakeResponse(first_token_logprobs=[1000.0, -1.0]))
    router = _make_router(adapter, signals=[EntropySignal(threshold=1.5)])
    decision = router.route({"prompt": "anything"})
    assert isinstance(decision, RouterDecision)
    # Dominant-token distribution → entropy ≈ 0 → below threshold → cheap wins.
    assert decision.model_id == "claude-haiku-4-5"
    assert decision.signal_values["entropy"] is not None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_extract_logprobs_abstains_on_non_finite_direct(bad: float) -> None:
    # A non-finite logprob slips _shannon_entropy_nats's `total <= 0` guard
    # (NaN <= 0 is False) → entropy silently reads 0.0 → request never escalates.
    # Abstain at the extraction seam like the missing-field guard (#94, #95).
    assert (
        _extract_first_token_logprobs(FakeResponse(first_token_logprobs=[math.log(0.5), bad]))
        is None
    )
    reading = EntropySignal(threshold=0.5).measure(
        FakeResponse(first_token_logprobs=[math.log(0.5), bad])
    )
    assert reading.value is None
    assert reading.trip is False


def test_extract_logprobs_abstains_on_none_in_direct_list() -> None:
    # #106: the direct path ran `float(v)` over every element before validating,
    # so a present-but-None logprob raised a raw TypeError that escaped measure()
    # and route() — aborting the request instead of abstaining. The nested path
    # already returns None on a missing logprob (#94); the direct path must match.
    assert (
        _extract_first_token_logprobs(FakeResponse(first_token_logprobs=[math.log(0.5), None]))
        is None
    )
    reading = EntropySignal(threshold=0.5).measure(
        FakeResponse(first_token_logprobs=[math.log(0.5), None])
    )
    assert reading.value is None
    assert reading.trip is False


def test_extract_logprobs_preserves_finite_zero_logprob() -> None:
    # The finiteness abstain must not reject a finite 0.0 logprob (a legit
    # prob-1.0 token); only NaN/±Inf abstain.
    out = _extract_first_token_logprobs(FakeResponse(first_token_logprobs=[0.0, math.log(0.5)]))
    assert out == [0.0, pytest.approx(math.log(0.5))]


def test_extract_logprobs_abstains_on_non_finite_nested_sdk_shape() -> None:
    # Same abstain on the nested SDK shape: a present-but-NaN logprob node.
    class Block:
        type = "text"
        logprobs = [{"top_logprobs": [{"logprob": math.log(0.5)}, {"logprob": float("nan")}]}]

    class SdkResponse:
        content = [Block()]

    assert _extract_first_token_logprobs(SdkResponse()) is None
    reading = EntropySignal(threshold=0.5).measure(SdkResponse())
    assert reading.value is None
    assert reading.trip is False


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


def test_entropy_handles_sdk_shape_with_object_top_logprobs() -> None:
    # Object-typed nodes (not dicts): block.logprobs[0] is an object whose
    # `top_logprobs` attribute holds objects carrying a `.logprob` attribute.
    class Entry:
        def __init__(self, logprob: float) -> None:
            self.logprob = logprob

    class Top:
        top_logprobs = [Entry(math.log(0.5)), Entry(math.log(0.5))]

    class Block:
        type = "text"
        logprobs = [Top()]

    class SdkResponse:
        content = [Block()]

    reading = EntropySignal(threshold=0.5).measure(SdkResponse())
    assert reading.value == pytest.approx(math.log(2), rel=1e-6)
    assert reading.trip is True


def test_extract_logprobs_returns_none_for_object_without_attr_or_get() -> None:
    # Regression for #69: a `top` node that is a plain object with neither a
    # `top_logprobs` attribute nor a `.get` method must yield None, not raise
    # AttributeError. The defensive contract is what lets the router fall
    # through to the next signal on logprob-less responses.
    class Bare: ...

    class Block:
        type = "text"
        logprobs = [Bare()]

    class SdkResponse:
        content = [Block()]

    assert _extract_first_token_logprobs(SdkResponse()) is None
    reading = EntropySignal().measure(SdkResponse())
    assert reading == SignalReading(value=None, trip=False)


def test_extract_logprobs_returns_none_when_an_entry_lacks_logprob() -> None:
    # A top_logprobs entry missing its `logprob` field is a malformed/truncated
    # SDK node. Defaulting it to 0.0 fabricated a prob-1.0 token that skews the
    # normalized entropy; per the defensive contract the whole extraction must
    # abstain (None) so measure() yields value=None ⟹ not-trip (#82, #73),
    # rather than measuring corrupt data.
    class Block:
        type = "text"
        logprobs = [
            {"top_logprobs": [{"logprob": math.log(0.5)}, {"token": "x"}]}  # 2nd lacks logprob
        ]

    class SdkResponse:
        content = [Block()]

    assert _extract_first_token_logprobs(SdkResponse()) is None
    reading = EntropySignal().measure(SdkResponse())
    assert reading == SignalReading(value=None, trip=False)


def test_extract_logprobs_preserves_a_present_zero_logprob() -> None:
    # The missing-field guard must not reject a *present* 0.0 logprob (a legit
    # prob-1.0 token). 0.0 entries are kept; only an absent field abstains.
    class Block:
        type = "text"
        logprobs = [{"top_logprobs": [{"logprob": 0.0}, {"logprob": math.log(0.5)}]}]

    class SdkResponse:
        content = [Block()]

    assert _extract_first_token_logprobs(SdkResponse()) == [0.0, pytest.approx(math.log(0.5))]


def test_entropy_signal_falls_through_to_next_signal_on_bare_response() -> None:
    # End-to-end (#69): EntropySignal can't measure a logprob-less response,
    # so a second signal that trips must still drive escalation — the entropy
    # crash previously aborted the whole route() before the judge ran.
    class Bare:
        text = "answer"
        prompt = "q"

    class Block:
        type = "text"
        logprobs = [Bare()]  # object lacking top_logprobs + .get

    class SdkResponse:
        content = [Block()]
        text = "answer"
        prompt = "q"

    adapter = StubAdapter(response=SdkResponse())  # type: ignore[arg-type]
    judge = StubJudge(canned_score=0.1)  # below 0.7 → trips
    router = UncertaintyRouter(
        cheap_model="cheap",
        strong_model="strong",
        cheap_adapter=adapter,
        signals=[EntropySignal(), JudgeConfidenceSignal(judge=judge, rubric="r")],
    )
    decision = router.route("req")
    assert decision.triggered_signal == "judge"
    assert decision.model_id == "strong"
    assert decision.signal_values["entropy"] is None


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


def test_judge_signal_sdk_block_with_none_text_abstains() -> None:
    # Issue #112: a truncated/malformed SDK `type="text"` block can carry
    # `text=None`. Pre-fix it reached `"".join([None])` in `_extract_text` and
    # raised a raw TypeError that escaped `measure`/`route()` — instead of the
    # `value=None, trip=False` abstain the empty-text guard intends. The judge
    # must never even be consulted (no usable text). Inverse safety net: pre-fix
    # this raised TypeError rather than returning a SignalReading.
    class Block:
        type = "text"
        text = None

    class SdkResponse:
        content = [Block()]
        prompt = "q"

    judge = StubJudge(canned_score=0.4)
    reading = JudgeConfidenceSignal(judge=judge, rubric="r", threshold=0.7).measure(SdkResponse())
    assert reading == SignalReading(value=None, trip=False)
    assert judge.last_call is None


def test_judge_signal_sdk_skips_none_text_block_keeps_str_blocks() -> None:
    # A None block between two valid text blocks is dropped, not fatal: the
    # surviving text is still joined and scored (over-rejection guard).
    class Good1:
        type = "text"
        text = "a"

    class Bad:
        type = "text"
        text = None

    class Good2:
        type = "text"
        text = "b"

    class SdkResponse:
        content = [Good1(), Bad(), Good2()]
        prompt = "q"

    judge = StubJudge(canned_score=0.4)
    reading = JudgeConfidenceSignal(judge=judge, rubric="r", threshold=0.7).measure(SdkResponse())
    assert reading.value == 0.4
    assert judge.last_call == ("q", "ab", "r")


# Issue #73: a judge that returns a verdict without a usable `.score` means
# "couldn't measure" — not "scored zero". The old `float(... or 0.0)` collapsed
# a missing score to 0.0, which then tripped (`0.0 < threshold`) and silently
# escalated every request to the expensive model. The contract is the same
# `value=None, trip=False` that the empty-text guard above honors.


@dataclass
class _CannedJudge:
    """`score(...)` returns whatever verdict object it was handed."""

    verdict: Any

    def score(self, prompt: str, response_text: str, *, rubric: str) -> Any:
        return self.verdict


@dataclass
class _NoScoreVerdict:
    """A verdict object that has no `.score` attribute at all."""

    reasoning: str = "judge returned an unexpected shape"


@dataclass
class _NullableVerdict:
    score: float | None


def test_judge_signal_missing_score_attr_reports_couldnt_measure() -> None:
    # Verdict object with no `.score` attribute → can't measure, must not trip.
    judge = _CannedJudge(verdict=_NoScoreVerdict())
    signal = JudgeConfidenceSignal(judge=judge, rubric="r", threshold=0.7)
    reading = signal.measure(FakeResponse(text="answer", prompt="q"))
    assert reading.value is None
    assert reading.trip is False


def test_judge_signal_none_score_reports_couldnt_measure() -> None:
    # Explicit `score=None` is the same "couldn't measure" case.
    judge = _CannedJudge(verdict=_NullableVerdict(score=None))
    signal = JudgeConfidenceSignal(judge=judge, rubric="r", threshold=0.7)
    reading = signal.measure(FakeResponse(text="answer", prompt="q"))
    assert reading.value is None
    assert reading.trip is False


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_judge_signal_nonfinite_score_reports_couldnt_measure(bad: float) -> None:
    # A non-finite score isn't a valid [0, 1] measurement (#36/#71 sweep): a
    # NaN would make `score < threshold` always-false (silent disable), so it
    # must surface as "couldn't measure", not as a value.
    judge = _CannedJudge(verdict=_NullableVerdict(score=bad))
    signal = JudgeConfidenceSignal(judge=judge, rubric="r", threshold=0.7)
    reading = signal.measure(FakeResponse(text="answer", prompt="q"))
    assert reading.value is None
    assert reading.trip is False


def test_judge_signal_genuine_zero_score_still_trips() -> None:
    # Regression guard for the fix: a *real* 0.0 score (judge says the output
    # is totally unfaithful) is finite and must still trip — only a missing
    # score is exempt.
    judge = _CannedJudge(verdict=_NullableVerdict(score=0.0))
    signal = JudgeConfidenceSignal(judge=judge, rubric="r", threshold=0.7)
    reading = signal.measure(FakeResponse(text="answer", prompt="q"))
    assert reading.value == 0.0
    assert reading.trip is True


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


# ----------------------------------------------------------------------
# Signal-name uniqueness (#32)
# ----------------------------------------------------------------------
# D-009 designates `RouterDecision.signal_values` as the per-signal
# telemetry the savings dashboard reads for cost attribution. Two signals
# sharing a `name` would silently overwrite each other in that dict — the
# guard catches it at construction so the failure is loud and names the
# offending duplicate.


def test_duplicate_signal_names_raises_at_construction() -> None:
    adapter = StubAdapter(response=FakeResponse(text="x"))
    trip = SignalReading(value=0.9, trip=True)
    no_trip = SignalReading(value=0.1, trip=False)
    with pytest.raises(ValueError, match=r"duplicate signal names: \['judge'\]"):
        UncertaintyRouter(
            cheap_model="claude-haiku-4-5",
            strong_model="claude-opus-4-7",
            cheap_adapter=adapter,
            signals=[
                ConstantSignal(name="judge", reading=trip),
                ConstantSignal(name="judge", reading=no_trip),
            ],
        )


def test_duplicate_signal_names_message_lists_only_colliding_name() -> None:
    adapter = StubAdapter(response=FakeResponse(text="x"))
    r = SignalReading(value=0.0, trip=False)
    with pytest.raises(ValueError, match=r"duplicate signal names: \['judge'\]") as exc_info:
        UncertaintyRouter(
            cheap_model="claude-haiku-4-5",
            strong_model="claude-opus-4-7",
            cheap_adapter=adapter,
            signals=[
                ConstantSignal(name="judge", reading=r),
                ConstantSignal(name="entropy", reading=r),
                ConstantSignal(name="judge", reading=r),
            ],
        )
    assert "entropy" not in str(exc_info.value)


def test_distinct_names_with_same_signal_class_construct_cleanly() -> None:
    # Legitimate use case: two judges with different rubrics, deliberately
    # given distinct names so the dashboard can attribute escalation cost
    # per-rubric. The guard must not over-trigger on this.
    adapter = StubAdapter(response=FakeResponse(text="answer", prompt="q"))
    judge_a = StubJudge(canned_score=0.3)
    judge_b = StubJudge(canned_score=0.9)
    router = UncertaintyRouter(
        cheap_model="claude-haiku-4-5",
        strong_model="claude-opus-4-7",
        cheap_adapter=adapter,
        signals=[
            JudgeConfidenceSignal(
                judge=judge_a, rubric="faithfulness", threshold=0.7, name="judge_factual"
            ),
            JudgeConfidenceSignal(
                judge=judge_b, rubric="safety", threshold=0.7, name="judge_safety"
            ),
        ],
    )
    decision = router.route({})
    # First (factual) trips, second (safety) does not — both readings recorded.
    assert decision.triggered_signal == "judge_factual"
    assert decision.signal_values == {"judge_factual": 0.3, "judge_safety": 0.9}


def test_default_name_pairing_entropy_plus_judge_still_constructs() -> None:
    # Regression pin: the canonical default-name shape (one EntropySignal +
    # one JudgeConfidenceSignal) has different defaults ("entropy" vs
    # "judge"), so the guard must not break the README's recommended setup.
    adapter = StubAdapter(response=FakeResponse(text="ok", prompt="q"))
    judge = StubJudge(canned_score=0.8)
    router = UncertaintyRouter(
        cheap_model="claude-haiku-4-5",
        strong_model="claude-opus-4-7",
        cheap_adapter=adapter,
        signals=[
            EntropySignal(),
            JudgeConfidenceSignal(judge=judge, rubric="faithfulness"),
        ],
    )
    # Constructed fine; no logprobs on the response so entropy returns
    # None / no-trip and judge passes (0.8 >= 0.7), so cheap stays.
    decision = router.route({})
    assert decision.model_id == "claude-haiku-4-5"
    assert decision.triggered_signal is None
    assert set(decision.signal_values.keys()) == {"entropy", "judge"}


# Issue #36: EntropySignal and JudgeConfidenceSignal thresholds were
# unvalidated. NaN/Infinity silently disabled the escalation gate; out-of-
# bounds values silently inverted it. Either failure mode reverses D-009's
# savings-dashboard intent without diagnostic.
class TestEntropySignalThresholdValidation:
    @pytest.mark.parametrize(
        "bad",
        [-0.01, -1.0, float("nan"), float("inf"), float("-inf")],
    )
    def test_rejects_non_finite_or_negative(self, bad: float) -> None:
        with pytest.raises(
            ValueError, match=r"EntropySignal\.threshold must be a finite number >= 0\.0"
        ):
            EntropySignal(threshold=bad)

    def test_accepts_zero_boundary(self) -> None:
        # Zero is meaningful — "trip on any nonzero entropy".
        sig = EntropySignal(threshold=0.0)
        assert sig.threshold == 0.0

    def test_accepts_default(self) -> None:
        sig = EntropySignal()
        assert sig.threshold == 1.5


class TestJudgeConfidenceSignalThresholdValidation:
    @pytest.mark.parametrize(
        "bad",
        [-0.01, 1.01, 2.0, float("nan"), float("inf"), float("-inf")],
    )
    def test_rejects_out_of_bounds_or_non_finite(self, bad: float) -> None:
        from cost_optimizer.router import JudgeConfidenceSignal as JCS

        class _StubJudge:
            def score(self, *_a, **_k):
                class V:
                    score = 0.5

                return V()

        with pytest.raises(
            ValueError,
            match=r"JudgeConfidenceSignal\.threshold must be a finite number in \[0\.0, 1\.0\]",
        ):
            JCS(judge=_StubJudge(), rubric="faithfulness", threshold=bad)

    def test_accepts_inclusive_boundaries(self) -> None:
        class _StubJudge:
            def score(self, *_a, **_k):
                class V:
                    score = 0.5

                return V()

        for ok in (0.0, 0.5, 1.0):
            sig = JudgeConfidenceSignal(judge=_StubJudge(), rubric="faithfulness", threshold=ok)
            assert sig.threshold == ok


# ----------------------------------------------------------------------
# SignalReading contract: value=None ("couldn't measure") must not trip
# (#81). The router only counts non-None readings in per_signal_measured,
# so a value=None+trip=True reading breaks the trips<=measured invariant.
# ----------------------------------------------------------------------


def test_signal_reading_rejects_none_value_with_trip() -> None:
    with pytest.raises(ValueError, match=r"value=None, trip=True\) is invalid"):
        SignalReading(value=None, trip=True)


def test_signal_reading_allows_none_value_without_trip() -> None:
    # "couldn't measure, so don't trip" — the contract the built-in signals follow.
    r = SignalReading(value=None, trip=False)
    assert r.value is None
    assert r.trip is False


@pytest.mark.parametrize("trip", [True, False])
def test_signal_reading_allows_measured_value_either_trip(trip: bool) -> None:
    r = SignalReading(value=0.42, trip=trip)
    assert r.value == 0.42
    assert r.trip is trip
