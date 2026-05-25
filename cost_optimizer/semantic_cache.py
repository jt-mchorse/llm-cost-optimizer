"""Semantic response cache.

A response cache keyed by **embedding similarity** rather than exact-match on
the prompt string. Two semantically-equivalent prompts (different surface form,
same intent) hit the same cache entry; two different prompts that happen to
share keywords don't.

The cache is two pluggable Protocols (D-004) plus a small orchestration layer:

- `Embedder` turns a string into a vector.
- `Storage` persists `{key, vector, payload, tags, expiry}` records and
  supports nearest-vector lookup, TTL-based expiry, and tag-based
  invalidation.
- `SemanticCache` composes them: lookup embeds the request, finds the
  highest-similarity stored vector, returns the payload iff cosine
  similarity ≥ threshold (default 0.95 — D-006, conservative on purpose).

Cache keys include the **model id** (D-005). The same prompt to two different
models is two cache entries; a model upgrade invalidates the cache for the
entries it touches without forcing a full flush.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

# ----------------------------------------------------------------------
# Embedder Protocol + dep-free reference
# ----------------------------------------------------------------------


class Embedder(Protocol):
    """Single-method seam for swapping embedding backends."""

    def embed(self, text: str) -> list[float]:
        """Return a unit-length embedding vector for `text`."""


HASH_EMBEDDING_DIM = 128


class HashEmbedder:
    """Deterministic hash-based embedder. Dep-free, hermetic.

    Token-bag projection into a 128-dim space via SHA-256 hashing of token
    n-grams. Not for production retrieval; production callers BYO via the
    `Embedder` Protocol (Cohere, Voyage, OpenAI, sentence-transformers all
    conform with a one-line wrapper).

    The point of this class is to let CI exercise the cache flow end-to-end
    without an embeddings API. Two near-identical prompts produce highly
    similar vectors; clearly different prompts produce dissimilar ones.
    """

    def __init__(self, *, ngram: int = 2) -> None:
        if ngram < 1:
            raise ValueError("ngram must be >= 1")
        self.ngram = ngram

    def embed(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise TypeError("text must be a str")
        tokens = _tokenize(text)
        vec = [0.0] * HASH_EMBEDDING_DIM
        # Bag-of-n-grams: for each n-gram, hash to a slot and increment.
        # Slot is taken from the first 8 bytes of SHA-256.
        ngrams = _ngrams(tokens, self.ngram)
        if not ngrams:
            # Degenerate input: empty string or no tokens. Return a constant
            # non-zero vector so similarity comparisons stay well-defined.
            vec[0] = 1.0
            return vec
        for ng in ngrams:
            h = hashlib.sha256(ng.encode("utf-8")).digest()
            slot = int.from_bytes(h[:4], "big") % HASH_EMBEDDING_DIM
            vec[slot] += 1.0
        # L2-normalize.
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def _tokenize(text: str) -> list[str]:
    return [t for t in text.lower().split() if t]


def _ngrams(tokens: list[str], n: int) -> list[str]:
    if n == 1:
        return list(tokens)
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


# ----------------------------------------------------------------------
# Storage Protocol + in-memory + Redis
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CacheRecord:
    """One stored cache entry."""

    key: str  # synthetic id; not the prompt
    vector: tuple[float, ...]
    payload: Any
    tags: frozenset[str]
    expires_at: float | None  # unix epoch; None = no expiry


class Storage(Protocol):
    """Persistence + nearest-vector + tag-membership operations."""

    def put(self, record: CacheRecord) -> None: ...
    def find_nearest(self, vector: list[float]) -> tuple[CacheRecord, float] | None:
        """Return (best_record, best_similarity) or None if store is empty."""

    def invalidate_by_tag(self, tag: str) -> int:
        """Drop every record tagged `tag`. Return count dropped."""

    def purge_expired(self, now: float) -> int:
        """Drop expired records; return count dropped."""

    def __len__(self) -> int: ...


class InMemoryStorage:
    """Dep-free, deterministic Storage implementation.

    Linear scan on lookup (fine for the cache sizes this layer is sized for —
    ~10k entries at most before you're rebuilding as a vector index anyway).
    For larger caches use `RedisStorage` or wire your own backend.
    """

    def __init__(self) -> None:
        self._records: dict[str, CacheRecord] = {}

    def put(self, record: CacheRecord) -> None:
        self._records[record.key] = record

    def find_nearest(self, vector: list[float]) -> tuple[CacheRecord, float] | None:
        if not self._records:
            return None
        best: tuple[CacheRecord, float] | None = None
        for r in self._records.values():
            sim = cosine(vector, list(r.vector))
            if best is None or sim > best[1]:
                best = (r, sim)
        return best

    def invalidate_by_tag(self, tag: str) -> int:
        to_drop = [k for k, r in self._records.items() if tag in r.tags]
        for k in to_drop:
            del self._records[k]
        return len(to_drop)

    def purge_expired(self, now: float) -> int:
        to_drop = [
            k for k, r in self._records.items() if r.expires_at is not None and r.expires_at <= now
        ]
        for k in to_drop:
            del self._records[k]
        return len(to_drop)

    def __len__(self) -> int:
        return len(self._records)


class RedisStorage:
    """Redis-backed Storage. Lazy-imports the `redis` SDK.

    Records live as Redis hashes at `cache:<key>`; tag membership lives as
    Redis SETs at `tag:<name>`. Native Redis TTL bounds memory growth even
    if `purge_expired` is never called.

    Linear scan on lookup is implemented over Redis SCAN (cursor-based) so
    it works on a live keyspace without blocking the server. For caches with
    >10k entries, swap this for a vector-index extension (RediSearch +
    HNSW). The Protocol shape doesn't change.
    """

    DEFAULT_KEY_PREFIX = "cache"
    DEFAULT_TAG_PREFIX = "tag"

    def __init__(
        self,
        *,
        url: str | None = None,
        client: Any | None = None,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        tag_prefix: str = DEFAULT_TAG_PREFIX,
    ) -> None:
        if client is None:
            try:
                import redis  # type: ignore[import-not-found]
            except ImportError as e:
                raise ImportError(
                    "RedisStorage requires the optional 'redis' extra. "
                    "Install with: pip install 'cost-optimizer[redis]'"
                ) from e
            client = redis.Redis.from_url(url or "redis://localhost:6379/0")
        self.client = client
        self.key_prefix = key_prefix
        self.tag_prefix = tag_prefix

    def _record_key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"

    def _tag_key(self, tag: str) -> str:
        return f"{self.tag_prefix}:{tag}"

    def put(self, record: CacheRecord) -> None:
        import json
        from binascii import b2a_base64

        # Store the vector + payload as a JSON-encoded blob; Redis hashes
        # don't natively carry numeric arrays.
        blob = json.dumps(
            {
                "key": record.key,
                "vector": list(record.vector),
                "payload": record.payload,
                "tags": sorted(record.tags),
                "expires_at": record.expires_at,
            }
        ).encode("utf-8")
        # b2a_base64 to keep bytes safe for any Redis-client encoding mode.
        self.client.set(self._record_key(record.key), b2a_base64(blob).decode("ascii"))
        if record.expires_at is not None:
            ttl = max(1, int(record.expires_at - time.time()))
            self.client.expire(self._record_key(record.key), ttl)
        for tag in record.tags:
            self.client.sadd(self._tag_key(tag), record.key)

    def _load(self, redis_key: str) -> CacheRecord | None:
        import json
        from binascii import a2b_base64

        raw = self.client.get(redis_key)
        if raw is None:
            return None
        # Decode; Redis client may return bytes or str depending on `decode_responses`.
        if isinstance(raw, bytes):
            raw = raw.decode("ascii")
        blob = a2b_base64(raw)
        data = json.loads(blob.decode("utf-8"))
        return CacheRecord(
            key=data["key"],
            vector=tuple(data["vector"]),
            payload=data["payload"],
            tags=frozenset(data["tags"]),
            expires_at=data["expires_at"],
        )

    def find_nearest(self, vector: list[float]) -> tuple[CacheRecord, float] | None:
        best: tuple[CacheRecord, float] | None = None
        cursor = 0
        match = f"{self.key_prefix}:*"
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=match)
            for k in keys:
                record = self._load(k.decode("utf-8") if isinstance(k, bytes) else k)
                if record is None:
                    continue
                sim = cosine(vector, list(record.vector))
                if best is None or sim > best[1]:
                    best = (record, sim)
            if cursor == 0:
                break
        return best

    def invalidate_by_tag(self, tag: str) -> int:
        members = self.client.smembers(self._tag_key(tag)) or set()
        count = 0
        for member in members:
            key = member.decode("utf-8") if isinstance(member, bytes) else member
            if self.client.delete(self._record_key(key)) > 0:
                count += 1
        self.client.delete(self._tag_key(tag))
        return count

    def purge_expired(self, now: float) -> int:
        # Native Redis TTL handles eviction. This method is here for
        # Storage-Protocol parity; returns 0 (Redis took care of it).
        del now  # unused; kept for protocol parity
        return 0

    def __len__(self) -> int:
        cursor = 0
        match = f"{self.key_prefix}:*"
        count = 0
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=match)
            count += len(keys)
            if cursor == 0:
                break
        return count


# ----------------------------------------------------------------------
# Math helpers
# ----------------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0.0 if either vector is zero."""
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ----------------------------------------------------------------------
# Telemetry
# ----------------------------------------------------------------------


@dataclass
class CacheStats:
    """Hits / misses / hit-rate, plus pending false-positive observations."""

    hits: int = 0
    misses: int = 0
    invalidations: int = 0
    expired_purged: int = 0

    @property
    def total_lookups(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        n = self.total_lookups
        return self.hits / n if n > 0 else 0.0


# ----------------------------------------------------------------------
# SemanticCache
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class CacheLookupResult:
    """Outcome of a single cache lookup."""

    hit: bool
    payload: Any | None
    similarity: float  # cosine sim of best match (0.0 if store empty)
    matched_record_key: str | None


class SemanticCache:
    """Embedding-keyed response cache.

    Args:
      embedder: turns prompts into vectors.
      storage: persists records + supports nearest-vector + tag operations.
      similarity_threshold: minimum cosine similarity for a hit. Default 0.95
        (D-006: high on purpose because false positives are user-visible
        bugs while false negatives are just cache misses).
      default_ttl_s: optional TTL applied to writes that don't specify one.
      now_fn: injectable clock (tests pass a fake; production gets `time.time`).
    """

    def __init__(
        self,
        *,
        embedder: Embedder,
        storage: Storage,
        similarity_threshold: float = 0.95,
        default_ttl_s: float | None = None,
        now_fn: Any = time.time,
    ) -> None:
        if not (0.0 < similarity_threshold <= 1.0):
            raise ValueError(f"similarity_threshold must be in (0, 1]; got {similarity_threshold}")
        # Extend the existing sign-only check to finiteness (#36). A NaN ttl
        # would store as expires_at = now + NaN = NaN, then every subsequent
        # `now < expires_at` comparison is false → every entry reads as
        # expired → the cache silently goes fully bypassed without diagnostic.
        if default_ttl_s is not None and (not math.isfinite(default_ttl_s) or default_ttl_s <= 0):
            raise ValueError(f"default_ttl_s must be a finite positive number; got {default_ttl_s}")
        self.embedder = embedder
        self.storage = storage
        self.similarity_threshold = similarity_threshold
        self.default_ttl_s = default_ttl_s
        self.now_fn = now_fn
        self.stats = CacheStats()

    def _make_key(self, prompt: str, model: str) -> str:
        # Keys include the model so the same prompt → two different models
        # are two cache entries (D-005).
        h = hashlib.sha256(f"{model} {prompt}".encode()).hexdigest()
        return h[:16]

    def lookup(self, prompt: str, *, model: str) -> CacheLookupResult:
        """Look up a cached response for `prompt`.

        Returns a `CacheLookupResult`. On hit, `payload` is the cached value;
        on miss, `payload` is `None` and the caller should call the model
        and `put()` the result.
        """
        # Drop expired entries opportunistically so cold lookups don't
        # silently match against stale data. RedisStorage no-ops this since
        # Redis itself evicts expired keys.
        self.stats.expired_purged += self.storage.purge_expired(self.now_fn())

        vector = self.embedder.embed(self._scoped_prompt(prompt, model))
        best = self.storage.find_nearest(vector)
        if best is None:
            self.stats.misses += 1
            return CacheLookupResult(
                hit=False, payload=None, similarity=0.0, matched_record_key=None
            )

        record, similarity = best
        if similarity >= self.similarity_threshold:
            self.stats.hits += 1
            return CacheLookupResult(
                hit=True,
                payload=record.payload,
                similarity=similarity,
                matched_record_key=record.key,
            )
        self.stats.misses += 1
        return CacheLookupResult(
            hit=False, payload=None, similarity=similarity, matched_record_key=None
        )

    def put(
        self,
        prompt: str,
        payload: Any,
        *,
        model: str,
        tags: Iterable[str] = (),
        ttl_s: float | None = None,
    ) -> str:
        """Store `payload` under the embedding of `prompt`. Returns the cache key."""
        ttl = ttl_s if ttl_s is not None else self.default_ttl_s
        expires_at = (self.now_fn() + ttl) if ttl is not None else None
        vector = tuple(self.embedder.embed(self._scoped_prompt(prompt, model)))
        key = self._make_key(prompt, model)
        record = CacheRecord(
            key=key,
            vector=vector,
            payload=payload,
            tags=frozenset(tags),
            expires_at=expires_at,
        )
        self.storage.put(record)
        return key

    def invalidate(self, *, tag: str) -> int:
        """Drop every record carrying `tag`. Returns count dropped."""
        n = self.storage.invalidate_by_tag(tag)
        self.stats.invalidations += n
        return n

    def _scoped_prompt(self, prompt: str, model: str) -> str:
        # Embedding input includes the model id so the two-model "different
        # entries for the same prompt" property holds at the embedding layer
        # too (not just the synthetic key).
        return f"[model={model}] {prompt}"


# ----------------------------------------------------------------------
# Offline false-positive measurement (D-007)
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class FalsePositiveSample:
    prompt: str
    cached_response: Any
    actual_response: Any
    is_false_positive: bool


def measure_false_positive_rate(
    cache: SemanticCache,
    held_out: Iterable[tuple[str, str]],
    *,
    model: str,
    call_model: Any,
    equality: Any = lambda a, b: a == b,
) -> tuple[float, list[FalsePositiveSample]]:
    """Offline helper: for each (prompt, _placeholder) pair, look up in the cache,
    if it hits also call the model, and check whether the cached response equals
    the model's actual response. Returns `(rate, samples)`.

    `call_model(prompt) -> response` is whatever the operator's real model
    invocation looks like; `equality(a, b) -> bool` defaults to plain `==`
    but callers can pass a semantic comparator (e.g., embedding similarity
    of responses) for natural-language outputs.

    Done OFFLINE on a held-out set, not online — online sampling would slowly
    bleed the cost savings the cache exists to deliver (D-007).
    """
    samples: list[FalsePositiveSample] = []
    fp_count = 0
    hit_count = 0
    for prompt, _ in held_out:
        result = cache.lookup(prompt, model=model)
        if not result.hit:
            continue
        hit_count += 1
        actual = call_model(prompt)
        is_fp = not equality(result.payload, actual)
        samples.append(
            FalsePositiveSample(
                prompt=prompt,
                cached_response=result.payload,
                actual_response=actual,
                is_false_positive=is_fp,
            )
        )
        if is_fp:
            fp_count += 1
    rate = (fp_count / hit_count) if hit_count > 0 else 0.0
    return rate, samples
