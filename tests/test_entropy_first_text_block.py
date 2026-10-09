"""EntropySignal reads the first TEXT block, not content[0] (#273).

`measure`'s comment says "We look at the first text block's first token's
distribution", and `_extract_first_token_logprobs` read `content[0]` whatever
its type. Measured on `main` (a hunt agent, re-run here): a response that leads
with a `thinking` block gave `SignalReading(value=None, trip=False)`; the same
text block alone gave `value=0.693, trip=True`. With extended thinking the
signal never escalated, and the reading looks like a confident cheap response.
"""

from __future__ import annotations

import types

import pytest

from cost_optimizer.router import EntropySignal

TEXT = {
    "type": "text",
    "text": "x",
    "logprobs": [{"top_logprobs": [{"logprob": -0.7}, {"logprob": -0.7}]}],
}


@pytest.mark.parametrize(
    "leading",
    [
        [{"type": "thinking", "thinking": "..."}],
        [{"type": "redacted_thinking", "data": "x"}],
        [
            {"type": "thinking", "thinking": "..."},
            {"type": "tool_use", "id": "t", "name": "n", "input": {}},
        ],
    ],
    ids=["thinking", "redacted_thinking", "thinking+tool_use"],
)
def test_a_text_block_after_other_blocks_is_measured(leading: list[dict]) -> None:
    reading = EntropySignal(threshold=0.5).measure({"content": [*leading, TEXT]})
    assert reading.value == pytest.approx(0.693, abs=1e-3)
    assert reading.trip is True


def test_object_shaped_blocks_too() -> None:
    blocks = [types.SimpleNamespace(type="thinking", thinking="..."), types.SimpleNamespace(**TEXT)]
    assert EntropySignal(threshold=0.5).measure(types.SimpleNamespace(content=blocks)).trip is True


def test_no_text_block_abstains() -> None:
    reading = EntropySignal(threshold=0.5).measure(
        {"content": [{"type": "thinking", "thinking": "..."}]}
    )
    assert (reading.value, reading.trip) == (None, False)


def test_untyped_blocks_keep_the_first_block_behaviour() -> None:
    # Older fakes and adapters build blocks with no `type`.
    reading = EntropySignal(threshold=0.5).measure({"content": [{"logprobs": TEXT["logprobs"]}]})
    assert reading.trip is True
