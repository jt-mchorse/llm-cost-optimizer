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

import datetime as dt
import math
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


# (label, payload, accepted?) -- the probe that found the defect, kept verbatim.
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
def test_rejected_payloads_fail_identically_on_both_backends(label: str, payload: Any) -> None:
    """Same exception type, same message, before any backend is touched."""
    messages = {}
    for backend in ("mem", "redis"):
        cache = _cache(backend)
        with pytest.raises(ValueError, match="JSON round-trip") as exc:
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
