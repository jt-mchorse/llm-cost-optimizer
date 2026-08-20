"""A cosine tie resolves on content, not on scan position (#188).

`find_nearest` used a bare `sim > best[1]`, so the first record *scanned* won a
tie. "First" is an iteration order — `dict.values()` for `InMemoryStorage`,
`SCAN` for `RedisStorage` — which is a property of how the cache was written,
not of what it holds.

Ties are ordinary here. `_tokenize` is `text.lower().split()`, so casing and
whitespace runs vanish from the embedding, while `_make_key` hashes the exact
prompt text. Four casings of one question are four distinct records sharing one
vector, every pair at cosine 1.0.

Assertions are anchored to the measured pre-fix observables recorded in #188 —
the served payload, the matched key, the D-007 rate — not to an exception type,
because the pre-fix code raised nothing. It returned a confident wrong answer.
"""

from __future__ import annotations

import itertools

import pytest

from cost_optimizer.semantic_cache import (
    HashEmbedder,
    InMemoryStorage,
    SemanticCache,
    cosine,
    measure_false_positive_rate,
)

MODEL = "claude-opus-4"
QUERY = "What is our refund policy?"

# Four casing/whitespace variants of one question: four distinct `_make_key`
# digests, one identical embedding.
VARIANTS = [
    "What is our refund policy?",
    "what is our refund policy?",
    "What  is  our  refund  policy?",
    "WHAT IS OUR REFUND POLICY?",
]

PERMUTATIONS = list(itertools.permutations(VARIANTS))


def _cache(order, storage=None) -> SemanticCache:
    cache = SemanticCache(embedder=HashEmbedder(), storage=storage or InMemoryStorage())
    for prompt in order:
        cache.put(prompt, f"ANSWER::{prompt}", model=MODEL)
    return cache


class TestThePremise:
    """The tie has to be real before the tiebreak means anything."""

    def test_the_variants_share_one_embedding(self) -> None:
        emb = HashEmbedder()
        vectors = [emb.embed(f"[model={MODEL}] {v}") for v in VARIANTS]
        for other in vectors[1:]:
            assert cosine(vectors[0], other) == pytest.approx(1.0)

    def test_the_variants_are_distinct_records(self) -> None:
        cache = _cache(VARIANTS)
        keys = {cache._make_key(v, MODEL) for v in VARIANTS}
        assert len(keys) == 4, "the variants must be four separate cache entries"
        assert len(cache.storage) == 4


class TestLookupIsInsertionOrderIndependent:
    def test_one_payload_across_all_24_orders(self) -> None:
        outcomes = set()
        for perm in PERMUTATIONS:
            res = _cache(perm).lookup(QUERY, model=MODEL)
            outcomes.add((res.hit, res.payload, res.matched_record_key))
        assert len(outcomes) == 1, (
            f"lookup served {len(outcomes)} different results across the 24 insertion "
            "orders of four tied records. Pre-fix: 4 distinct payloads, every one a "
            "hit at similarity 1.0."
        )
        ((hit, _payload, _key),) = outcomes
        assert hit is True
        assert next(iter(outcomes))[1] is not None

    def test_similarity_is_still_reported_as_one(self) -> None:
        # The tiebreak must not perturb the reported metric — only the choice.
        res = _cache(VARIANTS).lookup(QUERY, model=MODEL)
        assert res.similarity == pytest.approx(1.0)

    def test_the_winner_is_the_greatest_key(self) -> None:
        """Pins the direction so a future flip is deliberate, not accidental."""
        cache = _cache(VARIANTS)
        expected = max(cache._make_key(v, MODEL) for v in VARIANTS)
        assert cache.lookup(QUERY, model=MODEL).matched_record_key == expected


class TestSimilarityStillOutranksTheTiebreak:
    def test_a_strictly_better_record_wins_regardless_of_key_order(self) -> None:
        """Guards against a fix that sorts by key first.

        The exact-match record is deliberately the one whose key is *smaller*
        (`250a127a…` vs `c0d04e2c…`), so a comparison that put `key` ahead of
        `sim` would serve the worse match. The fixture assertion below is not
        decoration — my first attempt at this test picked a `near` prompt whose
        key happened to sort the other way, which would have made the whole
        thing vacuous, and the assertion is what caught it.
        """
        near = "What is our refund policy for annual plans?"
        cache = SemanticCache(embedder=HashEmbedder(), storage=InMemoryStorage())

        exact_key = cache._make_key(QUERY, MODEL)
        near_key = cache._make_key(near, MODEL)
        assert exact_key < near_key, (
            "fixture assumption broken: this test is only meaningful when the "
            "better-matching record has the smaller key"
        )

        cache.put(near, "WRONG", model=MODEL)
        cache.put(QUERY, "RIGHT", model=MODEL)
        res = cache.lookup(QUERY, model=MODEL)
        assert res.hit is True
        assert res.payload == "RIGHT"
        assert res.matched_record_key == exact_key
        assert res.similarity == pytest.approx(1.0)


class TestD007FalsePositiveRateIsReproducible:
    """The measured artifact: the offline rate D-007 exists to produce."""

    OLD = "30-day window, no exceptions"
    NEW = "60-day window for pro plans"

    def _entries(self):
        return [
            (VARIANTS[0], self.OLD),
            (VARIANTS[1], self.OLD),
            (VARIANTS[2], self.NEW),
            (VARIANTS[3], self.NEW),
        ]

    def test_one_rate_across_all_24_orders(self) -> None:
        rates = set()
        for perm in itertools.permutations(self._entries()):
            cache = SemanticCache(embedder=HashEmbedder(), storage=InMemoryStorage())
            for prompt, payload in perm:
                cache.put(prompt, payload, model=MODEL)
            rate, _ = measure_false_positive_rate(
                cache, [(QUERY, "")], model=MODEL, call_model=lambda p: self.NEW
            )
            rates.add(rate)
        assert len(rates) == 1, (
            f"measure_false_positive_rate returned {sorted(rates)} across the 24 "
            "insertion orders of identical cache contents. Pre-fix: 0.00 for 12 "
            "orders and 1.00 for the other 12."
        )

    def test_the_rate_still_moves_when_the_cache_is_actually_wrong(self) -> None:
        """Anti-vacuous in the other direction: the metric must still measure.

        A rate that is stable because it is always 0.0 would satisfy the test
        above and be useless. Every entry here holds the stale answer, so a
        correctly-working measurement must report a false positive.
        """
        cache = SemanticCache(embedder=HashEmbedder(), storage=InMemoryStorage())
        for v in VARIANTS:
            cache.put(v, self.OLD, model=MODEL)
        rate, samples = measure_false_positive_rate(
            cache, [(QUERY, "")], model=MODEL, call_model=lambda p: self.NEW
        )
        assert rate == pytest.approx(1.0)
        assert samples[0].is_false_positive is True


class TestBackendParityOnTies:
    """Cross-backend agreement is already an asserted property of this module."""

    @staticmethod
    def _redis_cache(order):
        fakeredis = pytest.importorskip("fakeredis")
        from cost_optimizer.semantic_cache import RedisStorage

        return _cache(order, storage=RedisStorage(client=fakeredis.FakeRedis()))

    def test_both_backends_pick_the_same_record(self) -> None:
        mem = _cache(VARIANTS).lookup(QUERY, model=MODEL)
        red = self._redis_cache(VARIANTS).lookup(QUERY, model=MODEL)
        assert mem.matched_record_key == red.matched_record_key, (
            f"in-memory chose {mem.matched_record_key}, Redis chose "
            f"{red.matched_record_key}. Pre-fix, on this same insertion order: "
            "250a127aab1750fb vs 1a5ec8281b4bf5ff."
        )
        assert mem.payload == red.payload

    def test_both_backends_agree_across_every_insertion_order(self) -> None:
        keys = set()
        for perm in PERMUTATIONS:
            keys.add(_cache(perm).lookup(QUERY, model=MODEL).matched_record_key)
            keys.add(self._redis_cache(perm).lookup(QUERY, model=MODEL).matched_record_key)
        assert len(keys) == 1, f"backends and orders produced {sorted(keys)}"
