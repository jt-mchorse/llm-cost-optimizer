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

import json
import math
from collections.abc import Mapping
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


def _message_without_content(usage: Any) -> Any:
    """A succeeded message with no `content` attribute at all.

    Distinct from `_message(None, usage)` only in how it is built, and both
    reach the guard as `getattr(message, "content", None) is None`. Both rows
    are in the table because a reader asked "can this really happen" should see
    the two producers.
    """
    return SimpleNamespace(usage=usage)


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
    # --- #213: the rows D-017's population rule names and its guard did not
    # implement. The block check swept one type where the container check
    # above it swept the whole space, and an absent `content` answered the
    # opposite of an absent `usage` to the same question.
    ("str-content-block", _message(["hello"], _object_usage()), "content block 0 is str-shaped"),
    (
        "bytes-content-block",
        _message([b"hello"], _object_usage()),
        "content block 0 is bytes-shaped",
    ),
    ("int-content-block", _message([7], _object_usage()), "content block 0 is int-shaped"),
    ("bool-content-block", _message([True], _object_usage()), "content block 0 is bool-shaped"),
    ("float-content-block", _message([1.5], _object_usage()), "content block 0 is float-shaped"),
    ("none-content-block", _message([None], _object_usage()), "content block 0 is None"),
    (
        "list-content-block",
        _message([[_object_block()]], _object_usage()),
        "content block 0 is list-shaped",
    ),
    (
        "str-block-second-position",
        _message([_object_block(), "hello"], _object_usage()),
        "content block 1 is str-shaped",
    ),
    ("content-is-none", _message(None, _object_usage()), "carried no content"),
    ("content-absent", _message_without_content(_object_usage()), "carried no content"),
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
    assert len(_UNREADABLE) >= 21
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


# --- #213: the two halves D-017's population rule already named -----------


def test_an_absent_content_and_an_empty_content_are_different_rows() -> None:
    """The pair that has to stay apart, asserted together.

    `[]` is a legitimate empty completion and keeps `error=None`. An absent
    `content` is a row we could not read, and reports — the same answer its
    `usage` sibling has always given to the same question. They are
    distinguishable because `getattr(message, "content", None)` returns `[]`
    for the one and `None` for the other; a fix that collapsed them would
    either re-open this hole or start flagging correct rows.
    """
    empty = _from_sdk_result_row(_entry(_message([], _object_usage())))
    assert empty.error is None
    assert empty.response_text == ""
    assert (empty.prompt_tokens, empty.completion_tokens) == (10, 5)

    for label, message in (
        ("content=None", _message(None, _object_usage())),
        ("content absent", _message_without_content(_object_usage())),
    ):
        row = _from_sdk_result_row(_entry(message))
        assert row.error is not None, label
        assert "carried no content" in row.error, label


def test_absent_content_is_worded_as_the_sibling_of_absent_usage() -> None:
    """Same question, same answer, same sentence shape.

    The asymmetry this closes was not only behavioural: the guard already said
    of the token attributes that "absence is a shape failure, not a zero". The
    two reasons now read as a pair, which is how the next reader sees that they
    are one rule.
    """
    no_content = _succeeded_row_shape_error(_message_without_content(_object_usage()))
    no_usage = _succeeded_row_shape_error(_message([_object_block()], None))
    assert no_content == "succeeded result carried no content"
    assert no_usage == "succeeded result carried no usage"


def test_no_value_a_json_decoder_produces_is_accepted_as_a_content_block() -> None:
    """The partition, discovered rather than restated.

    The silent set was never "dict" — it was "a decoded payload where a
    modelled object is expected", which is what a `model_dump()` payload, a
    gateway/proxy client or a `json.loads` of a raw response hands over. So the
    population is the JSON alphabet, and this test *reads it off a decoder*
    instead of hand-listing the types again — a hand-list growing one entry at
    a time is precisely what left the block check at one type while the
    container check above it had a catch-all.
    """
    decoded = json.loads(
        '{"o": {"k": 1}, "a": [1, 2], "s": "x", "i": 7, "f": 1.5, "t": true, "n": null}'
    )
    values = [decoded, *decoded.values()]
    assert len(values) >= 8, "the sample must actually exercise the alphabet"
    seen = {type(v).__name__ for v in values}
    assert {"dict", "list", "str", "int", "float", "bool", "NoneType"} <= seen

    for value in values:
        reason = _succeeded_row_shape_error(_message([value], _object_usage()))
        assert reason is not None, value
        assert "content block 0" in reason, (value, reason)


def test_an_object_block_is_never_flagged_however_empty() -> None:
    """The other side of the partition, and the reason it is on shape.

    Every one of these is a modelled object with no readable `.text`, which is
    exactly what a `tool_use` block is. None may report.
    """
    for block in (
        _tool_use_block(),
        SimpleNamespace(),
        SimpleNamespace(type="thinking", thinking="..."),
        SimpleNamespace(type="text", text=None),
        SimpleNamespace(type="text", text=7),
    ):
        assert _succeeded_row_shape_error(_message([block], _object_usage())) is None, block


# --- the neighbours for *this* change, built and run ----------------------


def _block_without_text_neighbour(block: Any) -> bool:
    """The tempting block-level fix: flag a block that yields no text.

    It is the outcome-based guard from #211 moved one level down, and it fails
    the same way — `tool_use` is an object block that carries no `.text` by
    design.
    """
    return not isinstance(getattr(block, "text", None), str)


def test_the_block_without_text_neighbour_flags_the_tool_use_row() -> None:
    assert _block_without_text_neighbour("hello")  # would catch the real defect
    assert _block_without_text_neighbour(_tool_use_block())  # and a correct row
    # What shipped separates them.
    assert _succeeded_row_shape_error(_message(["hello"], _object_usage())) is not None
    assert _succeeded_row_shape_error(_message([_tool_use_block()], _object_usage())) is None


def _mapping_and_str_only_neighbour(block: Any) -> bool:
    """The hand-list grown by exactly one entry.

    "The comment names `str` and `Mapping`, so check `str` and `Mapping`" is
    the smallest change that satisfies the row this issue leads with, and it
    leaves every other decoded value silent — the same one-at-a-time growth
    that produced the gap.
    """
    return isinstance(block, (str, Mapping))


def test_the_str_plus_mapping_neighbour_still_misses_four_decoded_shapes() -> None:
    missed = [b for b in (b"x", 7, 1.5, None, [1]) if not _mapping_and_str_only_neighbour(b)]
    assert len(missed) == 5, missed
    for block in missed:
        assert _succeeded_row_shape_error(_message([block], _object_usage())) is not None, block


def _absent_content_is_empty_neighbour(entry: Any) -> BatchResultRow:
    """The unfixed content read, written out rather than wrapped.

    `content = getattr(message, "content", []) or []` treats absence and
    emptiness as the same thing, which is how the absent row reported a
    successful empty answer with full token charges.
    """
    result = getattr(entry, "result", None)
    message = getattr(result, "message", None)
    content = getattr(message, "content", []) or []
    usage = getattr(message, "usage", None)
    return BatchResultRow(
        custom_id=getattr(entry, "custom_id", ""),
        response_text="".join(
            block_text
            for block in content
            if isinstance(block_text := getattr(block, "text", None), str)
        ),
        prompt_tokens=_coerce_token_count(getattr(usage, "input_tokens", 0)),
        completion_tokens=_coerce_token_count(getattr(usage, "output_tokens", 0)),
    )


def test_the_absent_is_empty_neighbour_reports_a_successful_empty_answer() -> None:
    """The defect, reproduced against the neighbour rather than described."""
    entry = _entry(_message_without_content(_object_usage()))
    stale = _absent_content_is_empty_neighbour(entry)
    assert stale.error is None
    assert stale.response_text == ""
    assert (stale.prompt_tokens, stale.completion_tokens) == (10, 5)  # charged in full

    fixed = _from_sdk_result_row(entry)
    assert fixed.error is not None
    assert fixed.response_text is None
