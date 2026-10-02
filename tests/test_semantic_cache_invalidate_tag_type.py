"""`SemanticCache.invalidate` refuses a tag that is not a `str` (#251).

#246 held `put`'s tags to `str`, so a non-`str` argument to `invalidate` can
never match a stored tag. Measured on `main`, after `put(..., tags=("tenant-42",))`:
a tuple, bytes, int or None returned 0 on both backends and the stale answer
kept being served; a list raised `TypeError` in memory and returned 0 on Redis.
"""

from __future__ import annotations

from typing import Any

import pytest

from cost_optimizer.semantic_cache import HashEmbedder, InMemoryStorage, SemanticCache

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import RedisStorage  # noqa: E402

PROMPT = "What is the refund policy?"
BACKENDS = ["mem", "redis"]


def _tagged_cache(backend: str) -> SemanticCache:
    storage = InMemoryStorage() if backend == "mem" else RedisStorage(client=fakeredis.FakeRedis())
    cache = SemanticCache(embedder=HashEmbedder(), storage=storage)
    cache.put(PROMPT, "answer", model="m", tags=("tenant-42",))
    return cache


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "bad",
    [("tenant-42",), ["tenant-42"], b"tenant-42", 42, None],
    ids=["tuple", "list", "bytes", "int", "none"],
)
def test_a_non_str_tag_is_refused_on_both_backends(backend: str, bad: Any) -> None:
    cache = _tagged_cache(backend)
    with pytest.raises(ValueError, match="invalidate takes one tag as a str") as exc:
        cache.invalidate(tag=bad)
    assert "call invalidate once per tag" in str(exc.value)
    # Refused before the storage: nothing dropped, nothing counted.
    assert cache.lookup(PROMPT, model="m").hit
    assert cache.stats.invalidations == 0


@pytest.mark.parametrize("backend", BACKENDS)
def test_the_working_spelling_still_evicts(backend: str) -> None:
    cache = _tagged_cache(backend)
    assert cache.invalidate(tag="tenant-42") == 1
    assert not cache.lookup(PROMPT, model="m").hit
    assert cache.stats.invalidations == 1


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("tag", ["nobody-has-this", ""], ids=["absent", "empty"])
def test_a_str_tag_nothing_carries_still_returns_zero(backend: str, tag: str) -> None:
    cache = _tagged_cache(backend)
    assert cache.invalidate(tag=tag) == 0
    assert cache.lookup(PROMPT, model="m").hit
