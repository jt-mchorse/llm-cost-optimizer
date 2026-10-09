"""A tag that cannot be encoded as UTF-8 is refused before any backend is touched (#287).

`_checked_tags` and `invalidate` checked only that a tag is a `str`. Measured on
main with `tags=("tenant-\\ud800",)`:

    mem    put ok                           invalidate -> 1          then a miss
    redis  put RAISED UnicodeEncodeError    invalidate RAISED ...    still a hit

`RedisStorage.put` wrote the record before indexing its tags, so the record was
served but never evictable by tag.
"""

from __future__ import annotations

import pytest

from cost_optimizer.semantic_cache import HashEmbedder, InMemoryStorage, SemanticCache

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import RedisStorage  # noqa: E402

PROMPT = "What is the refund policy?"
BACKENDS = ["mem", "redis"]
BAD_TAGS = ["tenant-\ud800", "\udfff", "a\udc80b"]


def _cache(backend: str) -> SemanticCache:
    storage = InMemoryStorage() if backend == "mem" else RedisStorage(client=fakeredis.FakeRedis())
    return SemanticCache(embedder=HashEmbedder(), storage=storage)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("tag", BAD_TAGS, ids=ascii)
def test_put_refuses_the_tag_and_stores_nothing(backend: str, tag: str) -> None:
    cache = _cache(backend)
    with pytest.raises(ValueError, match="cannot be encoded as UTF-8"):
        cache.put(PROMPT, "answer", model="m", tags=("ok", tag))
    assert not cache.lookup(PROMPT, model="m").hit


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("tag", BAD_TAGS, ids=ascii)
def test_invalidate_refuses_the_tag_the_same_way_on_both_backends(backend: str, tag: str) -> None:
    cache = _cache(backend)
    cache.put(PROMPT, "answer", model="m", tags=("ok",))
    with pytest.raises(ValueError, match="cannot be encoded as UTF-8"):
        cache.invalidate(tag=tag)
    assert cache.lookup(PROMPT, model="m").hit


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("tag", ["tenant-é", "租户", "emoji-\U0001f600"])
def test_a_non_ascii_tag_still_round_trips(backend: str, tag: str) -> None:
    cache = _cache(backend)
    cache.put(PROMPT, "answer", model="m", tags=(tag,))
    assert cache.lookup(PROMPT, model="m").hit
    assert cache.invalidate(tag=tag) == 1
    assert not cache.lookup(PROMPT, model="m").hit
