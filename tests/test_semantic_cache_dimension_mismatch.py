"""A stored vector of another dimension is skipped, not raised on (#357).

`put` checked that embedding values are finite but never their length against
what was already stored, and `find_nearest` handed every same-model record to
`cosine()`, which raises `ValueError("vector length mismatch")`. So one record
of another dimension -- left behind when a persistent store outlives an
embedder swap -- made every `lookup` for that model raise, an exact hit
included, on both backends.

Measured on origin/main 6b46bc8: after a 4-dim `put` and an 8-dim `put` of
"hello", `lookup("hello")` and `lookup("anything else")` both raised
`ValueError: vector length mismatch: 8 vs 4`; an empty `[]` embedding was
accepted by `put`.
"""

from __future__ import annotations

import hashlib

import pytest

from cost_optimizer.semantic_cache import (
    CacheRecord,
    InMemoryStorage,
    RedisStorage,
    SemanticCache,
    cosine,
)

MODEL = "m"


class _DimEmbedder:
    """Deterministic `dim`-dimensional embedder (no zero components)."""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()
        return [float(digest[i]) + 1.0 for i in range(self.dim)]


class _EmptyEmbedder:
    def embed(self, text: str) -> list[float]:
        return []


def _redis_storage() -> RedisStorage:
    fakeredis = pytest.importorskip("fakeredis")
    return RedisStorage(client=fakeredis.FakeRedis())


BACKENDS = [
    pytest.param(InMemoryStorage, id="memory"),
    pytest.param(_redis_storage, id="redis"),
]


def _swapped_store(make_storage):
    """A store with one 4-dim record, then a new deploy at 8 dims putting "hello"."""
    storage = make_storage()
    old = SemanticCache(embedder=_DimEmbedder(4), storage=storage)
    old.put("what is the capital of France?", "Paris", model=MODEL)
    new = SemanticCache(embedder=_DimEmbedder(8), storage=storage)
    new.put("hello", "hi", model=MODEL)
    return storage, new


@pytest.mark.parametrize("make_storage", BACKENDS)
class TestEmbedderSwap:
    def test_same_dimension_record_is_a_hit(self, make_storage) -> None:
        _, new = _swapped_store(make_storage)
        result = new.lookup("hello", model=MODEL)
        assert result.hit
        assert result.payload == "hi"

    def test_unrelated_prompt_is_a_miss(self, make_storage) -> None:
        _, new = _swapped_store(make_storage)
        result = new.lookup("anything else", model=MODEL)
        assert not result.hit

    def test_old_dimension_still_hits_its_own_record(self, make_storage) -> None:
        # The skip is symmetric: an old-dimension reader still finds its record
        # past the new-dimension one.
        storage, _ = _swapped_store(make_storage)
        old = SemanticCache(embedder=_DimEmbedder(4), storage=storage)
        result = old.lookup("what is the capital of France?", model=MODEL)
        assert result.hit
        assert result.payload == "Paris"

    def test_find_nearest_skips_the_other_dimension(self, make_storage) -> None:
        storage = make_storage()
        storage.put(
            CacheRecord(
                key="k4",
                vector=(1.0, 0.0, 0.0, 0.0),
                payload="p4",
                tags=frozenset(),
                expires_at=None,
            )
        )
        assert storage.find_nearest([1.0, 0.0]) is None
        storage.put(
            CacheRecord(
                key="k2", vector=(1.0, 0.0), payload="p2", tags=frozenset(), expires_at=None
            )
        )
        best = storage.find_nearest([1.0, 0.0])
        assert best is not None
        assert best[0].key == "k2"
        assert best[1] == pytest.approx(1.0)

    def test_same_dimension_store_unchanged(self, make_storage) -> None:
        # Control: no mismatch anywhere, so behaviour is exactly as before.
        cache = SemanticCache(embedder=_DimEmbedder(8), storage=make_storage())
        cache.put("hello", "hi", model=MODEL)
        cache.put("goodbye", "bye", model=MODEL)
        assert cache.lookup("hello", model=MODEL).payload == "hi"
        assert cache.lookup("goodbye", model=MODEL).payload == "bye"
        assert not cache.lookup("anything else", model=MODEL).hit


@pytest.mark.parametrize("make_storage", BACKENDS)
def test_empty_embedding_refused_at_put(make_storage) -> None:
    storage = make_storage()
    cache = SemanticCache(embedder=_EmptyEmbedder(), storage=storage)
    with pytest.raises(ValueError, match="empty"):
        cache.put("x", "y", model=MODEL)
    assert len(storage) == 0


def test_empty_embedding_refused_at_lookup() -> None:
    cache = SemanticCache(embedder=_EmptyEmbedder(), storage=InMemoryStorage())
    with pytest.raises(ValueError, match="empty"):
        cache.lookup("x", model=MODEL)


def test_cosine_still_raises_on_mismatch() -> None:
    # Control: direct callers rely on `cosine()` refusing a mismatch.
    with pytest.raises(ValueError, match="length mismatch"):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])
