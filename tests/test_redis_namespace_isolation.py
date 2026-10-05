"""`RedisStorage(key_prefix=...)` is a namespace for records AND tags (#253, D-024).

Measured on `main` (fakeredis, one client):

    A=key_prefix "tenantA", B="tenantB"; B.put(tags=("v1",))
    A.invalidate(tag="v1") -> 0;  B.invalidate(tag="v1") -> 0;  B still serves the stale answer
    C="cache", D="cache:eu";  D.put("P")
    len(C) -> 1;  C.lookup("P") -> hit=True, payload "D-only"

Every cache shared the `tag:<name>` SET, whose members are bare record keys, so
A's index validation `srem`-ed B's members and deleted the SET; and the SCAN
pattern `"cache:*"` also matched `"cache:eu:..."`.
"""

from __future__ import annotations

import pytest

from cost_optimizer.semantic_cache import HashEmbedder, SemanticCache

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import RedisStorage  # noqa: E402

PROMPT = "What is the refund policy?"


def _cache(client: object, **kw: object) -> SemanticCache:
    return SemanticCache(embedder=HashEmbedder(), storage=RedisStorage(client=client, **kw))


@pytest.mark.parametrize("order", ["A-first", "B-first"])
def test_one_caches_invalidate_does_not_wipe_anothers_tag_index(order: str) -> None:
    r = fakeredis.FakeRedis()
    a, b = _cache(r, key_prefix="tenantA"), _cache(r, key_prefix="tenantB")
    a.put(PROMPT, "answer-A", model="m", tags=("v1",))
    b.put(PROMPT, "answer-B", model="m", tags=("v1",))
    first, second = (a, b) if order == "A-first" else (b, a)
    assert first.invalidate(tag="v1") == 1
    assert second.lookup(PROMPT, model="m").hit  # untouched by the other's invalidate
    assert second.invalidate(tag="v1") == 1
    assert not second.lookup(PROMPT, model="m").hit


@pytest.mark.parametrize(
    ("outer", "inner"), [("cache", "cache:eu"), ("t", "t:x"), ("tenantA", "tenantA:tag")]
)
def test_a_nested_prefix_is_not_scanned_as_this_caches_records(outer: str, inner: str) -> None:
    r = fakeredis.FakeRedis()
    o, i = _cache(r, key_prefix=outer), _cache(r, key_prefix=inner)
    i.put(PROMPT, "inner-only", model="m", tags=("t",))
    assert len(o.storage) == 0
    assert not o.lookup(PROMPT, model="m").hit
    assert len(i.storage) == 1
    o.put(PROMPT, "outer", model="m")
    assert o.lookup(PROMPT, model="m").payload == "outer"
    assert i.lookup(PROMPT, model="m").payload == "inner-only"


def test_a_prefix_with_glob_characters_matches_only_itself() -> None:
    r = fakeredis.FakeRedis()
    star, plain = _cache(r, key_prefix="c*"), _cache(r, key_prefix="cx")
    plain.put(PROMPT, "plain", model="m")
    assert len(star.storage) == 0
    star.put(PROMPT, "star", model="m")
    assert star.lookup(PROMPT, model="m").payload == "star"


def test_the_default_layout_is_unchanged() -> None:
    r = fakeredis.FakeRedis()
    cache = _cache(r)
    cache.put(PROMPT, "x", model="m", tags=("t",))
    keys = sorted(k.decode() for k in r.scan_iter())
    assert len(keys) == 2
    assert keys[0].startswith("cache:")
    assert len(keys[0]) == len("cache:") + 16
    assert keys[1] == "tag:t"


def test_an_explicit_tag_prefix_is_honoured() -> None:
    r = fakeredis.FakeRedis()
    cache = _cache(r, key_prefix="tenantA", tag_prefix="shared-tags")
    cache.put(PROMPT, "x", model="m", tags=("t",))
    assert r.exists("shared-tags:t")


def test_a_custom_prefix_keeps_its_tags_under_itself() -> None:
    r = fakeredis.FakeRedis()
    cache = _cache(r, key_prefix="tenantA")
    cache.put(PROMPT, "x", model="m", tags=("t",))
    assert r.exists("tenantA:tag:t")
    assert len(cache.storage) == 1  # its own tag SET is not counted as a record
