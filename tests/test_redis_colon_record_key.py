"""`RedisStorage.put` refuses a record key it could never read back (#358).

Measured on `main` 6b46bc8 (fakeredis), through the public `Storage` API:

    st.put(CacheRecord(key="tenant:42", vector=(1.0, 0.0), payload="Paris", ...))
    InMemoryStorage -> find_nearest: (record, 1.0)   len 1
    RedisStorage    -> find_nearest: None            len 0   keys: cache:tenant:42, tag:t

`_own_record_keys` keeps a SCAN match only when the remainder after
`key_prefix:` has no `:` (D-024, #253), so the record was written and then
invisible. The scan rule stays; `put` now refuses such a key before any write.
"""

from __future__ import annotations

import pytest

from cost_optimizer.semantic_cache import (
    CacheRecord,
    HashEmbedder,
    InMemoryStorage,
    SemanticCache,
)

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import RedisStorage  # noqa: E402


def _record(key: str, tags: frozenset[str] = frozenset({"t"})) -> CacheRecord:
    return CacheRecord(
        key=key, vector=(1.0, 0.0), payload="Paris", tags=tags, expires_at=None, model="m"
    )


@pytest.mark.parametrize("key", ["tenant:42", ":", "a:", ":a", "cache:eu:abc"])
@pytest.mark.parametrize("tags", [frozenset({"t"}), frozenset()], ids=["tagged", "untagged"])
@pytest.mark.parametrize("prefix", ["cache", "tenantA"])
def test_a_colon_key_is_refused_with_nothing_written(
    key: str, tags: frozenset[str], prefix: str
) -> None:
    r = fakeredis.FakeRedis()
    st = RedisStorage(client=r, key_prefix=prefix)
    with pytest.raises(ValueError, match=r"contains ':'.*never found"):
        st.put(_record(key, tags))
    assert r.keys("*") == []
    assert st.find_nearest([1.0, 0.0], model="m") is None
    assert len(st) == 0


def test_the_refusal_names_the_key_and_the_prefix() -> None:
    st = RedisStorage(client=fakeredis.FakeRedis(), key_prefix="eu")
    with pytest.raises(ValueError, match="contains ':'") as exc:
        st.put(_record("tenant:42"))
    assert "'tenant:42'" in str(exc.value)
    assert "'eu:'" in str(exc.value)


def test_a_refused_re_put_leaves_the_existing_record_alone() -> None:
    # The refusal is the first thing `put` does: an existing colon-free record
    # and its tag index are not touched by a later refused put.
    r = fakeredis.FakeRedis()
    st = RedisStorage(client=r)
    st.put(_record("k1"))
    before = sorted(r.keys("*"))
    with pytest.raises(ValueError, match="contains ':'"):
        st.put(_record("k1:x", frozenset({"u"})))
    assert sorted(r.keys("*")) == before
    assert st.invalidate_by_tag("t") == 1


# Controls: green before and after the fix.


@pytest.mark.parametrize("key", ["k1", "tenant-42", "tenant_42", "", "a/b", "*?[]"])
def test_colon_free_storage_level_keys_agree_on_both_backends(key: str) -> None:
    results = []
    for st in (InMemoryStorage(), RedisStorage(client=fakeredis.FakeRedis())):
        st.put(_record(key))
        hit = st.find_nearest([1.0, 0.0], model="m")
        assert hit is not None
        results.append((hit[0].key, hit[0].payload, len(st)))
    assert results[0] == results[1] == (key, "Paris", 1)


def test_semantic_cache_hex_keys_still_round_trip() -> None:
    cache = SemanticCache(
        embedder=HashEmbedder(), storage=RedisStorage(client=fakeredis.FakeRedis())
    )
    cache.put("What is the capital of France?", "Paris", model="m", tags=("geo",))
    assert cache.lookup("What is the capital of France?", model="m").payload == "Paris"
    assert cache.invalidate(tag="geo") == 1
