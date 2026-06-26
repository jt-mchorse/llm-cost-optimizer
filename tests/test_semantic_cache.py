"""Tests for the semantic cache.

Three layers covered hermetically:
1. Math (cosine), embedder (HashEmbedder), and InMemoryStorage — pure unit tests.
2. SemanticCache orchestration: hit/miss boundary, TTL, tag invalidation,
   model-scoped keys, false-positive measurement helper.
3. RedisStorage parity against the in-memory backend, exercised against
   `fakeredis` so CI doesn't need a Redis server.
"""

from __future__ import annotations

import math

import pytest

from cost_optimizer.semantic_cache import (
    CacheRecord,
    HashEmbedder,
    InMemoryStorage,
    SemanticCache,
    cosine,
    measure_false_positive_rate,
)

# ----------------------------------------------------------------------
# cosine
# ----------------------------------------------------------------------


def test_cosine_identical_is_one():
    assert cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_anti_parallel_is_negative_one():
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_returns_zero():
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_length_mismatch_raises():
    with pytest.raises(ValueError, match="length mismatch"):
        cosine([1.0], [1.0, 2.0])


# Issue #87: defense-in-depth. find_nearest scans every record through cosine,
# so a non-finite *result* (huge-but-finite components overflow sum(x*x) to inf)
# must return a finite 0.0 fallback, not a nan that poisons find_nearest's best.
def test_cosine_overflow_returns_finite_zero_not_nan():
    huge = [1e200, 1e200]
    sim = cosine(huge, huge)
    assert math.isfinite(sim)
    assert sim == 0.0


# ----------------------------------------------------------------------
# HashEmbedder
# ----------------------------------------------------------------------


def test_hash_embedder_is_deterministic():
    e = HashEmbedder()
    assert e.embed("the quick brown fox") == e.embed("the quick brown fox")


def test_hash_embedder_returns_unit_vector():
    e = HashEmbedder()
    v = e.embed("hello world")
    norm = sum(x * x for x in v) ** 0.5
    assert norm == pytest.approx(1.0)


def test_hash_embedder_similar_inputs_have_high_similarity():
    e = HashEmbedder(ngram=2)
    a = e.embed("how do I refund a charge")
    b = e.embed("how do I refund the charge")  # one word changed
    assert cosine(a, b) > 0.5


def test_hash_embedder_different_inputs_have_lower_similarity():
    e = HashEmbedder(ngram=2)
    a = e.embed("how do I refund a charge")
    b = e.embed("what is the weather today")
    assert cosine(a, b) < 0.5


def test_hash_embedder_empty_returns_nonzero_vector():
    e = HashEmbedder()
    v = e.embed("")
    assert any(x != 0 for x in v)


def test_hash_embedder_rejects_negative_ngram():
    # Extended in #40 to the portfolio positive-integer contract; the
    # original "must be >= 1" wording was tightened to "must be a positive
    # integer".
    with pytest.raises(ValueError, match="ngram must be a positive integer"):
        HashEmbedder(ngram=0)


def test_hash_embedder_rejects_non_string():
    with pytest.raises(TypeError):
        HashEmbedder().embed(42)  # type: ignore[arg-type]


# Issue #40: completes the portfolio's HashEmbedder sweep. Sign-only
# `ngram < 1` accepted bool (silently bound, unigram embedding silently
# degraded cache hit-rate — the worst harm class for this repo's purpose),
# float (silently bound, then `_ngrams` raised TypeError deep in the
# call chain), and NaN/Inf (silently bound, range/overflow errors at
# embed time). Mirrors rag-production-kit#43, embedding-model-shootout#36,
# and prompt-regression-suite#38.


@pytest.mark.parametrize(
    "bad_ngram",
    [
        True,
        False,
        0,
        -1,
        -2,
        0.5,
        1.5,
        2.0,
        math.nan,
        math.inf,
        -math.inf,
        None,
        "2",
        [2],
        (2,),
    ],
)
def test_hash_embedder_rejects_non_positive_int_ngram(bad_ngram):
    with pytest.raises(ValueError, match="ngram must be a positive integer"):
        HashEmbedder(ngram=bad_ngram)


@pytest.mark.parametrize("good_ngram", [1, 2, 3, 5, 100])
def test_hash_embedder_accepts_positive_int_ngram(good_ngram):
    e = HashEmbedder(ngram=good_ngram)
    assert e.ngram == good_ngram


def test_hash_embedder_default_ngram_unchanged():
    # Default constructor path stays bound to 2; no behaviour change for
    # the 99% case.
    e = HashEmbedder()
    assert e.ngram == 2


# ----------------------------------------------------------------------
# InMemoryStorage
# ----------------------------------------------------------------------


def _record(key: str, vector: list[float], **kwargs):
    return CacheRecord(
        key=key,
        vector=tuple(vector),
        payload=kwargs.get("payload", "p"),
        tags=frozenset(kwargs.get("tags", ())),
        expires_at=kwargs.get("expires_at"),
    )


def test_inmemory_find_nearest_on_empty_returns_none():
    s = InMemoryStorage()
    assert s.find_nearest([1.0, 0.0]) is None


def test_inmemory_find_nearest_returns_highest_similarity():
    s = InMemoryStorage()
    s.put(_record("a", [1.0, 0.0]))
    s.put(_record("b", [0.0, 1.0]))
    s.put(_record("c", [0.99, 0.01]))
    best = s.find_nearest([1.0, 0.0])
    assert best is not None
    record, sim = best
    assert record.key == "a"
    assert sim == pytest.approx(1.0)


def test_inmemory_invalidate_by_tag():
    s = InMemoryStorage()
    s.put(_record("a", [1.0, 0.0], tags=("legal",)))
    s.put(_record("b", [0.0, 1.0], tags=("legal", "urgent")))
    s.put(_record("c", [0.0, 1.0], tags=("urgent",)))
    n = s.invalidate_by_tag("legal")
    assert n == 2
    assert len(s) == 1


def test_inmemory_purge_expired():
    s = InMemoryStorage()
    s.put(_record("a", [1.0], expires_at=10.0))
    s.put(_record("b", [1.0], expires_at=20.0))
    s.put(_record("c", [1.0], expires_at=None))
    n = s.purge_expired(now=15.0)
    assert n == 1
    assert len(s) == 2


# ----------------------------------------------------------------------
# SemanticCache orchestration
# ----------------------------------------------------------------------


def _cache(threshold=0.95, ttl=None, now=None):
    fake_now = [1000.0]
    if now is not None:
        fake_now[0] = now

    def now_fn():
        return fake_now[0]

    cache = SemanticCache(
        embedder=HashEmbedder(),
        storage=InMemoryStorage(),
        similarity_threshold=threshold,
        default_ttl_s=ttl,
        now_fn=now_fn,
    )
    return cache, fake_now


def test_lookup_on_empty_returns_miss():
    cache, _ = _cache()
    result = cache.lookup("anything", model="claude-haiku-4-5")
    assert result.hit is False
    assert result.payload is None
    assert cache.stats.misses == 1
    assert cache.stats.hits == 0


def test_put_then_exact_lookup_hits():
    cache, _ = _cache()
    cache.put("how do I refund a charge", "answer-A", model="claude-haiku-4-5")
    result = cache.lookup("how do I refund a charge", model="claude-haiku-4-5")
    assert result.hit is True
    assert result.payload == "answer-A"
    assert result.similarity == pytest.approx(1.0)


def test_put_then_near_match_lookup_hits_at_threshold():
    cache, _ = _cache(threshold=0.7)
    cache.put("how do I refund a charge", "answer-A", model="claude-haiku-4-5")
    # One-word change ("charge" → "purchase") should still hit at the lower threshold.
    result = cache.lookup("how do I refund a purchase", model="claude-haiku-4-5")
    assert result.hit is True
    assert result.payload == "answer-A"


def test_clearly_different_lookup_misses():
    cache, _ = _cache(threshold=0.5)
    cache.put("how do I refund a charge", "answer-A", model="claude-haiku-4-5")
    result = cache.lookup("what is the weather today", model="claude-haiku-4-5")
    assert result.hit is False
    assert result.payload is None
    # Similarity returned in the result for telemetry, even on miss.
    assert 0.0 <= result.similarity < 0.5


def test_model_scoped_keys_isolate_per_model():
    cache, _ = _cache()
    cache.put("how do I refund a charge", "answer-haiku", model="claude-haiku-4-5")
    # Same prompt but different model → does NOT hit (D-005).
    result = cache.lookup("how do I refund a charge", model="claude-opus-4-7")
    assert result.hit is False


# Issue #98: the degenerate-input branch of HashEmbedder (fewer tokens than the
# n-gram width) returned a *constant* vector, so every empty/whitespace prompt —
# and at ngram>=3 every single-word prompt — collided at cosine 1.0 regardless
# of model id or content. That silently defeated model-scoping (D-005) and
# served a false-positive cache hit (D-006/D-007). The slot is now seeded from a
# hash of the full scoped text, so only a genuinely identical degenerate input
# collides.
def test_degenerate_empty_prompt_does_not_hit_across_models():
    cache, _ = _cache()
    cache.put("", "response-for-empty", model="m")
    # Whitespace-only prompt under a DIFFERENT model must miss, not return
    # model "m"'s cached response.
    result = cache.lookup("   ", model="other")
    assert result.hit is False


def test_degenerate_empty_prompt_same_model_still_hits():
    # Determinism preserved: an identical degenerate input under the same model
    # is still a legitimate hit (the same empty prompt should reuse its entry).
    cache, _ = _cache()
    cache.put("", "response-for-empty", model="m")
    result = cache.lookup("", model="m")
    assert result.hit is True
    assert result.payload == "response-for-empty"


def test_degenerate_single_word_does_not_hit_across_models_at_ngram3():
    # At ngram=3 a single-word prompt is degenerate (2 tokens incl. the model
    # prefix < 3). Pre-fix this returned model A's "hello" response for a
    # model-B "world" lookup — wrong model AND wrong content.
    cache = SemanticCache(embedder=HashEmbedder(ngram=3), storage=InMemoryStorage())
    cache.put("hello", "resp-hello-modelA", model="A")
    assert cache.lookup("world", model="B").hit is False
    # Same word + same model still hits.
    assert cache.lookup("hello", model="A").hit is True


def test_hash_embedder_degenerate_vectors_differ_by_model_and_content():
    # Embedder-level: distinct degenerate (scoped) inputs are no longer the
    # same constant vector, while identical inputs still match.
    e = HashEmbedder()
    v_m = e.embed("[model=m] ")
    v_other = e.embed("[model=other] ")
    assert cosine(v_m, v_other) < 0.95  # distinct model → not a hit
    assert cosine(v_m, e.embed("[model=m] ")) == pytest.approx(1.0)  # identical → match
    # Still a well-defined unit vector.
    assert math.sqrt(sum(x * x for x in v_m)) == pytest.approx(1.0)


def test_default_ttl_expires_entries():
    cache, fake_now = _cache(ttl=60.0, now=1000.0)
    cache.put("p", "v", model="m")
    assert cache.lookup("p", model="m").hit is True
    fake_now[0] = 1100.0  # 100s later, well past 60s TTL
    assert cache.lookup("p", model="m").hit is False
    assert cache.stats.expired_purged >= 1


def test_per_call_ttl_overrides_default():
    cache, fake_now = _cache(ttl=10.0, now=1000.0)
    cache.put("p", "v", model="m", ttl_s=120.0)
    fake_now[0] = 1050.0  # past default TTL but inside per-call TTL
    assert cache.lookup("p", model="m").hit is True


def test_no_ttl_means_no_expiry():
    cache, fake_now = _cache(ttl=None, now=1000.0)
    cache.put("p", "v", model="m")
    fake_now[0] = 1e9
    assert cache.lookup("p", model="m").hit is True


def test_invalidate_by_tag_drops_records():
    cache, _ = _cache()
    cache.put("p1", "v1", model="m", tags=("legal",))
    cache.put("p2", "v2", model="m", tags=("legal", "urgent"))
    cache.put("p3", "v3", model="m", tags=("urgent",))
    n = cache.invalidate(tag="legal")
    assert n == 2
    # The non-tagged-legal entry survives.
    assert cache.lookup("p3", model="m").hit is True
    # The two tagged-legal entries are gone.
    assert cache.lookup("p1", model="m").hit is False
    assert cache.lookup("p2", model="m").hit is False


def test_threshold_bounds_validated():
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        SemanticCache(embedder=HashEmbedder(), storage=InMemoryStorage(), similarity_threshold=0.0)
    with pytest.raises(ValueError, match=r"\(0, 1\]"):
        SemanticCache(embedder=HashEmbedder(), storage=InMemoryStorage(), similarity_threshold=1.1)


def test_ttl_validated_positive():
    with pytest.raises(ValueError, match="positive"):
        SemanticCache(
            embedder=HashEmbedder(),
            storage=InMemoryStorage(),
            similarity_threshold=0.9,
            default_ttl_s=0,
        )


# Issue #36: extend default_ttl_s sign-only check to finiteness. NaN ttl
# would store as expires_at=now+NaN=NaN; every subsequent now<expires_at
# check is false → every entry reads as expired → cache silently bypassed.
@pytest.mark.parametrize(
    "bad",
    [float("nan"), float("inf"), float("-inf")],
)
def test_ttl_rejects_non_finite(bad: float):
    with pytest.raises(ValueError, match="finite positive number"):
        SemanticCache(
            embedder=HashEmbedder(),
            storage=InMemoryStorage(),
            similarity_threshold=0.9,
            default_ttl_s=bad,
        )


# Issue #85: the per-call put(ttl_s=...) override takes precedence over
# default_ttl_s but was unvalidated — a negative ttl stored expires_at in the
# past (entry silently evicted on next lookup) and a non-finite ttl corrupted
# expires_at. Apply the same guard as the constructor at this seam.
def test_put_rejects_non_positive_ttl_s():
    cache, _ = _cache()
    with pytest.raises(ValueError, match="finite positive number"):
        cache.put("p", "v", model="m", ttl_s=0)
    with pytest.raises(ValueError, match="finite positive number"):
        cache.put("p", "v", model="m", ttl_s=-10.0)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_put_rejects_non_finite_ttl_s(bad: float):
    cache, _ = _cache()
    with pytest.raises(ValueError, match="finite positive number"):
        cache.put("p", "v", model="m", ttl_s=bad)


def test_put_ttl_s_none_falls_back_to_default():
    # ttl_s=None must still defer to default_ttl_s (here: no expiry).
    cache, _ = _cache(ttl=None)
    cache.put("p", "v", model="m", ttl_s=None)
    assert cache.lookup("p", model="m").hit is True


def test_put_valid_positive_ttl_s_stores_and_retrieves():
    cache, fake_now = _cache(ttl=None)
    cache.put("p", "v", model="m", ttl_s=60.0)
    assert cache.lookup("p", model="m").hit is True
    # Still present just before expiry, gone after.
    fake_now[0] += 59.0
    assert cache.lookup("p", model="m").hit is True
    fake_now[0] += 2.0
    assert cache.lookup("p", model="m").hit is False


def test_stats_track_hit_rate():
    cache, _ = _cache()
    cache.put("p", "v", model="m")
    cache.lookup("p", model="m")  # hit
    cache.lookup("nothing", model="m")  # miss against existing entry; depends on threshold
    # Stats correct regardless.
    assert cache.stats.hits + cache.stats.misses >= 2
    assert 0.0 <= cache.stats.hit_rate <= 1.0


# ----------------------------------------------------------------------
# False-positive measurement helper (D-007)
# ----------------------------------------------------------------------


def test_false_positive_rate_zero_when_cache_agrees_with_model():
    cache, _ = _cache()
    cache.put("greet", "Hello!", model="m")

    rate, samples = measure_false_positive_rate(
        cache,
        held_out=[("greet", "")],
        model="m",
        call_model=lambda p: "Hello!",  # model agrees with cached
    )
    assert rate == 0.0
    assert len(samples) == 1
    assert samples[0].is_false_positive is False


def test_false_positive_rate_one_when_cache_disagrees_always():
    cache, _ = _cache()
    cache.put("p1", "old", model="m")
    cache.put("p2", "old", model="m")
    rate, samples = measure_false_positive_rate(
        cache,
        held_out=[("p1", ""), ("p2", "")],
        model="m",
        call_model=lambda p: "new",
    )
    assert rate == 1.0
    assert all(s.is_false_positive for s in samples)


def test_false_positive_rate_skips_misses():
    cache, _ = _cache()
    rate, samples = measure_false_positive_rate(
        cache,
        held_out=[("never-seen", "")],
        model="m",
        call_model=lambda p: pytest.fail("should not be called on a miss"),
    )
    assert rate == 0.0
    assert samples == []


def test_false_positive_rate_supports_custom_equality():
    cache, _ = _cache()
    cache.put("p", "Hello, world.", model="m")
    # Custom equality: case-insensitive substring containment.
    rate, _ = measure_false_positive_rate(
        cache,
        held_out=[("p", "")],
        model="m",
        call_model=lambda _p: "hello, World.",
        equality=lambda a, b: a.lower() == b.lower(),
    )
    assert rate == 0.0


# ----------------------------------------------------------------------
# RedisStorage parity (uses fakeredis if installed, else skipped)
# ----------------------------------------------------------------------


@pytest.fixture
def fake_redis_client():
    try:
        import fakeredis  # type: ignore[import-not-found]
    except ImportError:
        pytest.skip("fakeredis not installed; skipping RedisStorage parity tests")
    return fakeredis.FakeRedis()


def test_redis_storage_roundtrip(fake_redis_client):
    from cost_optimizer.semantic_cache import RedisStorage

    s = RedisStorage(client=fake_redis_client)
    s.put(_record("a", [1.0, 0.0], payload={"answer": "A"}, tags=("legal",)))
    best = s.find_nearest([1.0, 0.0])
    assert best is not None
    record, sim = best
    assert record.key == "a"
    assert record.payload == {"answer": "A"}
    assert "legal" in record.tags
    assert sim == pytest.approx(1.0)
    assert len(s) == 1


def test_redis_storage_invalidate_by_tag(fake_redis_client):
    from cost_optimizer.semantic_cache import RedisStorage

    s = RedisStorage(client=fake_redis_client)
    s.put(_record("a", [1.0, 0.0], tags=("legal",)))
    s.put(_record("b", [0.0, 1.0], tags=("legal", "urgent")))
    s.put(_record("c", [0.0, 1.0], tags=("urgent",)))
    n = s.invalidate_by_tag("legal")
    assert n == 2
    assert len(s) == 1


def test_redis_storage_reput_with_changed_tags_drops_stale_membership(fake_redis_client):
    # Re-putting an existing key with a different tag set (reclassification)
    # must not leave the old tag pointing at the key. Pre-fix the additive
    # `sadd` left `tag:legal -> a` in place, so invalidating "legal" wrongly
    # evicted the record even though it's now tagged only "urgent". This
    # mirrors InMemoryStorage, which replaces the whole record on re-put.
    from cost_optimizer.semantic_cache import RedisStorage

    s = RedisStorage(client=fake_redis_client)
    s.put(_record("a", [1.0, 0.0], tags=("legal",)))
    s.put(_record("a", [1.0, 0.0], tags=("urgent",)))  # reclassified; legal dropped
    # The entry is no longer tagged "legal" — invalidating "legal" must skip it.
    assert s.invalidate_by_tag("legal") == 0
    assert len(s) == 1
    # The current tag still invalidates it.
    assert s.invalidate_by_tag("urgent") == 1
    assert len(s) == 0


def test_semantic_cache_with_redis_backend_behaves_like_inmemory(fake_redis_client):
    from cost_optimizer.semantic_cache import RedisStorage

    cache = SemanticCache(
        embedder=HashEmbedder(),
        storage=RedisStorage(client=fake_redis_client),
        similarity_threshold=0.9,
    )
    cache.put("how do I refund a charge", "answer-A", model="m", tags=("legal",))
    assert cache.lookup("how do I refund a charge", model="m").hit is True
    assert cache.invalidate(tag="legal") == 1
    assert cache.lookup("how do I refund a charge", model="m").hit is False


# ----------------------------------------------------------------------
# Issue #87: reject a non-finite embedding component at the BYO-embedder
# seam. Unvalidated, a NaN/Inf component makes every cosine similarity NaN
# (nan >= threshold is always False), so an identical prompt is reported as a
# miss — the cache silently goes fully bypassed and hit_rate reads 0.
# ----------------------------------------------------------------------


class _StubEmbedder:
    """BYO embedder that returns a fixed vector (mimics a corrupt model output)."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed(self, text: str) -> list[float]:
        return list(self._vector)


def _cache_with(embedder) -> SemanticCache:
    return SemanticCache(embedder=embedder, storage=InMemoryStorage(), similarity_threshold=0.95)


@pytest.mark.parametrize(
    "bad_vec",
    [
        [0.6, float("nan"), 0.8],
        [float("inf"), 0.2, 0.1],
        [0.1, 0.2, float("-inf")],
    ],
    ids=["nan", "inf", "-inf"],
)
def test_put_rejects_non_finite_embedding(bad_vec: list[float]):
    cache = _cache_with(_StubEmbedder(bad_vec))
    with pytest.raises(ValueError, match="non-finite component"):
        cache.put("p", "v", model="m")


@pytest.mark.parametrize(
    "bad_vec",
    [
        [0.6, float("nan"), 0.8],
        [float("inf"), 0.2, 0.1],
        [0.1, 0.2, float("-inf")],
    ],
    ids=["nan", "inf", "-inf"],
)
def test_lookup_rejects_non_finite_embedding(bad_vec: list[float]):
    cache = _cache_with(_StubEmbedder(bad_vec))
    with pytest.raises(ValueError, match="non-finite component"):
        cache.lookup("p", model="m")


def test_finite_byo_embedder_still_hits_on_identical_prompt():
    # Regression guard: the seam validation must not break a legitimate finite
    # embedder — an identical prompt must still be a hit with similarity 1.0.
    cache = _cache_with(_StubEmbedder([0.6, 0.0, 0.8]))
    cache.put("how do I refund a charge", "answer-A", model="m")
    res = cache.lookup("how do I refund a charge", model="m")
    assert res.hit is True
    assert res.payload == "answer-A"
    assert res.similarity == pytest.approx(1.0)
    assert cache.stats.hit_rate == pytest.approx(1.0)
