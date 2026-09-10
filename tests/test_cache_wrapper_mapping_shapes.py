"""``cache_wrapper`` reads a ``Mapping``, not only a ``dict`` (#215).

``batch.py`` argued the rule out in prose when #213 widened
``_succeeded_row_shape_error``: "``Mapping``/``Sequence`` rather than
``dict``/``list`` so the JSON alphabet's near relatives — a tuple of blocks'
worth of decoded values, a ``MappingProxyType`` — land on the same side."

``cache_wrapper.py`` still said ``dict`` and ``list``, at four sites. Measured
before the fix, 20 000 cached tokens in every row::

    dict response  + dict usage      (control)   cache_read_input_tokens -> 20000
    dict response  + Mapping usage               cache_read_input_tokens -> 0
    dict response  + UserDict usage              cache_read_input_tokens -> 0
    Mapping response (MappingProxyType)          cache_read_input_tokens -> 0
    UserDict response                            cache_read_input_tokens -> 0
    object response + dict usage      (control)  cache_read_input_tokens -> 20000
    object response + Mapping usage              cache_read_input_tokens -> 0
    object response + object usage    (control)  cache_read_input_tokens -> 20000

End to end at ``$5.00/MTok``, the four gap rows reported ``hits=0``,
``tokens_cached=0``, ``dollars_saved=$0.0000`` — which is #209's harm verbatim,
quoting its own docstring: "a well-formed field in the other container is
indistinguishable from a call that genuinely did no caching […] folded into
``aggregate`` (which only ever adds, so it never recovers) and out through
``dump_aggregate_json`` onto the savings dashboard."

``collections.UserDict`` is the sharp member: **not** a ``dict`` subclass, but a
``Mapping``, and the ordinary base for a gateway/proxy client's response
wrapper.

The ``dict``/``list`` rows stay in every table below as the control, so this is
evidence about a widening rather than about a blanket accept. So do the rows for
the shapes that must keep abstaining — ``_get_usage``'s docstring draws that
line explicitly ("the bug was a well-formed value being read as absent, not a
malformed one being tolerated") and this change stays on its side of it.
"""

from __future__ import annotations

from collections import UserDict
from types import MappingProxyType
from typing import Any

import pytest

from cost_optimizer.cache_wrapper import (
    PromptCacheWrapper,
    _get_usage,
    _mark_messages_prefix,
    _mark_system,
    _mark_tools,
)
from cost_optimizer.pricing import ModelPricing
from cost_optimizer.shapes import is_block_sequence, is_decoded_json_value

USAGE: dict[str, int] = {
    "cache_read_input_tokens": 20_000,
    "cache_creation_input_tokens": 0,
    "input_tokens": 100,
    "output_tokens": 10,
}

PRICING = ModelPricing(model="claude-opus-4-8", input_per_mtok=5.0)

#: 20 000 tokens at $5.00/MTok with the default 0.1 read multiplier.
EXPECTED_SAVED = 20_000 * (5.0 / 1_000_000) * 0.9


class _ObjUsage:
    def __init__(self, d: dict[str, int]) -> None:
        self.__dict__.update(d)


class _ObjResponse:
    def __init__(self, usage: Any) -> None:
        self.usage = usage


class _FakeMessages:
    def __init__(self, response: Any) -> None:
        self._response = response

    def create(self, **kwargs: Any) -> Any:
        return self._response


class _FakeClient:
    def __init__(self, response: Any) -> None:
        self.messages = _FakeMessages(response)


def _wrapper(response: Any) -> PromptCacheWrapper:
    return PromptCacheWrapper(
        client=_FakeClient(response), model="claude-opus-4-8", pricing=PRICING
    )


# (label, response factory). Every row carries the SAME 20 000 cached tokens;
# the only thing that varies is the container they arrive in. Four were read as
# zero before #215 and four were the controls that always worked.
READABLE_ROWS: tuple[tuple[str, Any], ...] = (
    ("dict response + dict usage (control)", lambda: {"usage": dict(USAGE)}),
    ("dict response + MappingProxyType usage", lambda: {"usage": MappingProxyType(dict(USAGE))}),
    ("dict response + UserDict usage", lambda: {"usage": UserDict(dict(USAGE))}),
    ("MappingProxyType response", lambda: MappingProxyType({"usage": dict(USAGE)})),
    ("UserDict response", lambda: UserDict({"usage": dict(USAGE)})),
    ("object response + dict usage (control)", lambda: _ObjResponse(dict(USAGE))),
    (
        "object response + MappingProxyType usage",
        lambda: _ObjResponse(MappingProxyType(dict(USAGE))),
    ),
    ("object response + object usage (control)", lambda: _ObjResponse(_ObjUsage(USAGE))),
)


@pytest.mark.parametrize(("label", "factory"), READABLE_ROWS, ids=[r[0] for r in READABLE_ROWS])
def test_every_container_pairing_reads_the_same_tokens(label: str, factory: Any) -> None:
    usage = _get_usage(factory())
    assert getattr(usage, "cache_read_input_tokens", 0) == 20_000


@pytest.mark.parametrize(("label", "factory"), READABLE_ROWS, ids=[r[0] for r in READABLE_ROWS])
def test_every_container_pairing_reports_the_same_dollars(label: str, factory: Any) -> None:
    """The harm in the unit the dashboard shows. `$0.00` on a call that served
    20 000 tokens from cache is not distinguishable from a call that cached
    nothing, and `aggregate` only ever adds, so it never recovers.
    """
    telemetry = _wrapper(factory()).create(messages=[{"role": "user", "content": "hi"}]).telemetry
    assert telemetry.hits == 1
    assert telemetry.tokens_cached == 20_000
    assert telemetry.dollars_saved == pytest.approx(EXPECTED_SAVED)


def test_the_table_holds_both_the_gap_rows_and_the_controls() -> None:
    """Anti-vacuous. Three rows were already correct before #215 and are kept
    so the assertions above are evidence about a widening, not about a change
    that made everything pass by accepting anything.
    """
    labels = [label for label, _ in READABLE_ROWS]
    assert sum("control" in label for label in labels) == 3
    assert len(READABLE_ROWS) == 8


# --- the line that must NOT move ------------------------------------------

ABSTAINING_ROWS: tuple[tuple[str, Any], ...] = (
    ("usage is a list", lambda: {"usage": [1, 2]}),
    ("usage is a str", lambda: {"usage": "20000"}),
    ("usage is an int", lambda: {"usage": 20_000}),
    ("usage key absent", lambda: {}),
    ("usage is None", lambda: {"usage": None}),
    ("response is a list", lambda: [{"usage": dict(USAGE)}]),
    ("response is a str", lambda: "not a response"),
)


@pytest.mark.parametrize(("label", "factory"), ABSTAINING_ROWS, ids=[r[0] for r in ABSTAINING_ROWS])
def test_a_malformed_usage_still_abstains_to_zero(label: str, factory: Any) -> None:
    """`_get_usage`'s docstring draws this line: "the bug was a well-formed
    value being read as absent, not a malformed one being tolerated." A list, a
    string, an int and an absent key must keep reading `0` with no error — the
    "abstain, don't crash on malformed SDK shapes" contract #114/#136 set.

    `response is a list` is the row that would move if the widening had reached
    for `Sequence` on the *response* container too. It must not: a list is not a
    mapping, and `.get("usage")` on one is not a thing.
    """
    telemetry = _wrapper(factory()).create(messages=[{"role": "user", "content": "hi"}]).telemetry
    assert telemetry.tokens_cached == 0
    assert telemetry.dollars_saved == 0.0
    assert telemetry.hits == 0


# --- the same shape one level over: list vs Sequence ----------------------

BLOCKS: list[dict[str, str]] = [
    {"type": "text", "text": "alpha"},
    {"type": "text", "text": "omega"},
]


def _is_marked(blocks: Any) -> bool:
    return any("cache_control" in b for b in blocks)


@pytest.mark.parametrize(
    ("label", "system"),
    [
        ("list of blocks (control)", list(BLOCKS)),
        ("tuple of blocks", tuple(BLOCKS)),
    ],
    ids=["list", "tuple"],
)
def test_mark_system_marks_any_block_sequence(label: str, system: Any) -> None:
    """A tuple of blocks was returned UNMARKED and unreported: the API then
    caches nothing and the resulting `$0.00` is arithmetically honest and
    diagnostically useless. `_mark_tools` iterates without a type test and has
    handled a tuple correctly all along — the same job done properly in the
    same file, which is why it is asserted alongside.
    """
    assert _is_marked(_mark_system(system))
    assert _is_marked(_mark_tools(list(BLOCKS)))


@pytest.mark.parametrize(
    ("label", "content"),
    [
        ("list of blocks (control)", list(BLOCKS)),
        ("tuple of blocks", tuple(BLOCKS)),
    ],
    ids=["list", "tuple"],
)
def test_mark_messages_prefix_marks_any_block_sequence(label: str, content: Any) -> None:
    out = _mark_messages_prefix([{"role": "user", "content": content}])
    assert _is_marked(out[-1]["content"])


def test_a_str_still_takes_the_promote_branch() -> None:
    """Asserted for both call sites, since each has its own `str` branch.

    I built the over-broad neighbour — drop the `str`/`bytes` exclusion from
    `is_block_sequence` — expecting it to break this. It does not: all three
    call sites (`_mark_system`, `_mark_messages_prefix`, and `batch.py`'s
    content check) test `str` on an EARLIER line, so the promote branch wins by
    statement order and the neighbour turns only the predicate's own rows red.

    That is the reason to keep the exclusion, stated accurately: the predicate
    has to be correct standing alone, because a guarantee that depends on
    statement order is not a guarantee — it holds until a fourth call site is
    written by someone who did not know to check `str` first, and then a system
    prompt is marked character by character and `dict("y")` raises on the way.
    """
    marked_system = _mark_system("you are a helpful assistant")
    assert marked_system == [
        {
            "type": "text",
            "text": "you are a helpful assistant",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    out = _mark_messages_prefix([{"role": "user", "content": "hello"}])
    assert out[-1]["content"] == [
        {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}}
    ]


def test_an_empty_block_sequence_is_left_alone() -> None:
    """There is nothing to mark, and inventing a block would put a text block
    the caller never wrote into the request.
    """
    assert _mark_system([]) == []
    assert _mark_system(()) == ()
    out = _mark_messages_prefix([{"role": "user", "content": []}])
    assert out[-1]["content"] == []


# --- the shared vocabulary's own contract ---------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([{"type": "text"}], True),
        (({"type": "text"},), True),
        ([], True),
        ("hello", False),
        (b"hello", False),
        (bytearray(b"x"), False),
        ({"type": "text"}, False),
        (MappingProxyType({"a": 1}), False),
        (UserDict({"a": 1}), False),
        (None, False),
        (7, False),
        (True, False),
    ],
    ids=repr,
)
def test_is_block_sequence(value: Any, expected: bool) -> None:
    assert is_block_sequence(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("x", True),
        (7, True),
        (True, True),
        ({"type": "text"}, True),
        (MappingProxyType({"a": 1}), True),
        (UserDict({"a": 1}), True),
        (["a"], True),
        (("a",), True),
        (_ObjUsage({}), False),
        (_ObjResponse(None), False),
    ],
    ids=repr,
)
def test_is_decoded_json_value(value: Any, expected: bool) -> None:
    """The inverse question `batch.py` asks of a content *block*. A modelled
    object is the only thing that is not a decoded value, which is exactly the
    partition D-017 named.
    """
    assert is_decoded_json_value(value) is expected


def test_the_two_predicates_disagree_only_where_they_should() -> None:
    """They are not inverses and must not be read as such: a list IS a decoded
    JSON value AND IS a block sequence — that is the ordinary case at both call
    sites. The one that would be a bug is a value both predicates rejected while
    a caller iterated it anyway.
    """
    assert is_decoded_json_value([{"type": "text"}]) is True
    assert is_block_sequence([{"type": "text"}]) is True
    assert is_decoded_json_value("x") is True
    assert is_block_sequence("x") is False
