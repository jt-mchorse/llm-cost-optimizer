"""The two shipped backends must not serve different payloads (#192).

`SemanticCache.put` took `payload: Any` and validated nothing about it, while
the two shipped `Storage` implementations disagreed about what that meant:
`InMemoryStorage` `deepcopy`s the payload (exact types preserved),
`RedisStorage` `json.dumps` it. Six of the twelve shapes in `_TABLE` below
diverged when run through the identical public API.

The harm split in two. `tuple`/int-keyed-dict payloads changed *type* silently
-- `payload[1]` works on the default backend and raises `KeyError: 1` on Redis
for an entry the cache reported as a hit. `set`/`bytes`/`datetime` payloads
raised `TypeError` from inside `RedisStorage.put`, i.e. a request failed
*because it tried to cache its own result*, and only after the operator made
the backend swap `RedisStorage`'s own docstring recommends.

This module is a *differential* test, not a unit test of the validator: every
row is driven through both backends and the two verdicts are compared. A rule
that lived in only one implementation would show up here as a disagreement, and
so would a future third backend that serialized differently.
"""

from __future__ import annotations

import collections
import datetime as dt
import enum
import json
import math
from collections.abc import Callable
from typing import Any

import pytest

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import (  # noqa: E402
    HashEmbedder,
    InMemoryStorage,
    RedisStorage,
    SemanticCache,
)

PROMPT = "what is the capital of france"
MODEL = "m"


def _cache(backend: str) -> SemanticCache:
    storage = InMemoryStorage() if backend == "mem" else RedisStorage(client=fakeredis.FakeRedis())
    return SemanticCache(embedder=HashEmbedder(), storage=storage)


def _self_dict() -> dict[str, Any]:
    d: dict[str, Any] = {"answer": "Paris"}
    d["self"] = d
    return d


def _self_list() -> list[Any]:
    xs: list[Any] = [1]
    xs.append(xs)
    return xs


_SHARED = {"k": 1}


# --- #207 subclass shapes ---------------------------------------------------
#
# Every one of these is an `isinstance` of an accepted JSON type and a `type()`
# of something else. They are ordinary library and stdlib shapes, not
# contrivances: an `IntEnum` status, a `defaultdict` accumulator and a `Counter`
# are exactly what ends up in a response payload someone decides to cache.


class _Status(enum.IntEnum):
    OK = 1


class _Tier(enum.StrEnum):
    GOLD = "gold"


class _MyDict(dict): ...


class _MyList(list): ...


class _MyStr(str): ...


class _MyInt(int): ...


class _MyFloat(float): ...


_Point = collections.namedtuple("_Point", "x y")


def _default_dict() -> dict[str, Any]:
    answers: collections.defaultdict[str, list[str]] = collections.defaultdict(list)
    answers["q1"].append("Paris")
    return {"answers": answers}


# (label, payload, accepted?) -- the probe that found the defect, kept verbatim,
# plus the three reference-graph rows added by #203.
_TABLE: list[tuple[str, Any, bool]] = [
    ("str", "Paris", True),
    ("dict of str", {"answer": "Paris"}, True),
    ("None", None, True),
    ("bool", True, True),
    ("int", 7, True),
    ("nested lists and dicts", {"a": [1, {"b": ["c", None, False]}]}, True),
    ("NaN in dict", {"score": float("nan")}, True),
    ("Infinity in dict", {"score": float("inf")}, True),
    ("dict with tuple value", {"citations": ("a", "b")}, False),
    ("bare tuple", ("a", "b"), False),
    ("nested tuple in list", {"xs": [("a", 1)]}, False),
    ("int-keyed dict", {1: "one", 2: "two"}, False),
    ("set", {"a", "b"}, False),
    ("bytes", b"Paris", False),
    ("datetime", dt.datetime(2026, 1, 1), False),
    # #203. A cycle is the shape the two backends disagree about most starkly --
    # `InMemoryStorage.put` stores it intact because `copy.deepcopy` memoizes,
    # while `RedisStorage.put` raises `Circular reference detected` from
    # `json.dumps` -- and the validator could not reach a verdict on it at all:
    # the walk ran to 5.7 GB resident. Rejecting at the seam makes both roads
    # agree, which is what puts these rows in *this* table rather than only in
    # tests/test_semantic_cache_payload_cycles.py.
    ("self-referential dict", _self_dict(), False),
    ("self-referential list", _self_list(), False),
    # The negative half of the same rule, and the one a "reject any id seen
    # twice" fix would break: `json.dumps` emits a shared node once per path and
    # both backends round-trip it.
    ("shared acyclic reference", {"a": _SHARED, "b": _SHARED}, True),
    # #207. The rule is "is exactly a JSON type"; the walk asked `isinstance`,
    # which answers "is-a", so every subclass of an accepted type passed while
    # `json.dumps` flattened it to the base. Ten of eleven measured shapes were
    # accepted and diverged. These rows are here rather than in a validator unit
    # test for the same reason the originals are: the claim is about what the
    # two backends *serve*, so it has to be driven through both.
    #
    # `namedtuple` is in the list precisely because it was already rejected --
    # by the accident of subclassing `tuple`, which the old table happened to
    # cover. It is the control that keeps "we got this class already" honest.
    ("IntEnum value", {"status": _Status.OK}, False),
    ("StrEnum value", {"tier": _Tier.GOLD}, False),
    ("bare IntEnum", _Status.OK, False),
    ("defaultdict", _default_dict(), False),
    ("Counter", {"c": collections.Counter("aab")}, False),
    ("OrderedDict", {"od": collections.OrderedDict([("b", 1), ("a", 2)])}, False),
    ("dict subclass", {"d": _MyDict(a=1)}, False),
    ("list subclass", {"xs": _MyList([1, 2])}, False),
    ("str subclass", {"s": _MyStr("x")}, False),
    ("int subclass", {"n": _MyInt(3)}, False),
    ("float subclass", {"f": _MyFloat(1.5)}, False),
    ("namedtuple", {"p": _Point(1, 2)}, False),
    ("str-subclass dict key", {_MyStr("a"): 1}, False),
]

_ACCEPTED = [(lbl, pl) for lbl, pl, ok in _TABLE if ok]
_REJECTED = [(lbl, pl) for lbl, pl, ok in _TABLE if not ok]


def test_the_table_covers_both_verdicts() -> None:
    """Anti-vacuous guard: a table that drifted to all-accept or all-reject would
    make every parametrized case below pass while proving nothing."""
    assert len(_ACCEPTED) >= 6
    assert len(_REJECTED) >= 6


def _equalish(a: Any, b: Any) -> bool:
    """Structural equality that treats two NaNs as equal.

    `nan != nan`, and two of the accepted rows carry one deliberately -- they
    round-trip identically on both backends and are explicitly *not* part of
    this defect, which is why `_validate_payload` is a structural walk rather
    than a `json.loads(json.dumps(x)) == x` check.
    """
    if isinstance(a, float) and isinstance(b, float):
        return (math.isnan(a) and math.isnan(b)) or a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_equalish(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_equalish(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


@pytest.mark.parametrize(("label", "payload"), _ACCEPTED, ids=[r[0] for r in _ACCEPTED])
def test_accepted_payloads_are_served_identically_by_both_backends(
    label: str, payload: Any
) -> None:
    """No `bounded_work` here, unlike the rejected driver below: an *accepted*
    payload is acyclic by construction, so it is not a candidate for the
    non-termination this table's cyclic rows guard against."""
    served = {}
    for backend in ("mem", "redis"):
        cache = _cache(backend)
        cache.put(prompt=PROMPT, payload=payload, model=MODEL)
        hit = cache.lookup(prompt=PROMPT, model=MODEL)
        assert hit.hit, f"{label} did not hit on {backend}"
        served[backend] = hit.payload

    assert _equalish(served["mem"], served["redis"]), (
        f"{label}: in-memory served {served['mem']!r} "
        f"({type(served['mem']).__name__}) but redis served {served['redis']!r} "
        f"({type(served['redis']).__name__})"
    )
    # Type identity, not just value equality: `('a','b') == ['a','b']` is False
    # in Python, but a laxer comparison would have let the original defect
    # through on a row like `{"xs": [("a", 1)]}` if the walk ever stopped
    # descending.
    assert type(served["mem"]) is type(served["redis"])


@pytest.mark.parametrize(("label", "payload"), _REJECTED, ids=[r[0] for r in _REJECTED])
def test_rejected_payloads_fail_identically_on_both_backends(
    label: str, payload: Any, bounded_work: Callable[..., Any]
) -> None:
    """Same exception type, same message, before any backend is touched."""
    messages = {}
    for backend in ("mem", "redis"):
        cache = _cache(backend)
        # Bounded because two of these rows are cyclic (#203) and the walk that
        # rejects them fails by *not returning*, not by returning wrongly --
        # "did it raise" and "did it finish" are different questions. Every
        # rejected row short-circuits inside `_validate_payload`, before the
        # embedder or either backend is touched (which is the next assertion
        # below), so the cheap default limit is the right one here.
        with (
            bounded_work(what=f"{label} through {backend}"),
            pytest.raises(ValueError, match="JSON round-trip") as exc,
        ):
            cache.put(prompt=PROMPT, payload=payload, model=MODEL)
        messages[backend] = str(exc.value)
        # Nothing was stored on the way to the error.
        assert len(cache.storage) == 0, f"{label} left a record behind on {backend}"

    assert messages["mem"] == messages["redis"], label
    assert "JSON round-trip" in messages["mem"]


def test_rejection_names_the_path_not_just_the_type() -> None:
    """A bare "payload is not serializable" is unactionable on a nested value."""
    cache = _cache("mem")
    with pytest.raises(ValueError, match=r"payload\['xs'\]\[0\]") as exc:
        cache.put(prompt=PROMPT, payload={"xs": [("a", 1)]}, model=MODEL)
    assert "payload['xs'][0]" in str(exc.value)

    with pytest.raises(ValueError, match="non-string key") as exc:
        cache.put(prompt=PROMPT, payload={"meta": {1: "one"}}, model=MODEL)
    assert "payload['meta']" in str(exc.value)
    assert "non-string key" in str(exc.value)


def test_a_deep_payload_raises_valueerror_not_recursionerror() -> None:
    """The walk is iterative on purpose: `payload` is caller-supplied, and a
    `RecursionError` is not the `ValueError` this seam contracts to raise."""
    deep: Any = ("leaf",)
    for _ in range(5_000):
        deep = [deep]
    cache = _cache("mem")
    with pytest.raises(ValueError, match="JSON round-trip"):
        cache.put(prompt=PROMPT, payload=deep, model=MODEL)


def test_validation_runs_before_the_embedder() -> None:
    """`_validate_payload` is placed ahead of `embedder.embed` so a bad payload
    costs nothing -- for a BYO embedder that is a network call."""
    calls: list[str] = []

    class CountingEmbedder:
        def embed(self, text: str) -> list[float]:
            calls.append(text)
            return HashEmbedder().embed(text)

    cache = SemanticCache(embedder=CountingEmbedder(), storage=InMemoryStorage())
    with pytest.raises(ValueError, match="JSON round-trip"):
        cache.put(prompt=PROMPT, payload={"a", "b"}, model=MODEL)
    assert calls == []

    cache.put(prompt=PROMPT, payload={"ok": 1}, model=MODEL)
    assert len(calls) == 1


# --- #207: the rejection message must name the mechanism that actually fires --
#
# Two opposite mechanisms reach the same `raise`. `json.dumps` **converts** a
# `tuple`, an `IntEnum`, a `defaultdict` and every other subclass of an
# encodable base to its base type; it **raises** `TypeError` for a `set`, a
# `bytes` or a `datetime`. The single message that stood here claimed the
# `TypeError` for all of them -- so the rows whose harm is the silent one, the
# one this module's docstring calls worse, were the rows being told they would
# fail loudly.
#
# `_unrepresentable_message` decides the mechanism from json's own type
# dispatch rather than by calling `json.dumps` on a caller-supplied payload
# (which recurses, and would turn a rejection into a `RecursionError` on the
# very deep input the iterative walk exists to survive). These tests are what
# keeps that cheap rule honest: they run the real `json.dumps`.

_CONVERTS = "encodes it as a plain"
_RAISES = "is not JSON serializable"


def _real_json_mechanism(payload: Any) -> str:
    """What `json.dumps` actually does with this payload: converts, or refuses."""
    try:
        json.dumps(payload)
    except TypeError:
        return "raises"
    except ValueError:
        return "circular"
    return "converts"


@pytest.mark.parametrize(("label", "payload"), _REJECTED, ids=[r[0] for r in _REJECTED])
def test_rejection_message_matches_what_json_dumps_really_does(
    label: str, payload: Any, bounded_work: Callable[..., Any]
) -> None:
    cache = _cache("mem")
    with (
        bounded_work(what=f"{label} message"),
        pytest.raises(ValueError, match="JSON round-trip") as exc,
    ):
        cache.put(prompt=PROMPT, payload=payload, model=MODEL)
    message = str(exc.value)

    # The cyclic rows and the non-string-key rows carry their own dedicated
    # messages (#203 / #192) and are not part of the converts-vs-raises split.
    if "contains itself" in message or "non-string key" in message:
        return

    mechanism = _real_json_mechanism(payload)
    if mechanism == "converts":
        assert _CONVERTS in message, (
            f"{label}: json.dumps silently converts this, but the message claims "
            f"a TypeError. Got: {message}"
        )
        assert _RAISES not in message
    else:
        assert _RAISES in message, (
            f"{label}: json.dumps refuses this, but the message claims a silent "
            f"conversion. Got: {message}"
        )
        assert _CONVERTS not in message


def test_both_message_mechanisms_are_exercised() -> None:
    """Anti-vacuous arm for the parametrized test above: if every rejected row
    happened to land on one branch, the other branch's assertion would never
    run and could rot to anything."""
    kinds = {_real_json_mechanism(pl) for _, pl in _REJECTED}
    assert "converts" in kinds
    assert "raises" in kinds


def test_a_tuple_is_told_it_becomes_a_list_not_a_tuple() -> None:
    """The first row of this module's own table, and the one the old message got
    most wrong. JSON has a single array type, so the base json dispatches on
    (`tuple`) is not the type the value comes back as (`list`) -- a message
    built from the base alone would tell an operator their tuple round-trips to
    a tuple, which is the entire defect restated as advice."""
    cache = _cache("mem")
    with pytest.raises(ValueError, match="JSON round-trip") as exc:
        cache.put(prompt=PROMPT, payload=("a", "b"), model=MODEL)
    message = str(exc.value)
    assert "encodes it as a plain list" in message
    assert "plain tuple" not in message


def test_the_defaultdict_row_reproduces_the_consumer_level_harm() -> None:
    """Why a subclass row is not pedantry. Before the fix both backends accepted
    this payload and served different objects; the divergence surfaced in the
    *consumer*, at a reported cache hit, on the backend `RedisStorage`'s own
    docstring recommends. Measured, before: `served["answers"]["q2"]` returned
    `[]` on in-memory and raised `KeyError: 'q2'` on redis.

    Now both backends refuse it at the seam with the identical message, which is
    the whole point of D-015's "validate at `put`, not inside a backend".
    """
    payload = _default_dict()
    messages = []
    for backend in ("mem", "redis"):
        cache = _cache(backend)
        with pytest.raises(ValueError, match="JSON round-trip") as exc:
            cache.put(prompt=PROMPT, payload=payload, model=MODEL)
        messages.append(str(exc.value))
        assert len(cache.storage) == 0
    assert messages[0] == messages[1]
    assert "defaultdict" in messages[0]
    assert "plain dict" in messages[0]


# --- #207: the accepted set is exactly-typed, discovered not asserted --------


def _every_node(value: Any) -> list[Any]:
    """Flatten a payload to every node in it, containers included."""
    out: list[Any] = []
    stack = [value]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        out.append(node)
        if isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return out


def test_every_accepted_row_is_exactly_typed_all_the_way_down() -> None:
    """The rule the table encodes, stated over the table itself.

    This is the invariant `isinstance` could not express: an accepted payload
    must contain no node whose `type` is outside the JSON set, not merely no
    node that fails an `isinstance`. Written against the table rather than the
    validator so it stays true if the validator is rewritten.
    """
    allowed = {type(None), bool, int, float, str, dict, list}
    for label, payload in _ACCEPTED:
        for node in _every_node(payload):
            assert type(node) in allowed, (
                f"{label} carries a {type(node).__name__} node ({node!r}); an accepted "
                "row must be exactly-typed all the way down or the two backends can "
                "still diverge on it"
            )


def test_the_subclass_rows_would_all_have_passed_an_isinstance_gate() -> None:
    """Anti-vacuous arm for the subclass rows: they only prove something if they
    are genuinely `isinstance` of an accepted type. A row that was already
    caught by the old gate (a `set`, say) would make the new rows look like
    coverage while testing nothing new."""
    subclass_rows = [
        pl
        for lbl, pl in _REJECTED
        if lbl
        in {
            "IntEnum value",
            "StrEnum value",
            "bare IntEnum",
            "defaultdict",
            "Counter",
            "OrderedDict",
            "dict subclass",
            "list subclass",
            "str subclass",
            "int subclass",
            "float subclass",
            "str-subclass dict key",
        }
    ]
    assert len(subclass_rows) == 12, "the subclass rows were renamed or dropped"
    for payload in subclass_rows:
        offenders = [
            n
            for n in _every_node(payload)
            if type(n) not in {type(None), bool, int, float, str, dict, list}
        ]
        assert offenders, f"{payload!r} has no subclass node -- it proves nothing"
        for node in offenders:
            assert isinstance(node, (dict, list, tuple, str, int, float, bool)), (
                f"{node!r} ({type(node).__name__}) is not an isinstance of any JSON "
                "base, so the old gate already rejected it and this row is not a "
                "#207 regression test"
            )
