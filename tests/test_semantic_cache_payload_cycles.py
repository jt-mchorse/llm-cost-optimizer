"""A cyclic payload is refused, and the refusal *terminates* (#203).

`_validate_payload` (#192) walks a payload to reject shapes the two shipped
backends disagree about. It had no cycle check, so the one shape they disagree
about most starkly could not reach a verdict at all: measured through
`SemanticCache.put`, a self-referential dict ran until the process held 5.7 GB
resident, because every pop of a cyclic container pushes its children back with
a longer `path` string.

The divergence is real and is exactly this validator's business. At the storage
seam, with the validator bypassed::

    mem    put -> OK, len=1                                # deepcopy memoizes; cycle survives
    redis  put -> ValueError: Circular reference detected  # json.dumps refuses

`test_backends_disagree_about_a_cycle_at_the_storage_seam` below pins that, so
the reject rule keeps a reason rather than becoming folklore.

Two things need asserting and they are not the same thing:

1. The cycle is **refused** with the seam's path-naming `ValueError`.
2. The walk **terminates**. A test that only asserts `(1)` passes trivially on
   correct code, and a future rewrite that reintroduces the hang would surface
   as a suite that never finishes rather than as a red test. Every case here
   runs under a bounded-work guard.

The negative control matters as much: `json.dumps` accepts a container reachable
by two different paths and emits it twice, so the validator must too. A blanket
"seen this id -> reject" would pass every test in the first group and start
rejecting payloads that work on both backends today.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from cost_optimizer.semantic_cache import (
    CacheRecord,
    HashEmbedder,
    InMemoryStorage,
    SemanticCache,
    _validate_payload,
)

MODEL = "m"
PROMPT = "what is the capital of france"


def _self_dict() -> dict[str, Any]:
    d: dict[str, Any] = {"answer": "Paris"}
    d["self"] = d
    return d


def _self_list() -> list[Any]:
    xs: list[Any] = [1]
    xs.append(xs)
    return xs


def _indirect_cycle() -> dict[str, Any]:
    a: dict[str, Any] = {}
    b: dict[str, Any] = {"a": a}
    a["b"] = b
    return a


def _dict_holding_a_self_list() -> dict[str, Any]:
    """The cycle is one level down, not at the root — the root is fine."""
    return {"answer": "Paris", "trace": _self_list()}


_CYCLIC_BUILDERS = [
    ("self-referential dict", _self_dict),
    ("self-referential list", _self_list),
    ("indirect a -> b -> a", _indirect_cycle),
    ("cycle nested under a clean root", _dict_holding_a_self_list),
]


@pytest.mark.parametrize(
    ("label", "build"), _CYCLIC_BUILDERS, ids=[label for label, _ in _CYCLIC_BUILDERS]
)
def test_cyclic_payload_is_refused_and_the_walk_terminates(
    label: str, build: Any, bounded_work: Callable[..., Any]
) -> None:
    payload = build()
    with bounded_work(what="_validate_payload"), pytest.raises(ValueError, match="contains itself"):
        _validate_payload(payload)


@pytest.mark.parametrize(
    ("label", "build"), _CYCLIC_BUILDERS, ids=[label for label, _ in _CYCLIC_BUILDERS]
)
def test_the_refusal_reaches_the_public_put_seam(
    label: str, build: Any, bounded_work: Callable[..., Any]
) -> None:
    """The harm was reachable through `SemanticCache.put`, so the fix must be too."""
    cache = SemanticCache(embedder=HashEmbedder(), storage=InMemoryStorage())
    payload = build()
    with bounded_work(what="put"), pytest.raises(ValueError, match="contains itself"):
        cache.put(PROMPT, payload, model=MODEL)
    assert len(cache.storage) == 0, "a refused payload must not have been stored"


@pytest.mark.parametrize(
    ("label", "build"), _CYCLIC_BUILDERS, ids=[label for label, _ in _CYCLIC_BUILDERS]
)
def test_the_error_names_the_path_to_the_cycle(
    label: str, build: Any, bounded_work: Callable[..., Any]
) -> None:
    """`payload['b']['a']`, not "somewhere in your payload".

    Bounded like the two drivers above. Every test in this file that feeds a
    cyclic payload to the walk has to be, or removing the cycle check takes the
    *runner* down instead of turning this file red — which is how the
    calibration in `conftest.DEFAULT_TRACE_EVENT_LIMIT` was found: an earlier
    draft left this one arm unguarded and pytest printed eight `F`s and then
    nothing at all.
    """
    with (
        bounded_work(what="_validate_payload"),
        pytest.raises(ValueError, match="contains itself") as exc,
    ):
        _validate_payload(build())
    message = str(exc.value)
    assert message.startswith("payload"), message
    assert "Break the cycle before caching" in message


# --- negative control: sharing is not a cycle -------------------------------

_SHARED_ACYCLIC: list[tuple[str, Any]] = []


def _register_shared() -> None:
    x = {"k": 1, "xs": [1, 2]}
    _SHARED_ACYCLIC.append(("same dict under two keys", {"a": x, "b": x}))
    _SHARED_ACYCLIC.append(("same dict at two depths", {"a": x, "b": {"c": x}}))
    ys = [1, 2]
    _SHARED_ACYCLIC.append(("same list twice in a list", [ys, ys]))
    _SHARED_ACYCLIC.append(("diamond", {"l": {"leaf": x}, "r": {"leaf": x}}))


_register_shared()


@pytest.mark.parametrize(
    ("label", "payload"), _SHARED_ACYCLIC, ids=[label for label, _ in _SHARED_ACYCLIC]
)
def test_a_container_reachable_twice_is_not_a_cycle(label: str, payload: Any) -> None:
    """`json.dumps` accepts these and emits the shared node twice, so we must too.

    This is the arm that a "seen this id before -> reject" implementation fails.
    That implementation would satisfy every cycle test above, which is why the
    control is here: the rule being modelled is `json.dumps`'s
    `check_circular=True` (is a container its own *ancestor*), not "has this
    object been visited".
    """
    json.dumps(payload)  # the reference implementation accepts it...
    _validate_payload(payload)  # ...so the validator must not refuse it
    cache = SemanticCache(embedder=HashEmbedder(), storage=InMemoryStorage())
    cache.put(PROMPT, payload, model=MODEL)
    assert cache.lookup(PROMPT, model=MODEL).hit


def test_json_dumps_is_the_rule_being_modelled() -> None:
    """Pin the reference behaviour itself, so the two arms above stay coupled.

    If a future Python changed `json.dumps` to tolerate cycles, or to reject
    shared references, this validator's contract would move with it — and this
    assertion is where that shows up, rather than as a mysterious divergence
    between the validator and a backend.
    """
    with pytest.raises(ValueError, match="Circular reference detected"):
        json.dumps(_self_dict())
    x = {"k": 1}
    assert json.dumps({"a": x, "b": x}) == '{"a": {"k": 1}, "b": {"k": 1}}'


def test_backends_disagree_about_a_cycle_at_the_storage_seam() -> None:
    """Why the cycle belongs on the reject side at all (#192's own criterion).

    Driven at the `Storage` seam, below `SemanticCache.put`, because that is
    where the divergence lives — above it, the validator now makes both roads
    agree, which is the point of the fix and would hide the reason for it.
    """
    fakeredis = pytest.importorskip("fakeredis")
    from cost_optimizer.semantic_cache import RedisStorage

    record = CacheRecord(
        key="k1",
        vector=(1.0, 0.0),
        payload=_self_dict(),
        tags=frozenset(),
        expires_at=None,
        model=MODEL,
    )

    mem = InMemoryStorage()
    mem.put(record)  # deepcopy memoizes the cycle: stored intact
    assert len(mem) == 1

    redis = RedisStorage(client=fakeredis.FakeRedis())
    with pytest.raises(ValueError, match="Circular reference detected"):
        redis.put(record)  # json.dumps refuses
    assert len(redis) == 0
