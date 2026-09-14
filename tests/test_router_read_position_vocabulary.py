"""Every duck-typed read in `router.py` goes through `_read_field` (#219).

#217 swept one rule across the nested read and left the other where it was. Its
own comment names the sweep it ran -- "Same widening as the direct path above
(#217), **at all three levels of the nest**" -- and that is the *container* rule,
`is_item_sequence` in place of `isinstance(x, list)`. The *field-read* rule,
`_read_field` in place of a bare `getattr`, was at one level out of three: 9 of
`router.py`'s 11 duck-typed reads bypassed it.

Measured on `986f8a0` with a differential probe that turns **one read position at
a time** into a plain `dict`, everything else held as objects, against a
genuinely uncertain distribution (two near-equal tokens, 0.693 nats)::

    position made a dict                 logprobs found    entropy   _extract_text
    (control) all objects                           yes   0.693147         'Paris'
    1. the RESPONSE                                  NO       None              ''
    2. the CONTENT BLOCK                             NO       None              ''
    3. the logprob/top_logprobs NODES               yes   0.693147         'Paris'

Row 3 is the only position `_read_field` covered and the only one that worked.
Same uncertainty, opposite routing decision -- and `trip=False` is also what a
*confident* response produces, so nothing separated a suppressed escalation from
a correctly-cheap one. That is why every row below asserts the ENTROPY VALUE and
not only the trip.

A plain `dict` is enough. #217 needed a `tuple`, a `UserDict` or a
`MappingProxyType` to show its gap; this fires on the most ordinary shape there
is -- a `dict` content block is what the HTTP API returns on the wire and what
`model_dump()` / `json.loads(body)` produce.

**Why #217's control row did not catch it.** #217's table has a green row reading
`control: list containers, dict nodes -> entropy 0.693147 trip True`. It is
labelled "dict nodes" and makes only the *node* position a dict -- the one
position already covered. A control named after a shape, exercising one of the
three places that shape occurs, is how this stayed invisible. The table here is
indexed by POSITION for that reason.

**One harm was hidden behind another.** With a Mapping response, `_extract_text`
returned `""`, `JudgeConfidenceSignal.measure` took its empty-text abstain, and
the judge was never called -- so the lost `prompt` at line 431 was latent. Fixing
the text read alone makes it live, and then the judge receives `""` as its prompt
and returns a score anyway: a *wrong measurement* driving a routing decision,
which is worse than the abstain it replaces. The sites move together for that
reason, and `test_a_mapping_response_still_carries_its_prompt_to_the_judge` is the
arm that would have gone red on the one-site-at-a-time fix.
"""

from __future__ import annotations

import ast
import inspect
import math
import pathlib
from collections import OrderedDict, UserDict
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from cost_optimizer import router
from cost_optimizer.router import (
    EntropySignal,
    JudgeConfidenceSignal,
    _extract_first_token_logprobs,
    _extract_text,
    _read_field,
)

# Two near-equal tokens: entropy = ln(2) = 0.693... nats, over the 0.5 threshold,
# so a working signal trips and a suppressed one does not. Same constants as
# `tests/test_router_shape_vocabulary.py` so the two files agree on the fixture.
LOGPROB = -0.69
EXPECTED_ENTROPY = math.log(2)
THRESHOLD = 0.5
TEXT = "the capital is Paris"


# --- the four node flavours a client can hand back --------------------------
#
# `object` is the control. The other three are the shapes #215 and #217 between
# them established as real: a `dict` (the wire shape), a `UserDict` (a
# gateway/proxy wrapper's ordinary base, and not a `dict` subclass), and a
# `MappingProxyType` (what a frozen response exposes).
FLAVOURS: dict[str, Any] = {
    "object": lambda **kw: SimpleNamespace(**kw),
    "dict": lambda **kw: dict(kw),
    "UserDict": lambda **kw: UserDict(kw),
    "mappingproxy": lambda **kw: MappingProxyType(dict(kw)),
    "OrderedDict": lambda **kw: OrderedDict(kw),
}

#: The three positions in the nested read, which is the axis #217's control row
#: collapsed. Named so a failure says *where*.
POSITIONS = ("response", "content_block", "nodes")


def _nested(response_f: Any, block_f: Any, node_f: Any) -> Any:
    top = [node_f(logprob=LOGPROB), node_f(logprob=LOGPROB)]
    block = block_f(type="text", text=TEXT, logprobs=[node_f(top_logprobs=top)])
    return response_f(content=[block])


def _factories(position: str, flavour: str) -> tuple[Any, Any, Any]:
    """All-objects except *position*, which becomes *flavour*."""
    obj = FLAVOURS["object"]
    chosen = FLAVOURS[flavour]
    return (
        chosen if position == "response" else obj,
        chosen if position == "content_block" else obj,
        chosen if position == "nodes" else obj,
    )


@pytest.mark.parametrize("flavour", [f for f in FLAVOURS if f != "object"])
@pytest.mark.parametrize("position", POSITIONS)
def test_entropy_is_measured_at_every_read_position(position: str, flavour: str) -> None:
    """The value, not the trip: `trip=False` is also what a confident response gives."""
    response = _nested(*_factories(position, flavour))
    got = _extract_first_token_logprobs(response)
    assert got is not None, (
        f"a {flavour} at the {position} position read as absent, which abstains the "
        f"signal to trip=False -- indistinguishable from a confident response"
    )
    reading = EntropySignal(threshold=THRESHOLD).measure(response)
    assert reading.value == pytest.approx(EXPECTED_ENTROPY, abs=1e-3), (
        f"a {flavour} at the {position} position measured {reading.value!r} instead "
        f"of {EXPECTED_ENTROPY:.6f}"
    )
    assert reading.trip is True


@pytest.mark.parametrize("flavour", [f for f in FLAVOURS if f != "object"])
@pytest.mark.parametrize("position", ["response", "content_block"])
def test_text_is_extracted_at_every_read_position(position: str, flavour: str) -> None:
    response_f, block_f, _ = _factories(position, flavour)
    response = response_f(content=[block_f(type="text", text=TEXT)])
    assert _extract_text(response) == TEXT, (
        f"a {flavour} at the {position} position lost the assistant text, which "
        f"abstains JudgeConfidenceSignal through its empty-text guard"
    )


def test_the_control_is_green_and_the_table_spans_all_three_positions() -> None:
    """Anti-vacuous: the all-objects control must pass, and the axis must be the
    POSITION axis rather than #217's collapsed one."""
    response = _nested(*(FLAVOURS["object"],) * 3)
    assert _extract_first_token_logprobs(response) is not None
    assert EntropySignal(threshold=THRESHOLD).measure(response).value == pytest.approx(
        EXPECTED_ENTROPY, abs=1e-3
    )
    # Each position must actually be a *different* factory slot, or the table is
    # three copies of one row.
    slots = {p: _factories(p, "dict") for p in POSITIONS}
    assert len({tuple(id(f) for f in v) for v in slots.values()}) == len(POSITIONS)


# --- JudgeConfidenceSignal: the same gap, twice -----------------------------


class _RecordingJudge:
    """Returns a fixed low score, so `trip=True` (escalate) is always correct."""

    def __init__(self, verdict_f: Any, score: float = 0.2) -> None:
        self._verdict_f = verdict_f
        self._score = score
        self.seen_prompt: Any = "<never called>"

    def score(self, prompt: str, text: str, rubric: str | None = None) -> Any:
        self.seen_prompt = prompt
        return self._verdict_f(score=self._score)


def _signal(judge: Any) -> JudgeConfidenceSignal:
    return JudgeConfidenceSignal(judge=judge, threshold=0.5, rubric="is this confident?")


@pytest.mark.parametrize("verdict_flavour", list(FLAVOURS))
@pytest.mark.parametrize("response_flavour", list(FLAVOURS))
def test_judge_confidence_reads_the_score_off_any_verdict_shape(
    response_flavour: str, verdict_flavour: str
) -> None:
    """`.score` comes off a BYO duck-typed judge, which line 445's own comment
    already argued is "reachable, not hypothetical" -- for the *value* domain. A
    `{"score": 0.2}` verdict is the obvious thing a dict-based judge returns."""
    judge = _RecordingJudge(FLAVOURS[verdict_flavour])
    block = FLAVOURS["object"](type="text", text=TEXT)
    response = FLAVOURS[response_flavour](content=[block], prompt="what is the capital?")
    reading = _signal(judge).measure(response)
    assert reading.value == pytest.approx(0.2), (
        f"a {verdict_flavour} verdict off a {response_flavour} response measured "
        f"{reading.value!r}; the judge returned 0.2, so this suppresses an escalation"
    )
    assert reading.trip is True


@pytest.mark.parametrize("response_flavour", list(FLAVOURS))
def test_a_mapping_response_still_carries_its_prompt_to_the_judge(
    response_flavour: str,
) -> None:
    """The arm a one-site-at-a-time fix fails.

    Before #219 a Mapping response lost its text first, so `measure` abstained
    before the judge ran and the lost `prompt` was invisible. Fixing the text read
    alone makes the judge run with `""` as its prompt -- a wrong measurement
    rather than an abstain, which is strictly worse. This pins the pass-through,
    not merely that a score came back.
    """
    judge = _RecordingJudge(FLAVOURS["object"])
    block = FLAVOURS["object"](type="text", text=TEXT)
    response = FLAVOURS[response_flavour](content=[block], prompt="what is the capital?")
    _signal(judge).measure(response)
    assert judge.seen_prompt == "what is the capital?", (
        f"a {response_flavour} response handed the judge {judge.seen_prompt!r}; an "
        f"empty prompt still produces a score, so the routing decision is wrong "
        f"rather than abstained"
    )


# --- the defensive abstains, asserted unchanged -----------------------------


@pytest.mark.parametrize("flavour", list(FLAVOURS))
@pytest.mark.parametrize(
    ("label", "bad"),
    [
        pytest.param("None element (#94/#106)", [LOGPROB, None], id="none-element"),
        pytest.param("non-numeric (#140)", [LOGPROB, "high"], id="non-numeric"),
        pytest.param("non-finite (#95)", [LOGPROB, float("nan")], id="non-finite"),
        pytest.param("+inf (#95)", [LOGPROB, float("inf")], id="pos-inf"),
    ],
)
def test_every_defensive_abstain_survives_at_every_flavour(
    flavour: str, label: str, bad: list[Any]
) -> None:
    """Widening the *shape* domain must not widen the *value* domain."""
    f = FLAVOURS[flavour]
    top = [f(logprob=v) for v in bad]
    block = f(type="text", text=TEXT, logprobs=[f(top_logprobs=top)])
    response = f(content=[block])
    assert _extract_first_token_logprobs(response) is None, (
        f"{label} must abstain at the {flavour} flavour, not be measured"
    )


@pytest.mark.parametrize("flavour", list(FLAVOURS))
def test_the_str_and_bytes_exclusions_survive(flavour: str) -> None:
    """#218 measured the `bytes` row as the one the exclusion actually saves:
    iterating `bytes` yields ints, so `float(97)` is `97.0` and the non-numeric
    abstain never fires. Pin both halves at every flavour."""
    f = FLAVOURS[flavour]
    for bad in ("abc", b"abc", bytearray(b"abc")):
        assert _extract_first_token_logprobs(f(first_token_logprobs=bad)) is None, (
            f"a {type(bad).__name__} first_token_logprobs must not be measured "
            f"as a distribution (flavour={flavour})"
        )


# --- the discovered-population locks ----------------------------------------


def _router_getattr_sites() -> list[str]:
    """Every `getattr(...)` call in `router.py` outside `_read_field` itself.

    Over the AST, so the prose that has to *name* `getattr` in order to explain
    why it is wrong (the `_read_field` docstring, and the `#140` comment quoting
    the old `float(getattr(...) or 0.0)`) is not a match. A line regex reads an
    explanation as an instance; that trap cost a cycle in the sibling fix for
    llm-eval-harness#240 the same night.
    """
    src = pathlib.Path(inspect.getfile(router)).read_text(encoding="utf-8")
    tree = ast.parse(src)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_read_field":
            allowed = {getattr(n, "lineno", -1) for n in ast.walk(node)}
    sites = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and node.lineno not in allowed
        ):
            sites.append(f"router.py:{node.lineno}: {ast.unparse(node)}")
    return sites


def test_no_duck_typed_read_in_router_bypasses_read_field() -> None:
    """The rule reads in one place, and a read site added later cannot join the gap.

    The population is discovered, not listed. #217 hand-listed the three levels of
    the *container* rule and got them all; the field-read rule was never counted
    at all, and a hand list of the sites it did cover would have said "2, and
    that is all of them".
    """
    assert _router_getattr_sites() == [], (
        "these reads bypass `_read_field`, so a Mapping-shaped node reads as "
        "absent and silently abstains the routing signal (#219):\n"
        + "\n".join(f"  - {s}" for s in _router_getattr_sites())
    )


def test_the_getattr_lock_can_fail() -> None:
    """Anti-vacuous: the matcher must find the shape, and must not find the prose."""
    defect = ast.parse('def _extract_text(r):\n    return getattr(r, "text", None)\n')
    found = [
        ast.unparse(n)
        for n in ast.walk(defect)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr"
    ]
    assert found == ["getattr(r, 'text', None)"], found
    prose = ast.parse(
        'def f():\n    """A bare getattr(...) or obj.get(...) raised."""\n    return 1\n'
    )
    assert [n for n in ast.walk(prose) if isinstance(n, ast.Call)] == []
    # And the exemption must be real: `_read_field`'s own `getattr` is excluded,
    # so the lock cannot be green merely because it exempts everything.
    assert "getattr" in inspect.getsource(_read_field)


def _read_field_names() -> set[str]:
    """The field-name literals passed to `_read_field`, from the call sites."""
    src = pathlib.Path(inspect.getfile(router)).read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_read_field"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            names.add(node.args[1].value)
    return names


def test_no_read_field_name_collides_with_a_mapping_attribute() -> None:
    """`_read_field` reads the attribute first and the Mapping key second (#69).

    For a `Mapping`, a field whose name collides with a `Mapping` attribute
    resolves to the **bound method** instead of the value --
    `_read_field({"items": [...]}, "items")` returns `dict.items`. No current
    name collides; this is what stops the next one being added silently. Pinning
    the collision rather than reordering the lookup keeps #69's ordering, whose
    reason (never call `.get` on an object that has none) is still correct.
    """
    names = _read_field_names()
    assert names, "expected to discover the field names from the call sites"
    collisions = sorted(
        f"{n} (collides on {t.__name__})"
        for n in names
        for t in (dict, UserDict, MappingProxyType, OrderedDict)
        if hasattr(t, n) or hasattr(t({}) if t is UserDict else {}, n)
    )
    assert collisions == [], (
        "these field names resolve to a Mapping's own attribute before its key, "
        "so `_read_field` would return a bound method instead of the value:\n"
        + "\n".join(f"  - {c}" for c in collisions)
    )


def test_the_collision_lock_can_fail() -> None:
    """Anti-vacuous, and it also documents the hazard concretely."""
    # A `UserDict` has the same hazard as a `dict`: `.items` resolves first.
    assert callable(_read_field(UserDict({"items": [1, 2]}), "items"))
    assert callable(_read_field({"items": [1, 2]}, "items")), (
        "a Mapping key named like a Mapping attribute resolves to the method; "
        "this is the hazard `test_no_read_field_name_collides_with_a_mapping_attribute` pins"
    )
    # And a non-colliding name reads the key, which is the ordinary case.
    assert _read_field({"text": "x"}, "text") == "x"


def test_the_discovered_names_cover_the_known_sites() -> None:
    """The name collector must not be silently empty-ish: #219 routes nine reads,
    over seven distinct field names."""
    assert _read_field_names() == {
        "first_token_logprobs",
        "content",
        "logprobs",
        "top_logprobs",
        "logprob",
        "prompt",
        "score",
        "text",
        "type",
    }
