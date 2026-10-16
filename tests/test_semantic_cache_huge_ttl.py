"""A huge finite `ttl_s` caches on Redis exactly as it does in memory (#323).

`RedisStorage.put` ran `SET`, then computed `ceil((expires_at - now) * 1000)`,
then `PEXPIRE`, then the tag `SADD`s. Redis takes int64 milliseconds, so on main:

    ttl_s=1e16 / sys.maxsize -> PEXPIRE refused (ResponseError) after the SET
    ttl_s=1e306              -> ceil(inf) OverflowError after the SET

and each left the record stored with no expiry and missing from its tag's
index: served by every lookup, and `invalidate(tag=...)` returned 0.
"""

from __future__ import annotations

import sys

import pytest

from cost_optimizer import HashEmbedder, SemanticCache
from cost_optimizer.semantic_cache import InMemoryStorage

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import RedisStorage  # noqa: E402

HUGE = {
    "1e16": 1e16,
    "sys.maxsize": float(sys.maxsize),
    "1e306": 1e306,
    "float-max": sys.float_info.max,
}
QUERY = "how do I refund a charge"


def _backends():  # type: ignore[no-untyped-def]
    return {
        "memory": InMemoryStorage(),
        "redis": RedisStorage(client=fakeredis.FakeRedis()),
    }


@pytest.mark.parametrize("ttl_s", list(HUGE.values()), ids=list(HUGE))
def test_both_backends_put_serve_and_invalidate_it(ttl_s: float) -> None:
    verdicts = {}
    for name, storage in _backends().items():
        cache = SemanticCache(embedder=HashEmbedder(), storage=storage)
        cache.put(QUERY, "answer", model="m", ttl_s=ttl_s, tags=("t",))
        hit_before = cache.lookup(QUERY, model="m").hit
        removed = cache.invalidate(tag="t")
        hit_after = cache.lookup(QUERY, model="m").hit
        verdicts[name] = (hit_before, removed, hit_after)
    assert verdicts == {"memory": (True, 1, False), "redis": (True, 1, False)}


@pytest.mark.parametrize("ttl_s", list(HUGE.values()), ids=list(HUGE))
def test_the_redis_record_carries_a_bounded_positive_expiry(ttl_s: float) -> None:
    client = fakeredis.FakeRedis()
    storage = RedisStorage(client=client)
    cache = SemanticCache(embedder=HashEmbedder(), storage=storage)
    key = cache.put(QUERY, "answer", model="m", ttl_s=ttl_s, tags=("t",))
    pttl = client.pttl(storage._record_key(key))
    assert 0 < pttl <= RedisStorage._MAX_TTL_MS
    assert client.smembers(storage._tag_key("t")) == {key.encode()}


def test_a_ttl_below_the_bound_is_sent_exactly_as_before() -> None:
    # The bound only replaces values Redis would refuse; an ordinary long ttl
    # keeps #264's exact millisecond arithmetic.
    def now() -> float:
        return 1000.0

    client = fakeredis.FakeRedis()
    storage = RedisStorage(client=client, now_fn=now)
    sent: list[int] = []
    real = client.pexpire

    def recording(name, ms, *args, **kwargs):  # type: ignore[no-untyped-def]
        sent.append(ms)
        return real(name, ms, *args, **kwargs)

    client.pexpire = recording
    cache = SemanticCache(embedder=HashEmbedder(), storage=storage, now_fn=now)
    cache.put(QUERY, "answer", model="m", ttl_s=1e12 + 0.0015)
    assert sent == [1_000_000_000_000_002]


def test_the_bound_is_one_redis_accepts() -> None:
    client = fakeredis.FakeRedis()
    assert client.set("probe", "v", px=RedisStorage._MAX_TTL_MS)
    assert client.pttl("probe") > 0
