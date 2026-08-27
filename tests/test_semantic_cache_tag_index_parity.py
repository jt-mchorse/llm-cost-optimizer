"""A tag-set membership outlives the record it names, and keys are reused (#194).

`RedisStorage` keeps a `tag:<name>` SET as a tag->keys index.
`InMemoryStorage` keeps none -- it filters `tag in r.tags` on the records
themselves. Nothing kept the index in sync with the records, and a key leaves a
record's world in two ordinary ways the index never hears about:

- `invalidate_by_tag` on a *different* tag deletes the record and drops only
  that one tag's SET, leaving the key in every other SET it belonged to.
- native Redis TTL expires the record. Nothing touches any SET.

`SemanticCache._make_key` is a deterministic hash of `(prompt, model)`, so
re-caching the same prompt reuses that key exactly. The stale membership then
names a *live record that does not carry the tag*, and the old
`invalidate_by_tag` deleted it on the strength of the index alone. Measured
through the public API before this change::

    c.put(P, tags=["v1", "geography"]); c.invalidate(tag="v1")
    c.put(P, tags=["v2"]);              c.invalidate(tag="geography")

    mem   -> returned 0, still cached, len 1
    redis -> returned 1, EVICTED,      len 0

The harm is a wrong eviction, which is a cache miss, which is a paid API call --
the failure mode this package exists to prevent -- and it is quiet, because
`invalidate` reports `1` and looks like it did its job.

The `Storage` Protocol has five methods. `put` had three issues against it
(#131, #172, #192) and `find_nearest` two (#133, #188); the other three had
never been enumerated. This module is that enumeration, run as a *differential*:
every case goes through both backends and the two verdicts are compared, the
same shape `test_semantic_cache_payload_parity.py` established for #192.
"""

from __future__ import annotations

from typing import Any

import pytest

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import (  # noqa: E402
    CacheRecord,
    HashEmbedder,
    InMemoryStorage,
    RedisStorage,
    SemanticCache,
)

PROMPT = "what is the capital of france"
MODEL = "claude-opus-5"
BACKENDS = ["mem", "redis"]


class Clock:
    """Injected clock. `RedisStorage` and `SemanticCache` must share one (#172)."""

    def __init__(self, t: float = 1_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _storage(backend: str, clock: Clock) -> Any:
    if backend == "mem":
        return InMemoryStorage()
    return RedisStorage(client=fakeredis.FakeRedis(), now_fn=clock)


def _cache(backend: str, clock: Clock) -> tuple[SemanticCache, Any]:
    storage = _storage(backend, clock)
    return SemanticCache(embedder=HashEmbedder(), storage=storage, now_fn=clock), storage


def _record(key: str, tags: list[str], *, expires_at: float | None = None) -> CacheRecord:
    return CacheRecord(
        key=key,
        vector=(1.0, 0.0),
        payload={"a": 1},
        tags=frozenset(tags),
        expires_at=expires_at,
        model=MODEL,
    )


# ----------------------------------------------------------------------
# The reachability roads, through the public API
# ----------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_road_1_invalidating_a_sibling_tag_does_not_evict_the_next_generation(
    backend: str,
) -> None:
    """Retire `v1`, re-cache the same prompt as `v2`, then invalidate an
    unrelated tag the live record does not carry."""
    clock = Clock()
    cache, storage = _cache(backend, clock)

    cache.put(prompt=PROMPT, payload={"answer": "Paris"}, model=MODEL, tags=["v1", "geography"])
    cache.invalidate(tag="v1")
    cache.put(prompt=PROMPT, payload={"answer": "Paris, France"}, model=MODEL, tags=["v2"])

    dropped = cache.invalidate(tag="geography")
    hit = cache.lookup(prompt=PROMPT, model=MODEL)

    assert dropped == 0, f"{backend}: evicted a record that does not carry the tag"
    assert hit.hit
    assert hit.payload == {"answer": "Paris, France"}
    assert len(storage) == 1


def test_road_2_ttl_expiry_alone_is_enough_to_create_the_stale_membership() -> None:
    """No explicit invalidation anywhere -- just the ordinary lifecycle.

    Redis-only by construction: it is the backend with an index to go stale.
    Native TTL is simulated the way Redis actually does it -- the record key
    disappears and no SET is touched -- because a test that waited on a real
    clock would be a host-environment assertion, not a test.
    """
    clock = Clock()
    client = fakeredis.FakeRedis()
    storage = RedisStorage(client=client, now_fn=clock)
    cache = SemanticCache(embedder=HashEmbedder(), storage=storage, now_fn=clock)

    cache.put(
        prompt=PROMPT,
        payload={"answer": "Paris"},
        model=MODEL,
        tags=["geography", "v1"],
        ttl_s=60,
    )
    record_keys = [k.decode("utf-8") for k in client.keys("cache:*")]
    assert len(record_keys) == 1
    client.delete(record_keys[0])  # <- what native Redis TTL does

    # The index still names the vanished key, on every tag it ever carried.
    assert _tag_members(client, "tag:geography") == _tag_members(client, "tag:v1") != []

    cache.put(prompt=PROMPT, payload={"answer": "Paris, France"}, model=MODEL, tags=["v2"])
    dropped = cache.invalidate(tag="geography")
    hit = cache.lookup(prompt=PROMPT, model=MODEL)

    assert dropped == 0
    assert hit.hit
    assert hit.payload == {"answer": "Paris, France"}


def _tag_members(client: Any, tag_key: str) -> list[str]:
    return sorted(m.decode("utf-8") for m in client.smembers(tag_key))


def test_the_index_converges_instead_of_growing_forever() -> None:
    """Self-healing: a stale entry is dropped the first time it is walked."""
    clock = Clock()
    client = fakeredis.FakeRedis()
    storage = RedisStorage(client=client, now_fn=clock)
    cache = SemanticCache(embedder=HashEmbedder(), storage=storage, now_fn=clock)

    cache.put(prompt=PROMPT, payload={"a": 0}, model=MODEL, tags=["v1", "geography"])
    cache.invalidate(tag="v1")
    cache.put(prompt=PROMPT, payload={"a": 1}, model=MODEL, tags=["v2"])

    assert _tag_members(client, "tag:geography") != [], "precondition: the stale entry exists"
    cache.invalidate(tag="geography")
    assert _tag_members(client, "tag:geography") == []
    assert cache.lookup(prompt=PROMPT, model=MODEL).hit


# ----------------------------------------------------------------------
# Controls -- the fix must not stop tag invalidation from working
# ----------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_control_invalidating_a_tag_the_live_record_carries_still_evicts(backend: str) -> None:
    clock = Clock()
    cache, storage = _cache(backend, clock)
    cache.put(prompt=PROMPT, payload={"a": 0}, model=MODEL, tags=["v2"])
    assert cache.invalidate(tag="v2") == 1
    assert not cache.lookup(prompt=PROMPT, model=MODEL).hit
    assert len(storage) == 0


@pytest.mark.parametrize("backend", BACKENDS)
def test_control_the_same_tag_reused_across_generations_still_evicts(backend: str) -> None:
    clock = Clock()
    cache, _ = _cache(backend, clock)
    cache.put(prompt=PROMPT, payload={"a": 0}, model=MODEL, tags=["nightly"])
    cache.invalidate(tag="nightly")
    cache.put(prompt=PROMPT, payload={"a": 1}, model=MODEL, tags=["nightly"])
    assert cache.invalidate(tag="nightly") == 1
    assert not cache.lookup(prompt=PROMPT, model=MODEL).hit


@pytest.mark.parametrize("backend", BACKENDS)
def test_control_a_record_keeping_the_tag_across_generations_is_still_evicted(
    backend: str,
) -> None:
    """Road 1's shape, but the new record legitimately carries the tag."""
    clock = Clock()
    cache, _ = _cache(backend, clock)
    cache.put(prompt=PROMPT, payload={"a": 0}, model=MODEL, tags=["v1", "geography"])
    cache.invalidate(tag="v1")
    cache.put(prompt=PROMPT, payload={"a": 1}, model=MODEL, tags=["v2", "geography"])
    assert cache.invalidate(tag="geography") == 1
    assert not cache.lookup(prompt=PROMPT, model=MODEL).hit


# ----------------------------------------------------------------------
# The Protocol grid: the three methods nobody had enumerated
# ----------------------------------------------------------------------

# (label, body) -- each body is driven through BOTH backends and the two
# verdicts compared. A rule living in only one implementation shows up here as
# a disagreement, and so would a future third backend.
GRID: list[tuple[str, Any]] = [
    (
        "CONTROL one record one tag",
        lambda s, c: (s.put(_record("k1", ["a"])), (s.invalidate_by_tag("a"), len(s)))[1],
    ),
    (
        "record with two tags, invalidate both in turn",
        lambda s, c: (
            s.put(_record("k1", ["a", "b"])),
            (s.invalidate_by_tag("a"), s.invalidate_by_tag("b"), len(s)),
        )[1],
    ),
    (
        "re-put same key after a sibling-tag invalidate, then invalidate the old tag",
        lambda s, c: (
            s.put(_record("k1", ["a", "b"])),
            s.invalidate_by_tag("a"),
            s.put(_record("k1", ["c"])),
            (s.invalidate_by_tag("b"), len(s)),
        )[3],
    ),
    (
        "invalidate a tag nothing carries",
        lambda s, c: (s.put(_record("k1", ["a"])), (s.invalidate_by_tag("zzz"), len(s)))[1],
    ),
    (
        "expired-but-unpurged record carrying the tag",
        lambda s, c: (
            s.put(_record("k1", ["a"], expires_at=c.t - 1)),
            (s.invalidate_by_tag("a"), len(s)),
        )[1],
    ),
    (
        "two records share a tag",
        lambda s, c: (
            s.put(_record("k1", ["a"])),
            s.put(_record("k2", ["a"])),
            (s.invalidate_by_tag("a"), len(s)),
        )[2],
    ),
    (
        "__len__ over three records with mixed tags",
        lambda s, c: (
            s.put(_record("k1", ["a"])),
            s.put(_record("k2", ["a", "b"])),
            s.put(_record("k3", [])),
            len(s),
        )[3],
    ),
    (
        "purge_expired with one live record",
        lambda s, c: (
            s.put(_record("k1", ["a"], expires_at=c.t + 500)),
            (s.purge_expired(c.t), len(s)),
        )[1],
    ),
]


@pytest.mark.parametrize(("label", "body"), GRID, ids=[row[0] for row in GRID])
def test_storage_protocol_grid_agrees_across_backends(label: str, body: Any) -> None:
    verdicts = {}
    for backend in BACKENDS:
        clock = Clock()
        verdicts[backend] = body(_storage(backend, clock), clock)
    assert verdicts["mem"] == verdicts["redis"], (
        f"{label}: in-memory gave {verdicts['mem']!r} but redis gave {verdicts['redis']!r}"
    )


def test_the_grid_is_not_vacuous() -> None:
    """A grid that drifted to a single trivial row would make every case above
    pass while proving nothing."""
    assert len(GRID) >= 8
    labels = [row[0] for row in GRID]
    assert len(set(labels)) == len(labels)
    assert any("re-put same key" in lbl for lbl in labels), "the defect's own row must be present"


def test_purge_expired_is_a_documented_divergence_not_a_missed_one() -> None:
    """`RedisStorage.purge_expired` returns 0 where in-memory returns the count.

    That is deliberate -- native TTL does the eviction and the method exists for
    Protocol parity -- so it is asserted here with its reason rather than
    silently omitted from the grid, and a future enumeration of this Protocol
    does not have to re-derive it as a finding.
    """
    clock = Clock()
    mem = InMemoryStorage()
    mem.put(_record("k1", ["a"], expires_at=clock.t - 1))
    assert mem.purge_expired(clock.t) == 1
    assert len(mem) == 0

    redis = RedisStorage(client=fakeredis.FakeRedis(), now_fn=clock)
    redis.put(_record("k1", ["a"], expires_at=clock.t - 1))
    assert redis.purge_expired(clock.t) == 0
