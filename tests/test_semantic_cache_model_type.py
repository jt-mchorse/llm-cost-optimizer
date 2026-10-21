"""A non-`str` model is refused at `put` and `lookup` on every backend (#343).

`_is_record_blob` (#335) rejects a stored blob whose `model` is not a `str`, but
`put` never checked `model`. Measured on main 35cd5f9 with
`put("capital of spain", "Madrid", model=m, tags=("geo",))`:

    m = 4, None, 1.5, True
      mem    lookup hit True   len 1
      redis  lookup hit False  len 0   invalidate(tag="geo") -> 0
             key `cache:...` left behind with no TTL

The writer emitted what its own reader rejects, the shape #337 fixed for `bool`
vector components.
"""

from __future__ import annotations

import pytest

from cost_optimizer.semantic_cache import HashEmbedder, InMemoryStorage, SemanticCache

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import RedisStorage  # noqa: E402

PROMPT = "capital of spain"
BACKENDS = ["mem", "redis"]
BAD_MODELS = [4, None, 1.5, True, b"m"]


def _cache(backend: str) -> tuple[SemanticCache, object]:
    client = fakeredis.FakeRedis()
    storage = InMemoryStorage() if backend == "mem" else RedisStorage(client=client)
    return SemanticCache(embedder=HashEmbedder(), storage=storage), client


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("model", BAD_MODELS, ids=repr)
def test_put_refuses_a_non_str_model_and_writes_nothing(backend: str, model: object) -> None:
    cache, client = _cache(backend)
    with pytest.raises(ValueError, match="model must be a str"):
        cache.put(PROMPT, "Madrid", model=model, tags=("geo",))  # type: ignore[arg-type]
    assert len(cache.storage) == 0
    assert client.keys("*") == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("model", BAD_MODELS, ids=repr)
def test_lookup_refuses_a_non_str_model(backend: str, model: object) -> None:
    cache, _ = _cache(backend)
    cache.put(PROMPT, "Madrid", model="m")
    with pytest.raises(ValueError, match="model must be a str"):
        cache.lookup(PROMPT, model=model)  # type: ignore[arg-type]
    # Refused before any accounting: no miss recorded, the str record intact.
    assert cache.stats.misses == 0
    assert cache.lookup(PROMPT, model="m").hit


# Controls: green before and after the fix.


class _ModelId(str):
    pass


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("model", ["m", "", "claude-haiku-4-5", _ModelId("m")], ids=repr)
def test_a_str_model_round_trips_and_invalidates_on_both_backends(backend: str, model: str) -> None:
    cache, _ = _cache(backend)
    cache.put(PROMPT, "Madrid", model=model, tags=("geo",))
    result = cache.lookup(PROMPT, model=model)
    assert result.hit
    assert result.payload == "Madrid"
    assert len(cache.storage) == 1
    assert cache.invalidate(tag="geo") == 1
    assert not cache.lookup(PROMPT, model=model).hit
