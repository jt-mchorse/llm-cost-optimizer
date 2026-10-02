"""`SemanticCache.put` refuses a `tags` value that is not a collection of tag strings (#245).

`tags` is typed `Iterable[str]`, and a `str` is one. Measured on `1dcb7ae`:

    put(..., tags="tenant-42")   stored tags ['-', '2', '4', 'a', 'e', 'n', 't']
    invalidate(tag="tenant-42")  -> 0   the stale answer kept being served
    invalidate(tag="t")          -> 1   evicted a record nobody tagged "t"
    put(..., tags=[1, "x"])      stored by InMemoryStorage; RedisStorage raised
                                 TypeError sorting the mixed set
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from cost_optimizer.semantic_cache import HashEmbedder, InMemoryStorage, SemanticCache

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import RedisStorage  # noqa: E402

PROMPT = "What is the refund policy?"


def _cache(backend: str) -> SemanticCache:
    storage = InMemoryStorage() if backend == "mem" else RedisStorage(client=fakeredis.FakeRedis())
    return SemanticCache(embedder=HashEmbedder(), storage=storage)


BACKENDS = ["mem", "redis"]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("tags", ["tenant-42", b"tenant-42", "t"], ids=["str", "bytes", "one-char"])
def test_a_bare_string_is_refused_before_anything_is_stored(backend: str, tags: object) -> None:
    cache = _cache(backend)
    with pytest.raises(ValueError, match="not a single") as excinfo:
        cache.put(PROMPT, "answer", model="m", tags=tags)  # type: ignore[arg-type]
    assert "tags=(" in str(excinfo.value)  # names the spelling that works
    assert not cache.lookup(PROMPT, model="m").hit


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "bad", [1, None, ("nested",), b"bytes"], ids=["int", "none", "tuple", "bytes"]
)
def test_a_non_string_tag_is_refused_on_both_backends(backend: str, bad: object) -> None:
    """InMemoryStorage stored these and RedisStorage crashed on them: the two
    backends disagreed about one call."""
    cache = _cache(backend)
    with pytest.raises(ValueError, match="every tag must be a str"):
        cache.put(PROMPT, "answer", model="m", tags=["ok", bad])  # type: ignore[list-item]
    assert not cache.lookup(PROMPT, model="m").hit


def _gen() -> Iterable[str]:
    yield "tenant-42"
    yield "policy"


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "tags",
    [("tenant-42", "policy"), ["tenant-42", "policy"], {"tenant-42", "policy"}, _gen],
    ids=["tuple", "list", "set", "generator"],
)
def test_collections_of_tags_still_work_and_invalidate(backend: str, tags: object) -> None:
    cache = _cache(backend)
    value = tags() if callable(tags) else tags
    cache.put(PROMPT, "answer", model="m", tags=value)  # type: ignore[arg-type]
    assert cache.lookup(PROMPT, model="m").hit
    # The single-character tag the old behaviour invented matches nothing now.
    assert cache.invalidate(tag="t") == 0
    assert cache.invalidate(tag="tenant-42") == 1
    assert not cache.lookup(PROMPT, model="m").hit


def test_no_tags_is_still_the_default() -> None:
    cache = _cache("mem")
    cache.put(PROMPT, "answer", model="m")
    assert cache.lookup(PROMPT, model="m").hit
