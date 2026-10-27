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

from collections.abc import Callable
from typing import Any

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


# --- #335: a key under the prefix that this cache did not write ---------------
#
# Measured on `main` 9201786 (fakeredis): `SET cache:session <anything>` made
# every `lookup` raise `binascii.Error: Incorrect padding`, a hash at
# `cache:user42` raised `WRONGTYPE`, and `len()` counted both. `_own_record_keys`
# only skips a remainder with a `:` in it, so a colon-free foreign key got
# through to `_load`, which decoded with no error handling.


def _b64_json(value: object) -> bytes:
    import json
    from binascii import b2a_base64

    return b2a_base64(json.dumps(value).encode()).strip()


_FOREIGN = [
    pytest.param("cache:session", lambda r, k: r.set(k, b"abc"), id="string-bad-padding"),
    pytest.param("cache:counter", lambda r, k: r.set(k, b"1234"), id="string-b64-not-utf8"),
    pytest.param("cache:flag", lambda r, k: r.set(k, b"\xff\xfe"), id="string-not-ascii"),
    pytest.param("cache:j", lambda r, k: r.set(k, _b64_json([1, 2])), id="b64-json-not-a-dict"),
    pytest.param("cache:j", lambda r, k: r.set(k, _b64_json({"a": 1})), id="b64-json-not-a-record"),
    pytest.param("cache:user42", lambda r, k: r.hset(k, "name", "x"), id="hash-wrongtype"),
    pytest.param("cache:members", lambda r, k: r.sadd(k, "x"), id="set-wrongtype"),
    pytest.param("cache:queue", lambda r, k: r.rpush(k, "x"), id="list-wrongtype"),
]


@pytest.mark.parametrize(("foreign", "plant"), _FOREIGN)
def test_a_foreign_key_under_the_prefix_does_not_break_lookup_or_count(
    foreign: str, plant: Callable[[Any, str], object]
) -> None:
    r = fakeredis.FakeRedis()
    cache = _cache(r)
    cache.put(PROMPT, "Madrid", model="m", tags=("geo",))
    plant(r, foreign)
    assert len(cache.storage) == 1
    assert cache.lookup(PROMPT, model="m").payload == "Madrid"
    assert not cache.lookup("something else entirely", model="m").hit
    # `put` over an existing key and `invalidate` both still work around it,
    # and neither touches the foreign key.
    cache.put(PROMPT, "Madrid 2", model="m", tags=("geo",))
    assert cache.invalidate(tag="geo") == 1
    assert len(cache.storage) == 0
    assert r.exists(foreign)


def test_another_caches_tag_set_under_a_colon_free_prefix_is_not_a_record() -> None:
    # The issue's third case needs no foreign app: the default cache's
    # `tag:geography` SET sits under `key_prefix="tag"` with no `:` after it.
    r = fakeredis.FakeRedis()
    default, tagged = _cache(r), _cache(r, key_prefix="tag")
    default.put(PROMPT, "default", model="m", tags=("geography",))
    tagged.put(PROMPT, "tagged", model="m")
    assert len(tagged.storage) == 1
    assert tagged.lookup(PROMPT, model="m").payload == "tagged"
    assert default.lookup(PROMPT, model="m").payload == "default"


def test_a_record_blob_written_for_another_key_is_not_this_keys_record() -> None:
    # A well-formed record copied (or renamed) to a different key would
    # otherwise be served under a key its own `key` field contradicts.
    r = fakeredis.FakeRedis()
    cache = _cache(r)
    cache.put(PROMPT, "x", model="m")
    (own,) = [k for k in r.scan_iter("cache:*")]
    r.set("cache:elsewhere", r.get(own))
    assert len(cache.storage) == 1


@pytest.mark.parametrize(
    "field_value",
    [
        pytest.param(("vector", ["1.0"]), id="vector-str"),
        pytest.param(("tags", "geo"), id="tags-str"),
        pytest.param(("tags", [1]), id="tags-int"),
        pytest.param(("expires_at", "soon"), id="expires-str"),
        pytest.param(("model", 3), id="model-int"),
    ],
)
def test_a_blob_with_a_field_of_the_wrong_shape_is_not_a_record(
    field_value: tuple[str, object],
) -> None:
    r = fakeredis.FakeRedis()
    cache = _cache(r)
    blob = {
        "key": "j",
        "vector": [1.0],
        "payload": "p",
        "tags": [],
        "expires_at": None,
        "model": "m",
    }
    field, value = field_value
    blob[field] = value
    r.set("cache:j", _b64_json(blob))
    assert len(cache.storage) == 0
    assert not cache.lookup(PROMPT, model="m").hit


def test_storage_level_keys_and_pre_125_blobs_still_load() -> None:
    # Controls: no key-name filter (Storage callers use keys like "k1"), and a
    # blob without `model` -- written before #125 -- is still a record.
    from cost_optimizer.semantic_cache import CacheRecord

    r = fakeredis.FakeRedis()
    storage = RedisStorage(client=r)
    storage.put(
        CacheRecord(key="k1", vector=(1.0, 0.0), payload="p", tags=frozenset(), expires_at=None)
    )
    r.set(
        "cache:old",
        _b64_json(
            {"key": "old", "vector": [0.0, 1.0], "payload": "q", "tags": [], "expires_at": None}
        ),
    )
    assert len(storage) == 2
    hit = storage.find_nearest([0.0, 1.0])
    assert hit is not None
    assert hit[0].key == "old"
    assert hit[0].model == ""


def test_a_client_error_other_than_wrongtype_still_propagates() -> None:
    # Only `WRONGTYPE` means "not ours"; a refused read must not look like a miss.
    class Refusing(fakeredis.FakeRedis):  # type: ignore[misc,valid-type]
        def get(self, name: object) -> object:
            raise RuntimeError("NOAUTH Authentication required.")

    r = Refusing()
    cache = _cache(r)
    r.set("cache:k", b"x")
    with pytest.raises(RuntimeError, match="NOAUTH"):
        cache.lookup(PROMPT, model="m")


def test_a_bool_vector_put_wrote_is_still_a_record() -> None:
    # #337: `_validate_embedding` admits a bool component (it is an `int`), so
    # `put` writes one; #335's reader refused it and the Redis cache missed on
    # every lookup while the in-memory one hit.
    import hashlib

    from cost_optimizer.semantic_cache import InMemoryStorage

    class BinaryEmbedder:
        def embed(self, text: str) -> list[float]:
            return [bool(b & 1) for b in hashlib.sha256(text.encode()).digest()]

    for storage in (InMemoryStorage(), RedisStorage(client=fakeredis.FakeRedis())):
        cache = SemanticCache(embedder=BinaryEmbedder(), storage=storage)
        cache.put(PROMPT, "cached", model="m")
        assert cache.lookup(PROMPT, model="m").payload == "cached", type(storage).__name__
        assert len(storage) == 1


# --- #339 (part C): a foreign value under this cache's tag key ---------------
#
# Measured on `main` d231dce: with another app's string at `tag:session`,
# `put(..., tags=("session",))` raised `WRONGTYPE` from the `SADD` AFTER the
# record `SET`. The lookup then hit a record missing from its tag's index, and
# `invalidate(tag="session")` raised too, so nothing could remove it.

_FOREIGN_TAG = [
    pytest.param(lambda r, k: r.set(k, b"x"), id="string"),
    pytest.param(lambda r, k: r.hset(k, "f", "v"), id="hash"),
    pytest.param(lambda r, k: r.rpush(k, "x"), id="list"),
]


@pytest.mark.parametrize("plant", _FOREIGN_TAG)
def test_put_with_a_foreign_tag_key_writes_nothing(plant: Callable[[Any, str], object]) -> None:
    r = fakeredis.FakeRedis()
    cache = _cache(r)
    plant(r, "tag:session")
    with pytest.raises(ValueError, match="tag:session"):
        cache.put(PROMPT, "answer", model="m", tags=("ok", "session"))
    assert not cache.lookup(PROMPT, model="m").hit
    assert len(cache.storage) == 0
    assert not r.exists("tag:ok")  # refused before any write, not halfway


@pytest.mark.parametrize("plant", _FOREIGN_TAG)
def test_invalidate_leaves_a_foreign_tag_key_alone(plant: Callable[[Any, str], object]) -> None:
    r = fakeredis.FakeRedis()
    cache = _cache(r)
    cache.put(PROMPT, "answer", model="m", tags=("geo",))
    plant(r, "tag:session")
    assert cache.invalidate(tag="session") == 0
    assert r.exists("tag:session")
    assert cache.lookup(PROMPT, model="m").hit


def test_a_stale_tag_whose_key_became_foreign_does_not_break_a_re_put() -> None:
    r = fakeredis.FakeRedis()
    cache = _cache(r)
    cache.put(PROMPT, "v1", model="m", tags=("old",))
    r.delete("tag:old")
    r.set("tag:old", b"someone else's")
    cache.put(PROMPT, "v2", model="m", tags=("new",))
    assert cache.lookup(PROMPT, model="m").payload == "v2"
    assert r.get("tag:old") == b"someone else's"
    assert cache.invalidate(tag="new") == 1


def test_own_and_fresh_tag_sets_are_unaffected() -> None:
    # Control: an absent tag key and this cache's own SET both still work.
    r = fakeredis.FakeRedis()
    cache = _cache(r)
    cache.put(PROMPT, "a", model="m", tags=("t",))
    cache.put("another prompt", "b", model="m", tags=("t",))
    assert cache.invalidate(tag="t") == 2
    assert not r.exists("tag:t")


# --- #341: decodes that ran before #335's checks -----------------------------
#
# Measured on `main` 3bdff0c: with a `decode_responses=True` client, a foreign
# binary value raised `UnicodeDecodeError` from `get` itself, outside #335's
# decode guard; and a non-UTF-8 key NAME under the prefix raised from the scan
# in both client modes. Either one broke every `lookup` and `len`.


@pytest.mark.parametrize("decode", [False, True], ids=["bytes-client", "decoding-client"])
@pytest.mark.parametrize(
    "plant",
    [
        pytest.param(lambda r: r.set("cache:session", b"\x80\x04\x95binary"), id="binary-value"),
        pytest.param(lambda r: r.set(b"cache:\xff\xfe", b"x"), id="non-utf8-key-name"),
    ],
)
def test_undecodable_foreign_keys_are_skipped_in_both_client_modes(
    decode: bool, plant: Callable[[Any], object]
) -> None:
    r = fakeredis.FakeRedis(decode_responses=decode)
    cache = _cache(r)
    cache.put(PROMPT, "Madrid", model="m", tags=("geo",))
    plant(r)
    assert cache.lookup(PROMPT, model="m").payload == "Madrid"
    assert len(cache.storage) == 1
    assert cache.invalidate(tag="geo") == 1
    assert len(cache.storage) == 0


def test_a_decoding_client_still_loads_this_caches_own_records() -> None:
    # Control: `decode_responses=True` is a supported client mode.
    r = fakeredis.FakeRedis(decode_responses=True)
    cache = _cache(r)
    cache.put(PROMPT, "Madrid", model="m")
    cache.put("another prompt", "Paris", model="m")
    assert len(cache.storage) == 2
    assert cache.lookup(PROMPT, model="m").payload == "Madrid"


# --- #339: a non-UTF-8 member in a tag SET ------------------------------------
#
# Measured on `main` e22c19f: #341 made the record SCAN skip a non-UTF-8 key
# name, but `invalidate_by_tag` still decoded every `SMEMBERS` member. With
# another app's `b"\xff\xfe"` in `tag:geo`, a `decode_responses=True` client
# raised `UnicodeDecodeError` inside `smembers`, before anything was deleted,
# and the tagged record stayed served; a bytes client raised partway through.
# `InMemoryStorage` does `put ok -> invalidate 2 -> miss`.


@pytest.mark.parametrize("decode", [False, True], ids=["bytes-client", "decoding-client"])
@pytest.mark.parametrize(
    "foreign",
    [
        pytest.param((b"\xff\xfe",), id="non-utf8-member"),
        pytest.param((b"\xff\xfe", b"post:1", b"\x80"), id="mixed-foreign-members"),
        # Control: a valid UTF-8 foreign member was already skipped as stale.
        pytest.param((b"post:1",), id="utf8-member-control"),
        # Control: a SET holding only this cache's members.
        pytest.param((), id="own-members-control"),
    ],
)
def test_invalidate_skips_an_undecodable_tag_member_in_both_client_modes(
    decode: bool, foreign: tuple[bytes, ...]
) -> None:
    r = fakeredis.FakeRedis(decode_responses=decode)
    cache = _cache(r)
    cache.put(PROMPT, "Madrid", model="m", tags=("geo",))
    cache.put("another prompt", "Paris", model="m", tags=("geo",))
    if foreign:
        r.sadd("tag:geo", *foreign)
    assert cache.invalidate(tag="geo") == 2
    assert not cache.lookup(PROMPT, model="m").hit
    assert len(cache.storage) == 0
