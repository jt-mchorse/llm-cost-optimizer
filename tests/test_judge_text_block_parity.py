"""Both signals agree on which block is the text (#345).

#273 gave `_extract_first_token_logprobs` a fallback for content blocks with no
`type` ("an older fake or adapter keeps the old first-block behaviour"), while
`_extract_text` -- which feeds `JudgeConfidenceSignal` -- still required
`type == "text"`. Measured on `main` 9771c24: for one untyped block,
`EntropySignal` read value=1.386 while `JudgeConfidenceSignal` abstained with
value=None/trip=False; the same block typed gave the judge value=0.1/trip=True.
An abstention reads exactly like a confident cheap answer, so the judge's
escalation was silently off for those responses.
"""

from __future__ import annotations

import types
from typing import Any

import pytest

from cost_optimizer.router import (
    EntropySignal,
    JudgeConfidenceSignal,
    _extract_first_token_logprobs,
    _extract_text,
)

LOGPROBS = [{"top_logprobs": [{"logprob": -1.386}] * 4}]


class _LowJudge:
    def score(self, prompt: str, text: str, rubric: str) -> Any:
        return types.SimpleNamespace(score=0.1)


def _judge() -> JudgeConfidenceSignal:
    return JudgeConfidenceSignal(judge=_LowJudge(), rubric="r", threshold=0.7)


def _block(text: str, **extra: Any) -> dict[str, Any]:
    return {"text": text, "logprobs": [{"top_logprobs": [{"logprob": -0.7}]}], **extra}


# --- red on the unfixed code: the untyped fallback reaches the judge too ---


def test_untyped_block_is_judged_the_way_entropy_measures_it() -> None:
    # `prompt` because the judge abstains without one to grade against (#356).
    response = {"content": [{"text": "hi", "logprobs": LOGPROBS}], "prompt": "q"}
    entropy = EntropySignal().measure(response)
    judged = _judge().measure(response)
    assert entropy.value == pytest.approx(1.386, abs=1e-3)
    assert (judged.value, judged.trip) == (pytest.approx(0.1), True)


def test_untyped_object_blocks_too() -> None:
    blocks = [types.SimpleNamespace(text="a"), types.SimpleNamespace(text="b")]
    assert _extract_text(types.SimpleNamespace(content=blocks)) == "ab"


# --- controls: green before and after ---


def test_typed_block_is_judged() -> None:
    judged = _judge().measure(
        {"content": [{"type": "text", "text": "hi", "logprobs": LOGPROBS}], "prompt": "q"}
    )
    assert (judged.value, judged.trip) == (pytest.approx(0.1), True)


def test_thinking_then_text_reads_only_the_text() -> None:
    content = [{"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "hi"}]
    assert _extract_text({"content": content}) == "hi"


def test_no_text_block_still_abstains() -> None:
    # With a prompt, so it is the missing text that abstains (#356).
    judged = _judge().measure({"content": [{"type": "thinking", "thinking": "x"}], "prompt": "q"})
    assert (judged.value, judged.trip) == (None, False)


def test_mixed_typed_and_untyped_takes_only_typed_text() -> None:
    # Once any block declares a `type`, an untyped neighbour is not text.
    content = [{"text": "stray"}, {"type": "text", "text": "hi"}]
    assert _extract_text({"content": content}) == "hi"


# --- one source of truth: both extractors pick the same block ---

PARITY = [
    ("untyped", [_block("A")], "A"),
    ("typed", [_block("A", type="text")], "A"),
    ("thinking first", [{"type": "thinking", "thinking": "t"}, _block("A", type="text")], "A"),
    ("mixed", [_block("stray"), _block("A", type="text")], "A"),
    ("tool_use only", [{"type": "tool_use", "id": "x", "name": "n", "input": {}}], None),
]


@pytest.mark.parametrize(("label", "content", "first"), PARITY, ids=[p[0] for p in PARITY])
def test_both_extractors_agree_on_the_text_block(
    label: str, content: list[dict[str, Any]], first: str | None
) -> None:
    text = _extract_text({"content": content})
    logprobs = _extract_first_token_logprobs({"content": content})
    if first is None:
        assert text == "", label
        assert logprobs is None, label
    else:
        assert text.startswith(first), label
        assert logprobs == pytest.approx([-0.7]), label
