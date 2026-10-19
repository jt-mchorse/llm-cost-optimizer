"""An exact repeat hits at `similarity_threshold=1.0` (#327).

`cosine(v, v)` is exactly 1, but `dot / (na * nb)` rounded to
0.9999999999999999 for about a quarter of non-normalized vectors (540 of 2000
on 3.14, 593 on 3.11). Measured on main with a deterministic non-normalized
384-d embedder: 60 of 200 exact repeats missed at threshold 1.0.
"""

from __future__ import annotations

import math
import random
import zlib

import pytest

from cost_optimizer.semantic_cache import InMemoryStorage, SemanticCache, cosine


def _raw(seed: int, dim: int = 384) -> list[float]:
    r = random.Random(seed)
    return [r.gauss(0, 1) for _ in range(dim)]


class _RawEmbedder:
    def embed(self, text: str) -> list[float]:
        return _raw(zlib.crc32(text.encode()))


def _quotient(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_the_fixture_rounds_below_one_without_the_fix() -> None:
    # Anti-vacuity on whatever interpreter runs this.
    assert sum(1 for s in range(400) if _quotient(_raw(s), _raw(s)) < 1.0) >= 20


def test_a_vector_is_exactly_similar_to_itself() -> None:
    for s in range(400):
        v = _raw(s)
        assert cosine(v, list(v)) == 1.0


def _storages():  # type: ignore[no-untyped-def]
    out = [pytest.param(InMemoryStorage(), id="memory")]
    try:
        import fakeredis

        from cost_optimizer.semantic_cache import RedisStorage

        out.append(pytest.param(RedisStorage(client=fakeredis.FakeRedis()), id="redis"))
    except ImportError:  # pragma: no cover - fakeredis is a dev dependency
        pass
    return out


@pytest.mark.parametrize("storage", _storages())
def test_every_exact_repeat_hits_at_threshold_one(storage) -> None:  # type: ignore[no-untyped-def]
    cache = SemanticCache(embedder=_RawEmbedder(), storage=storage, similarity_threshold=1.0)
    misses = []
    for i in range(200):
        prompt = f"prompt {i}"
        cache.put(prompt, "answer", model="m")
        result = cache.lookup(prompt, model="m")
        if not result.hit:
            misses.append((prompt, result.similarity))
    assert misses == []


def test_distinct_vectors_are_unchanged() -> None:
    for s in range(200):
        a, b = _raw(s), _raw(s + 10_000)
        assert cosine(a, b) == _quotient(a, b)


def test_the_zero_and_overflow_fallbacks_are_unchanged() -> None:
    assert cosine([0.0, 0.0], [0.0, 0.0]) == 0.0
    huge = [1e200, 1e200]
    assert cosine(huge, list(huge)) == 0.0  # sum(x*x) overflows: the documented 0.0
