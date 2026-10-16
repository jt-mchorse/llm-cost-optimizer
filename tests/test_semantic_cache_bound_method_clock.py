"""The same clock passed to both sides is accepted however it is spelled (#319).

#172's guard compared the cache's `now_fn` with `RedisStorage.now_fn` by
identity. A bound method is a fresh object on every attribute access, so
`clock.now is clock.now` is False and the guard refused the same test clock on
both sides -- with a message telling the operator to do exactly that.
"""

from __future__ import annotations

import pytest

from cost_optimizer import HashEmbedder, SemanticCache

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import RedisStorage  # noqa: E402


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def now(self) -> float:
        return self.t


def test_a_bound_method_is_a_new_object_on_each_access() -> None:
    # The premise: what an identity check sees for one clock spelled twice.
    clock = _Clock()
    assert clock.now is not clock.now
    assert clock.now == clock.now


def test_the_same_bound_method_clock_on_both_sides_constructs() -> None:
    clock = _Clock()
    storage = RedisStorage(client=fakeredis.FakeRedis(), now_fn=clock.now)
    SemanticCache(embedder=HashEmbedder(), storage=storage, now_fn=clock.now)


def test_and_the_cache_then_runs_on_that_clock() -> None:
    # Constructing is not the point; caching on the shared clock is. The entry
    # must be live half-way through its ttl and gone after it, in fake time.
    clock = _Clock()
    client = fakeredis.FakeRedis()
    storage = RedisStorage(client=client, now_fn=clock.now)
    cache = SemanticCache(
        embedder=HashEmbedder(), storage=storage, default_ttl_s=60.0, now_fn=clock.now
    )
    key = cache.put("how do I refund a charge", "answer-A", model="m")
    assert client.ttl(storage._record_key(key)) == 60

    clock.t += 30.0
    assert cache.lookup("how do I refund a charge", model="m").hit is True
    clock.t += 31.0
    assert cache.lookup("how do I refund a charge", model="m").hit is False


def test_two_different_clock_instances_are_still_refused() -> None:
    storage = RedisStorage(client=fakeredis.FakeRedis(), now_fn=_Clock().now)
    with pytest.raises(ValueError, match="differs from this cache's"):
        SemanticCache(embedder=HashEmbedder(), storage=storage, now_fn=_Clock().now)


def test_a_bound_method_against_the_default_wall_clock_is_still_refused() -> None:
    # #172's shape, with the fake clock spelled as a bound method.
    storage = RedisStorage(client=fakeredis.FakeRedis())
    with pytest.raises(ValueError, match="differs from this cache's"):
        SemanticCache(embedder=HashEmbedder(), storage=storage, now_fn=_Clock().now)


def test_the_wall_clock_on_the_storage_against_a_bound_method_on_the_cache_is_refused() -> None:
    clock = _Clock()
    storage = RedisStorage(client=fakeredis.FakeRedis(), now_fn=clock.now)
    with pytest.raises(ValueError, match="differs from this cache's"):
        SemanticCache(embedder=HashEmbedder(), storage=storage)
