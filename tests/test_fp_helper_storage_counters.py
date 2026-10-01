"""`measure_false_positive_rate` restores the lookup counters, not the storage ones (#247).

Its `finally` restored the whole `CacheStats` snapshot. On a TTL cache whose
records expire during the measurement, `lookup` really purged them from
storage -- and the restore rolled `expired_purged` back to 0, so those evictions
were never counted. Measured on `1dcb7ae`: five records gone, counter 0.
"""

from __future__ import annotations

import pytest

from cost_optimizer.semantic_cache import (
    HashEmbedder,
    InMemoryStorage,
    SemanticCache,
    measure_false_positive_rate,
)


def _ttl_cache(clock: list[float]) -> SemanticCache:
    cache = SemanticCache(
        embedder=HashEmbedder(),
        storage=InMemoryStorage(),
        default_ttl_s=60,
        now_fn=lambda: clock[0],
    )
    for i in range(5):
        cache.put(f"question number {i}", f"a{i}", model="m")
    return cache


def test_purges_during_the_measurement_are_counted() -> None:
    clock = [0.0]
    cache = _ttl_cache(clock)
    clock[0] = 120.0
    measure_false_positive_rate(
        cache, [("question number 1", "x")], model="m", call_model=lambda p: "x"
    )
    assert len(cache.storage._records) == 0  # the purge really happened
    assert cache.stats.expired_purged == 5


def test_hits_and_misses_are_still_restored() -> None:
    """D-007: the offline lookups must not move the production hit rate."""
    clock = [0.0]
    cache = _ttl_cache(clock)
    cache.lookup("question number 0", model="m")  # one real hit before
    before = (cache.stats.hits, cache.stats.misses)
    measure_false_positive_rate(
        cache,
        [("question number 1", "x"), ("something unrelated entirely", "y")],
        model="m",
        call_model=lambda p: "x",
    )
    assert (cache.stats.hits, cache.stats.misses) == before


def test_restore_still_happens_when_call_model_raises() -> None:
    clock = [0.0]
    cache = _ttl_cache(clock)
    before = (cache.stats.hits, cache.stats.misses)

    def boom(prompt: str) -> str:
        raise RuntimeError("model down")

    with pytest.raises(RuntimeError):
        measure_false_positive_rate(cache, [("question number 1", "x")], model="m", call_model=boom)
    assert (cache.stats.hits, cache.stats.misses) == before


def test_invalidations_made_before_survive_the_measurement() -> None:
    clock = [0.0]
    cache = _ttl_cache(clock)
    cache.put("tagged question", "a", model="m", tags=("t1",))
    cache.invalidate(tag="t1")
    assert cache.stats.invalidations >= 1
    snapshot = cache.stats.invalidations
    measure_false_positive_rate(
        cache, [("question number 1", "x")], model="m", call_model=lambda p: "x"
    )
    assert cache.stats.invalidations == snapshot
