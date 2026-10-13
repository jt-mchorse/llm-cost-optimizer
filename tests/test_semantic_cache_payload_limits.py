"""A payload of exact JSON types the backends still disagree on is refused (#302).

`_validate_payload` (#192, D-015) checks *types*, so every test of it before
this one drove a payload it rejects -- including the 5,000-deep one, whose leaf
is a `tuple`. Only an *accepted* payload reaches a backend, and two accepted
shapes split them. Measured through `put` + `lookup` on unfixed code::

    payload                 in-memory                 redis
    list nested 600 deep    RecursionError from put   stored, hit
    {"n": 10**4300}         stored, hit               ValueError from put

The first is `copy.deepcopy` recursing (first failure at 498 levels on 3.11
and 3.12); the second is `json.dumps` refusing an int past
`sys.get_int_max_str_digits()`. The Redis failure was a half-write too: `put`
pruned the old record's tag memberships before serializing, so after a failed
re-put the old record was live but unreachable by `invalidate(tag=...)`.

Every case runs through BOTH backends and compares the verdicts, like
`test_semantic_cache_payload_parity.py`.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import (  # noqa: E402
    _MAX_PAYLOAD_DEPTH,
    HashEmbedder,
    InMemoryStorage,
    RedisStorage,
    SemanticCache,
)

PROMPT = "what is the capital of france"
MODEL = "m"
BACKENDS = ("mem", "redis")


def _cache(backend: str) -> SemanticCache:
    storage = InMemoryStorage() if backend == "mem" else RedisStorage(client=fakeredis.FakeRedis())
    return SemanticCache(embedder=HashEmbedder(), storage=storage)


def _nested_lists(depth: int) -> Any:
    """`depth` lists, one inside the next, around a str leaf."""
    node: Any = "leaf"
    for _ in range(depth):
        node = [node]
    return node


def _nested_dicts(depth: int) -> Any:
    node: Any = "leaf"
    for _ in range(depth):
        node = {"k": node}
    return node


def _verdict(backend: str, payload: Any) -> str:
    cache = _cache(backend)
    try:
        cache.put(prompt=PROMPT, payload=payload, model=MODEL)
    except ValueError as e:
        message = str(e)
    else:
        result = cache.lookup(PROMPT, model=MODEL)
        assert result.hit
        assert result.payload == payload
        return "stored"
    assert len(cache.storage) == 0, f"{backend} stored a record on the way to the error"
    assert "JSON round-trip" in message
    return "ValueError"


# 600 is past InMemoryStorage's deepcopy limit (498) and inside RedisStorage's on
# both interpreters; 2,000 is past Redis's on 3.11 only. Either way the two
# backends used to answer differently, and from inside the backend.
@pytest.mark.parametrize("build", [_nested_lists, _nested_dicts], ids=["lists", "dicts"])
@pytest.mark.parametrize("depth", [_MAX_PAYLOAD_DEPTH + 1, 600, 2_000])
def test_a_payload_nested_past_the_cap_is_refused_on_both_backends(build: Any, depth: int) -> None:
    verdicts = {b: _verdict(b, build(depth)) for b in BACKENDS}
    assert verdicts == {"mem": "ValueError", "redis": "ValueError"}


@pytest.mark.parametrize("build", [_nested_lists, _nested_dicts], ids=["lists", "dicts"])
def test_a_payload_nested_exactly_to_the_cap_round_trips_on_both_backends(build: Any) -> None:
    """The cap must not cost a payload both backends can store."""
    verdicts = {b: _verdict(b, build(_MAX_PAYLOAD_DEPTH)) for b in BACKENDS}
    assert verdicts == {"mem": "stored", "redis": "stored"}


def test_the_depth_rejection_names_the_path() -> None:
    with pytest.raises(ValueError, match=r"nested 257 containers deep") as exc:
        _cache("mem").put(
            prompt=PROMPT, payload={"answer": _nested_lists(_MAX_PAYLOAD_DEPTH)}, model=MODEL
        )
    assert str(exc.value).startswith("payload['answer']" + "[0]" * (_MAX_PAYLOAD_DEPTH - 1))


def test_wide_shallow_payloads_are_not_depth() -> None:
    """Depth is containers on one path, not containers in total."""
    payload = {f"k{i}": [[i] for _ in range(50)] for i in range(50)}
    assert {b: _verdict(b, payload) for b in BACKENDS} == {"mem": "stored", "redis": "stored"}


@pytest.mark.parametrize("sign", [1, -1], ids=["positive", "negative"])
def test_an_int_json_cannot_write_is_refused_on_both_backends(sign: int) -> None:
    payload = {"n": sign * 10**4300}  # 4,301 digits; the default limit is 4,300
    verdicts = {b: _verdict(b, payload) for b in BACKENDS}
    assert verdicts == {"mem": "ValueError", "redis": "ValueError"}


@pytest.mark.parametrize(
    "value",
    [10**4299, -(10**4299), 2**4000, 10**600],
    ids=["4300-digits", "minus-4300-digits", "2**4000", "601-digits"],
)
def test_a_large_int_json_can_write_round_trips_on_both_backends(value: int) -> None:
    """4,300 digits is inside the default limit; the probe must not refuse it."""
    verdicts = {b: _verdict(b, {"n": value}) for b in BACKENDS}
    assert verdicts == {"mem": "stored", "redis": "stored"}


def test_the_int_check_follows_the_processs_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The limit is `sys.set_int_max_str_digits`, not a constant: an operator who
    raised it can cache what `json.dumps` will then write, and one who lowered it
    gets the refusal at the seam rather than from inside RedisStorage."""
    previous = sys.get_int_max_str_digits()
    try:
        sys.set_int_max_str_digits(0)  # unlimited
        assert {b: _verdict(b, {"n": 10**5000}) for b in BACKENDS} == {
            "mem": "stored",
            "redis": "stored",
        }
        sys.set_int_max_str_digits(640)  # the floor
        assert {b: _verdict(b, {"n": 10**700}) for b in BACKENDS} == {
            "mem": "ValueError",
            "redis": "ValueError",
        }
    finally:
        sys.set_int_max_str_digits(previous)


def test_a_refused_re_put_leaves_the_old_records_tags_intact() -> None:
    """The Redis half-write: `put` used to prune the old record's tag index and
    THEN fail in `json.dumps`, so `invalidate(tag=...)` stopped reaching a record
    that was still being served."""
    for backend in BACKENDS:
        cache = _cache(backend)
        cache.put(prompt=PROMPT, payload={"answer": "old"}, model=MODEL, tags=["tenant-a"])
        for bad in ({"n": 10**4300}, _nested_lists(600)):
            with pytest.raises(ValueError, match="JSON round-trip"):
                cache.put(prompt=PROMPT, payload=bad, model=MODEL, tags=["tenant-b"])
        assert cache.lookup(PROMPT, model=MODEL).payload == {"answer": "old"}
        assert cache.invalidate(tag="tenant-a") == 1, backend
        assert not cache.lookup(PROMPT, model=MODEL).hit, backend
