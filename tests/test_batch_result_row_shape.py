"""A succeeded batch row must not report empty text or zero tokens as success (#211, D-017).

`_from_sdk_result_row` reads a succeeded row entirely through `getattr`. That
is a *consistent* contract — unlike `cache_wrapper._get_usage` (#209) there is
no dict road to be asymmetric with — and a dict *entry* already fails loudly at
the first hop. What had no guard at all was a dict-shaped value **nested inside**
an object-shaped entry: what a gateway/proxy client, a `model_dump()`-style
payload, or a downstream consumer's hand-built fake produces.

Measured on the unfixed code, and the last three rows are the ones that decide
the design:

    content                  response_text   in  out  error
    object block             'hello'         10    5  None
    dict block               ''              10    5  None
    object + dict usage      'hello'          0    0  None
    object + usage=None      'hello'          0    0  None    <- not in the issue
    tool_use-only block      ''              10    5  None    <- CORRECT, must stay
    [] empty content         ''              10    5  None    <- CORRECT, must stay

**The guard discriminates on shape, never on outcome.** "A succeeded row with
an empty `response_text` is malformed" reads as the obvious fix, satisfies every
broken row above, and then flags two correct ones. `test_the_outcome_based_
neighbour_flags_correct_rows` builds it and runs it.

It also does not claim a value that *was* read and found unreasonable:
`_MalformedUsage(NaN, "abc")` keeps its documented abstention to `0` per #136.
Unreadable is a shape problem; unreasonable is a value problem, and only the
first one hides work that actually happened.
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from cost_optimizer.batch import (
    BatchResultRow,
    _from_sdk_result_row,
    _succeeded_row_shape_error,
)
from cost_optimizer.pricing import _coerce_token_count

# --- fixtures -------------------------------------------------------------


def _entry(message: Any, *, custom_id: str = "r-1") -> Any:
    """A succeeded SDK result entry carrying `message`."""
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(type="succeeded", message=message),
    )


def _message(content: Any, usage: Any) -> Any:
    return SimpleNamespace(content=content, usage=usage)


def _object_block(text: str = "hello") -> Any:
    return SimpleNamespace(type="text", text=text)


def _dict_block(text: str = "hello") -> dict[str, str]:
    return {"type": "text", "text": text}


def _tool_use_block() -> Any:
    """An object block that legitimately carries no `.text`."""
    return SimpleNamespace(type="tool_use", id="tu_1", name="get_weather", input={})


def _object_usage(prompt: int = 10, completion: int = 5) -> Any:
    return SimpleNamespace(input_tokens=prompt, output_tokens=completion)


def _dict_usage(prompt: int = 10, completion: int = 5) -> dict[str, int]:
    return {"input_tokens": prompt, "output_tokens": completion}


# --- the shapes that must now surface as an error -------------------------

#: (case id, message, substring the field-named error must contain)
_UNREADABLE = [
    ("dict-content-block", _message([_dict_block()], _object_usage()), "content block 0"),
    (
        "dict-block-second-position",
        _message([_object_block(), _dict_block()], _object_usage()),
        "content block 1",
    ),
    ("dict-usage", _message([_object_block()], _dict_usage()), "usage is dict-shaped"),
    ("both-dict", _message([_dict_block()], _dict_usage()), "content block 0"),
    ("no-usage", _message([_object_block()], None), "carried no usage"),
    ("no-message", None, "carried no message"),
    ("content-is-str", _message("hello", _object_usage()), "content is str-shaped"),
    ("content-is-dict", _message({"type": "text"}, _object_usage()), "content is dict-shaped"),
    ("content-is-int", _message(7, _object_usage()), "not a sequence"),
    ("usage-missing-both", _message([_object_block()], SimpleNamespace()), "input_tokens"),
    (
        "usage-missing-output",
        _message([_object_block()], SimpleNamespace(input_tokens=3)),
        "output_tokens",
    ),
]


@pytest.mark.parametrize(
    ("case", "message", "phrase"), _UNREADABLE, ids=[c for c, _, _ in _UNREADABLE]
)
def test_an_unreadable_succeeded_row_reports_an_error_not_a_success(
    case: str, message: Any, phrase: str
) -> None:
    row = _from_sdk_result_row(_entry(message))
    assert row.error is not None, case
    assert phrase in row.error, row.error
    # Same shape the two pre-existing error branches use, so a consumer's
    # `if row.error:` arm needs no new case.
    assert row.response_text is None
    assert row.prompt_tokens == 0
    assert row.completion_tokens == 0
    # The row is still identifiable: an operator has to know *which* request.
    assert row.custom_id == "r-1"


def test_the_unreadable_population_is_not_empty() -> None:
    """Anti-vacuous: an empty parametrize list passes on nothing."""
    assert len(_UNREADABLE) >= 10
    assert len({c for c, _, _ in _UNREADABLE}) == len(_UNREADABLE)


# --- the shapes that must keep reporting success --------------------------


class _GarbageUsage:
    """Readable attributes holding unreasonable values (#136's contract)."""

    def __init__(self, prompt: Any, completion: Any) -> None:
        self.input_tokens = prompt
        self.output_tokens = completion


#: (case id, message, expected response_text, expected (in, out))
_STILL_SUCCESS = [
    ("plain-object-row", _message([_object_block()], _object_usage()), "hello", (10, 5)),
    ("tool-use-only", _message([_tool_use_block()], _object_usage()), "", (10, 5)),
    ("empty-content", _message([], _object_usage()), "", (10, 5)),
    (
        "tool-use-plus-text",
        _message([_tool_use_block(), _object_block("hi")], _object_usage()),
        "hi",
        (10, 5),
    ),
    (
        "readable-but-garbage-tokens",
        _message([_object_block()], _GarbageUsage(math.nan, "abc")),
        "hello",
        (0, 0),
    ),
    (
        "readable-but-negative-tokens",
        _message([_object_block()], _GarbageUsage(-3, -1)),
        "hello",
        (0, 0),
    ),
    ("content-is-a-tuple", _message((_object_block(),), _object_usage()), "hello", (10, 5)),
]


@pytest.mark.parametrize(
    ("case", "message", "text", "tokens"), _STILL_SUCCESS, ids=[c for c, _, _, _ in _STILL_SUCCESS]
)
def test_a_readable_succeeded_row_is_untouched(
    case: str, message: Any, text: str, tokens: tuple[int, int]
) -> None:
    row = _from_sdk_result_row(_entry(message))
    assert row.error is None, f"{case}: {row.error}"
    assert row.response_text == text
    assert (row.prompt_tokens, row.completion_tokens) == tokens


def test_136_abstention_contract_is_explicitly_preserved() -> None:
    """The line D-017 draws, stated as the thing it must not cross.

    #136 decided a present-but-malformed token value abstains to `0` rather
    than raising, because token accounting is best-effort observability
    gathered *after* the row already succeeded. The new guard fires on a field
    that could not be **read**; this one is a field that was read and held
    garbage. If a later edit collapses the two, this goes red.
    """
    for bad in (math.nan, math.inf, -math.inf, "abc", -3, object()):
        row = _from_sdk_result_row(_entry(_message([_object_block()], _GarbageUsage(bad, bad))))
        assert row.error is None, bad
        assert row.response_text == "hello"
        assert row.prompt_tokens == 0
        assert row.completion_tokens == 0


# --- the plausible neighbouring fix, built and run ------------------------


def _outcome_based_neighbour(entry: Any) -> BatchResultRow:
    """The fix that reads as correct: flag a succeeded row with no text.

    It needs no shape reasoning at all, which is exactly why it is the one
    someone would write. It is wrong because "produced no text" is an
    *outcome* two correct shapes also produce.

    Written out from the **unfixed** read rather than wrapped around
    `_from_sdk_result_row`, because wrapping the shipped function would let the
    neighbour inherit the very guard it is supposed to lack — a simulation that
    can only agree with itself.
    """
    custom_id = getattr(entry, "custom_id", "")
    result = getattr(entry, "result", None)
    if result is None or getattr(result, "type", None) != "succeeded":
        return _from_sdk_result_row(entry)  # unchanged branches, not under test
    message = getattr(result, "message", None)
    content = getattr(message, "content", []) or []
    text_parts = [
        block_text
        for block in content
        if isinstance(block_text := getattr(block, "text", None), str)
    ]
    response_text = "".join(text_parts)
    usage = getattr(message, "usage", None)
    if not response_text:
        return BatchResultRow(
            custom_id=custom_id,
            response_text=None,
            prompt_tokens=0,
            completion_tokens=0,
            error="succeeded row produced no text",
        )
    return BatchResultRow(
        custom_id=custom_id,
        response_text=response_text,
        prompt_tokens=_coerce_token_count(getattr(usage, "input_tokens", 0)),
        completion_tokens=_coerce_token_count(getattr(usage, "output_tokens", 0)),
    )


def test_the_outcome_based_neighbour_flags_correct_rows() -> None:
    """Run it, don't reason about it.

    A `tool_use`-only response and an empty `content` are legitimate succeeded
    rows with empty text. The outcome-based guard turns both into errors —
    which on this path means a caller's own correct batch starts reporting
    failures, strictly worse than the silent zero it was meant to replace.
    """
    for case in ("tool-use-only", "empty-content"):
        message = next(m for c, m, _, _ in _STILL_SUCCESS if c == case)
        assert _from_sdk_result_row(_entry(message)).error is None, case
        # The neighbour disagrees, on a row that is correct.
        assert _outcome_based_neighbour(_entry(message)).error is not None, case


def test_the_outcome_based_neighbour_also_misses_the_dict_usage_row() -> None:
    """And it does not even close the whole issue.

    `object content + dict usage` yields `'hello'` — non-empty — so the
    outcome-based guard passes it through with `prompt_tokens=0`, leaving the
    cost half of #211 exactly where it was.
    """
    message = _message([_object_block()], _dict_usage())
    assert _outcome_based_neighbour(_entry(message)).error is None
    # What shipped catches it.
    assert _from_sdk_result_row(_entry(message)).error is not None


# --- the classifier in isolation ------------------------------------------


def test_shape_error_returns_none_for_every_readable_message() -> None:
    for case, message, _, _ in _STILL_SUCCESS:
        assert _succeeded_row_shape_error(message) is None, case


def test_shape_error_names_the_field_for_every_unreadable_message() -> None:
    for case, message, phrase in _UNREADABLE:
        reason = _succeeded_row_shape_error(message)
        assert reason is not None, case
        assert phrase in reason, (case, reason)
        # A reason a human reads in a results dump, not a repr.
        assert reason == reason.strip()
        assert "\\n" not in reason


def test_a_failed_row_never_reaches_the_shape_guard() -> None:
    """The guard is scoped to `type == "succeeded"`; the other branches win first.

    A row that failed upstream already has a real error and must keep it —
    replacing it with a shape complaint would bury the actual cause.
    """
    failed = SimpleNamespace(
        custom_id="r-err",
        result=SimpleNamespace(type="errored", error="rate_limit", message=None),
    )
    row = _from_sdk_result_row(failed)
    assert row.error == "rate_limit"

    missing = SimpleNamespace(custom_id="r-none", result=None)
    assert _from_sdk_result_row(missing).error == "missing result"
