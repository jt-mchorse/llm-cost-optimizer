"""`router.py` was the third module with its own shape vocabulary (#217).

#213 argued the rule out in `batch.py`'s comment. #215 carried it to
`cache_wrapper.py` and opened with the sentence that names this file:

    `cache_wrapper.py` still said `dict` and `list`, at four sites. A rule
    stated in prose in one module is not a rule the sibling module has.

`router.py` reads nested values off the same duck-typed SDK response and still
said `dict` and `list` at six sites. After #216 it was the only module in the
package that did.

The consequence is worse than #215's, because there the harm was a wrong
*number* on a dashboard and here it is a wrong *decision*. Measured on `main`
with `EntropySignal(threshold=0.5)` against a genuinely uncertain distribution
(two near-equal tokens, entropy 0.693 nats), varying only the container and
node types:

    control: list + object nodes    entropy 0.693147   trip True
    control: list + dict nodes      entropy 0.693147   trip True
    control: direct list            entropy 0.693147   trip True
    TUPLE content/logprobs          entropy None       trip False
    UserDict nodes                  entropy None       trip False
    MappingProxy nodes              entropy None       trip False
    direct tuple logprobs           entropy None       trip False

Same uncertainty, opposite routing decision: the router keeps the cheap
model's answer on a response the signal exists to escalate.

**`trip=False` is also what a confident response produces.** No error, no log
line, nothing that distinguishes a suppressed escalation from a correctly-cheap
one. That is why the rows below assert the ENTROPY VALUE and not merely
`trip is True` — an assertion on the outcome alone cannot separate this bug
from correct operation, and would have passed against the unfixed code for any
input the signal was always going to decline.

The ABSTAIN rows are the anti-vacuous control and are load-bearing in the other
direction: this extractor owes four defensive abstains (#94, #95, #106, #140)
plus the `str` exclusion, and a fix written too broadly satisfies every row
above while breaking every one of them.
"""

from __future__ import annotations

import math
from collections import UserDict
from types import MappingProxyType, SimpleNamespace
from typing import Any

import pytest

from cost_optimizer.router import (
    EntropySignal,
    _extract_first_token_logprobs,
    _extract_text,
    _read_field,
)
from cost_optimizer.shapes import is_block_sequence, is_item_sequence

# Two near-equal tokens. Entropy = ln(2) = 0.6931... nats, comfortably over the
# 0.5 threshold, so a working signal trips and a suppressed one does not.
LOGPROBS = [-0.69, -0.69]
EXPECTED_ENTROPY = math.log(2)
THRESHOLD = 0.5


def _obj(d: dict[str, Any]) -> Any:
    return SimpleNamespace(**d)


def _userdict(d: dict[str, Any]) -> Any:
    u: UserDict[str, Any] = UserDict()
    u.update(d)
    return u


def _mappingproxy(d: dict[str, Any]) -> Any:
    return MappingProxyType(dict(d))


def _nested(container: Any, node: Any) -> Any:
    """`response.content[0].logprobs[0].top_logprobs[i].logprob`, built with
    *container* at all three sequence levels and *node* at both mapping levels.
    """
    entries = container([node({"logprob": lp}) for lp in LOGPROBS])
    top = node({"top_logprobs": entries})
    return SimpleNamespace(content=container([SimpleNamespace(logprobs=container([top]))]))


# (label, response). Every row here carries the SAME distribution and must
# therefore produce the same entropy and the same trip.
ESCALATES: list[tuple[str, Any]] = [
    ("control: list containers, object nodes", _nested(list, _obj)),
    ("control: list containers, dict nodes", _nested(list, dict)),
    ("control: direct list", SimpleNamespace(first_token_logprobs=list(LOGPROBS))),
    ("tuple containers", _nested(tuple, _obj)),
    ("UserDict nodes", _nested(list, _userdict)),
    ("MappingProxyType nodes", _nested(list, _mappingproxy)),
    ("tuple containers AND UserDict nodes", _nested(tuple, _userdict)),
    ("direct tuple", SimpleNamespace(first_token_logprobs=tuple(LOGPROBS))),
]

# (label, response, why). Each must still abstain — value None, trip False.
ABSTAINS: list[tuple[str, Any, str]] = [
    (
        "str content is not iterated into characters",
        SimpleNamespace(content="hello"),
        "the str exclusion the shared predicate carries",
    ),
    (
        "str first_token_logprobs",
        SimpleNamespace(first_token_logprobs="abc"),
        "same exclusion on the direct path",
    ),
    (
        "bytes first_token_logprobs",
        SimpleNamespace(first_token_logprobs=b"abc"),
        # THE row the str/bytes/Mapping exclusion actually saves. Measured:
        # dropping the exclusion turns exactly this one red and nothing else.
        # Iterating a str or a Mapping yields strings, so `float("a")` raises
        # and the #140 abstain catches them downstream by accident. Iterating
        # bytes yields INTS — float(97) is 97.0 and isfinite is happy — so a
        # bytes distribution is measured as byte values with nothing to object
        # to. #215 called the exclusion "redundant with statement order"; in
        # this module it is not redundant at all, for exactly one member.
        "iterating bytes yields ints, which float() accepts",
    ),
    (
        "Mapping first_token_logprobs is not a sequence of logprobs",
        SimpleNamespace(first_token_logprobs={"a": -0.69}),
        "a Mapping iterates into its keys",
    ),
    (
        "None logprob element (#94, #106)",
        SimpleNamespace(first_token_logprobs=[None, -0.69]),
        "malformed/truncated SDK distribution",
    ),
    (
        "non-numeric logprob element (#140)",
        SimpleNamespace(first_token_logprobs=["oops", -0.69]),
        "string label off a BYO distribution",
    ),
    (
        "non-finite logprob element (#95)",
        SimpleNamespace(first_token_logprobs=[float("nan"), -0.69]),
        "NaN slips _shannon_entropy_nats' total <= 0 guard",
    ),
    (
        "nested None logprob still abstains",
        SimpleNamespace(
            content=[SimpleNamespace(logprobs=[_obj({"top_logprobs": [_obj({"logprob": None})]})])]
        ),
        "the nested path's own #94 abstain",
    ),
    ("no logprobs at all", SimpleNamespace(), "nothing to read"),
    ("empty direct list", SimpleNamespace(first_token_logprobs=[]), "len 0 reading"),
]


@pytest.mark.parametrize(("label", "response"), ESCALATES, ids=[r[0] for r in ESCALATES])
def test_the_same_distribution_escalates_whatever_container_carries_it(
    label: str, response: Any
) -> None:
    reading = EntropySignal(threshold=THRESHOLD).measure(response)
    # The VALUE, not just the trip. `trip is True` alone would be satisfied by
    # any signal that happened to fire, and `trip is False` — the bug — is
    # exactly what a confident response produces.
    assert reading.value == pytest.approx(EXPECTED_ENTROPY), (
        f"{label}: entropy {reading.value!r}, expected {EXPECTED_ENTROPY!r}"
    )
    assert reading.trip is True, f"{label}: escalation suppressed"


@pytest.mark.parametrize(("label", "response"), ESCALATES, ids=[r[0] for r in ESCALATES])
def test_the_extractor_returns_the_identical_float_list(label: str, response: Any) -> None:
    """One layer below `measure`, so a failure names the extractor rather than
    the entropy arithmetic."""
    got = _extract_first_token_logprobs(response)
    assert got is not None, f"{label}: extractor read the distribution as absent"
    assert got == pytest.approx(LOGPROBS), f"{label}: extracted {got!r}"


@pytest.mark.parametrize(("label", "response", "why"), ABSTAINS, ids=[r[0] for r in ABSTAINS])
def test_every_defensive_abstain_survives_the_widening(label: str, response: Any, why: str) -> None:
    reading = EntropySignal(threshold=THRESHOLD).measure(response)
    assert reading.value is None, f"{label}: measured {reading.value!r} ({why})"
    assert reading.trip is False, f"{label}: tripped on data it must decline ({why})"


def test_read_field_reads_a_mapping_not_only_a_dict() -> None:
    """#69's reason survives; its partition does not.

    The rule is "never call `.get` on something that has none". `Mapping` is
    the protocol that guarantees one; `dict` is a subset of it that leaves out
    `UserDict` — not a `dict` subclass, and the ordinary base for a gateway or
    proxy client's response wrapper.
    """
    ud: UserDict[str, Any] = UserDict()
    ud.update({"logprob": -0.5})
    assert _read_field(ud, "logprob") == -0.5
    assert _read_field(MappingProxyType({"logprob": -0.5}), "logprob") == -0.5
    assert _read_field({"logprob": -0.5}, "logprob") == -0.5
    assert _read_field(SimpleNamespace(logprob=-0.5), "logprob") == -0.5

    # #69's actual crash: an object with neither the attribute nor a `.get`.
    # Widening must not reintroduce it.
    assert _read_field(object(), "logprob") is None
    assert _read_field(object(), "logprob", default=7) == 7
    # A str has no `.get` either, and is a Sequence — the other way in.
    assert _read_field("nope", "logprob") is None


@pytest.mark.parametrize(
    ("label", "content", "expected"),
    [
        ("list of blocks", [SimpleNamespace(type="text", text="hello")], "hello"),
        ("tuple of blocks", (SimpleNamespace(type="text", text="hello"),), "hello"),
        ("str content is not iterated", "raw", ""),
        ("bytes content is not iterated", b"raw", ""),
        ("mapping content is not iterated", {"text": "raw"}, ""),
        ("block with text=None is filtered", [SimpleNamespace(type="text", text=None)], ""),
        ("no content", None, ""),
    ],
)
def test_extract_text_over_the_same_container_table(
    label: str, content: Any, expected: str
) -> None:
    """`_extract_text` feeds `JudgeConfidenceSignal`, a different signal on the
    same response, and had the same sixth site."""
    assert _extract_text(SimpleNamespace(content=content)) == expected, label


def test_router_holds_no_third_copy_of_the_vocabulary() -> None:
    """Structural, because a copy passes every behavioural row above.

    #215's own finding was that a second correct copy is what left the first
    half open. A `router.py` that re-spelled `isinstance(x, Sequence) and not
    isinstance(x, (str, bytes, Mapping))` inline would satisfy every test in
    this file and be the exact shape the fix exists to remove.
    """
    import ast
    import inspect

    from cost_optimizer import router as router_module

    source = inspect.getsource(router_module)
    tree = ast.parse(source)

    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "isinstance"):
            continue
        if len(node.args) != 2:
            continue
        names: list[str] = []
        target = node.args[1]
        elts = target.elts if isinstance(target, ast.Tuple) else [target]
        for e in elts:
            if isinstance(e, ast.Name):
                names.append(e.id)
            elif isinstance(e, ast.Attribute):
                names.append(e.attr)
        for n in names:
            if n in {"list", "dict", "Sequence", "Mapping"}:
                bad.append(f"line {node.lineno}: isinstance(..., {n})")

    # `Mapping` in `_read_field` is the one legitimate remaining shape test —
    # it is a mapping-key read, not a sequence-of-items question, and it has no
    # shared predicate because no other module asks it. Everything else must go
    # through `shapes`.
    allowed = [b for b in bad if b.endswith("Mapping)")]
    assert len(allowed) == 1, f"expected exactly one Mapping test in router.py, got {allowed}"
    assert [b for b in bad if b not in allowed] == [], (
        "router.py re-spelled the shape vocabulary inline instead of importing "
        f"it from cost_optimizer.shapes: {[b for b in bad if b not in allowed]}"
    )
    assert "is_item_sequence" in source, "router.py no longer imports the shared predicate"


def test_is_block_sequence_is_the_same_function_not_a_second_copy() -> None:
    """Two names, one implementation. A behavioural suite cannot tell one
    definition from two identical ones, so assert the delegation structurally.
    """
    import ast
    import inspect

    from cost_optimizer import shapes as shapes_module

    body = ast.parse(inspect.getsource(shapes_module))
    fn = next(
        n
        for n in ast.walk(body)
        if isinstance(n, ast.FunctionDef) and n.name == "is_block_sequence"
    )
    statements = [s for s in fn.body if not isinstance(s, ast.Expr)]
    assert len(statements) == 1, "is_block_sequence grew a body of its own"
    ret = statements[0]
    assert isinstance(ret, ast.Return)
    assert isinstance(ret.value, ast.Call)
    assert isinstance(ret.value.func, ast.Name)
    assert ret.value.func.id == "is_item_sequence", (
        "is_block_sequence stopped delegating — that is the second correct copy "
        "#215 identified as the thing that left the first half of this open"
    )

    # And they agree, over the values the two callers actually disagree about.
    for value in [(1,), [1], "x", b"x", {"a": 1}, MappingProxyType({"a": 1}), 1, None]:
        assert is_item_sequence(value) == is_block_sequence(value), value
