"""RedisStorage refuses a key or tag prefix Redis cannot encode, at construction (#291).

A prefix holding a lone surrogate constructed fine, then every operation --
the first lookup included -- failed with a bare UnicodeEncodeError from the
client. Measured on main with fakeredis: `key_prefix="tenant-" + chr(0xD800)`
constructed, and `put` raised UnicodeEncodeError.
"""

from __future__ import annotations

import pytest

from cost_optimizer.semantic_cache import HashEmbedder, SemanticCache

fakeredis = pytest.importorskip("fakeredis")

from cost_optimizer.semantic_cache import RedisStorage  # noqa: E402


class _NoCalls:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the client must not be used: {name}")


@pytest.mark.parametrize("field", ["key_prefix", "tag_prefix"])
@pytest.mark.parametrize(
    "bad", ["tenant-" + chr(0xD800), chr(0xDCFF), "a" + chr(0xDFFF) + "b"], ids=ascii
)
def test_an_unencodable_prefix_is_refused_before_the_client_is_used(field: str, bad: str) -> None:
    with pytest.raises(ValueError, match=rf"^{field} .* cannot be encoded as UTF-8"):
        RedisStorage(client=_NoCalls(), **{field: bad})


@pytest.mark.parametrize("prefix", ["租户", "café", "tenant-\U0001f600"])
def test_a_non_ascii_prefix_still_works(prefix: str) -> None:
    cache = SemanticCache(
        embedder=HashEmbedder(),
        storage=RedisStorage(client=fakeredis.FakeRedis(), key_prefix=prefix),
    )
    cache.put("What is the refund policy?", "answer", model="m", tags=("t",))
    assert cache.lookup("What is the refund policy?", model="m").hit
    assert cache.invalidate(tag="t") == 1
