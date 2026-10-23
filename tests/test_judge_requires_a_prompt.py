"""#356: `JudgeConfidenceSignal` abstains when the response carries no prompt.

`route()` hands signals only the cheap *response*. A real SDK `Message` has no
`prompt` field, so `measure` read `None`, called `judge.score("", text, ...)`,
and the score it got back decided the routing: the judge graded an answer
against an empty question. `tests/test_router_read_position_vocabulary.py`
already names that outcome as worse than an abstain; it covered a Mapping
response that *has* a `prompt` key, not one that has none.

The red arms are the prompt-less shapes, each asserting the judge is never
called. The controls show a response that carries its prompt is still scored,
still trips, and still escalates through `route()`.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from cost_optimizer import JudgeConfidenceSignal, SignalReading, UncertaintyRouter


class _RecordingJudge:
    """Returns a low score (trip under threshold 0.5) and records every call."""

    def __init__(self, score: float = 0.2) -> None:
        self._score = score
        self.calls: list[tuple[Any, str]] = []

    def score(self, prompt: Any, text: str, rubric: str | None = None) -> Any:
        self.calls.append((prompt, text))
        return types.SimpleNamespace(score=self._score)


class _Adapter:
    def __init__(self, response: Any) -> None:
        self._response = response

    def call_cheap(self, request: Any) -> Any:
        return self._response


def _signal(judge: Any) -> JudgeConfidenceSignal:
    return JudgeConfidenceSignal(judge=judge, rubric="r", threshold=0.5)


def _message(**extra: Any) -> types.SimpleNamespace:
    """Shaped like `anthropic.types.Message`: typed text blocks, no `prompt`."""
    block = types.SimpleNamespace(type="text", text="Paris")
    return types.SimpleNamespace(role="assistant", content=[block], **extra)


def _mapping(**extra: Any) -> dict[str, Any]:
    """A `Message.model_dump()`-shaped dict."""
    return {"role": "assistant", "content": [{"type": "text", "text": "Paris"}], **extra}


SHAPES = {"object": _message, "mapping": _mapping}

# `missing` is the issue's case: what a real SDK response looks like.
NO_PROMPT = {
    "missing": {},
    "none": {"prompt": None},
    "empty": {"prompt": ""},
    "blank": {"prompt": "  \n"},
    "non-str": {"prompt": 42},
}


# --- red on the unfixed code ------------------------------------------------


@pytest.mark.parametrize("variant", list(NO_PROMPT))
@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_response_without_a_prompt_abstains_and_the_judge_is_not_called(
    shape: str, variant: str
) -> None:
    judge = _RecordingJudge()
    reading = _signal(judge).measure(SHAPES[shape](**NO_PROMPT[variant]))
    assert reading == SignalReading(value=None, trip=False), (
        f"a {shape} response with prompt={variant} measured {reading!r}; the judge "
        f"graded {judge.calls!r} against no question"
    )
    assert judge.calls == []


def test_route_with_an_sdk_shaped_response_does_not_route_on_an_empty_prompt() -> None:
    """The issue's repro through `route()`: the README calls
    `router.route({"prompt": ...})`, and the judge never saw that prompt."""
    judge = _RecordingJudge(score=0.2)
    router = UncertaintyRouter(
        cheap_model="cheap",
        strong_model="strong",
        cheap_adapter=_Adapter(_message()),
        signals=[_signal(judge)],
    )
    decision = router.route({"prompt": "What is the capital of France?"})
    assert judge.calls == []
    assert decision.signal_values == {"judge": None}
    assert (decision.model_id, decision.triggered_signal) == ("cheap", None)
    # Counted as not measured, so a judge that never gets a prompt shows up in
    # telemetry rather than looking like a confident one.
    assert "judge" not in router.stats.per_signal_measured


# --- controls: green before and after ---------------------------------------


@pytest.mark.parametrize("shape", list(SHAPES))
def test_control_a_response_carrying_its_prompt_is_scored_and_trips(shape: str) -> None:
    judge = _RecordingJudge(score=0.2)
    reading = _signal(judge).measure(SHAPES[shape](prompt="What is the capital of France?"))
    assert reading == SignalReading(value=pytest.approx(0.2), trip=True)
    assert judge.calls == [("What is the capital of France?", "Paris")]


def test_control_the_prompt_is_passed_through_verbatim() -> None:
    # The blank check strips to decide; it does not rewrite what the judge sees.
    judge = _RecordingJudge(score=0.9)
    reading = _signal(judge).measure(_mapping(prompt="  q  "))
    assert reading == SignalReading(value=pytest.approx(0.9), trip=False)
    assert judge.calls == [("  q  ", "Paris")]


def test_control_route_escalates_when_the_adapter_attaches_the_prompt() -> None:
    judge = _RecordingJudge(score=0.2)
    router = UncertaintyRouter(
        cheap_model="cheap",
        strong_model="strong",
        cheap_adapter=_Adapter(_message(prompt="q")),
        signals=[_signal(judge)],
    )
    decision = router.route({"prompt": "q"})
    assert (decision.model_id, decision.triggered_signal) == ("strong", "judge")
    assert decision.signal_values == {"judge": pytest.approx(0.2)}
    assert router.stats.per_signal_measured == {"judge": 1}
