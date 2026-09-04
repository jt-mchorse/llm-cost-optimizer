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

import copy
import hashlib
import json
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, cast

from cost_optimizer.io_utils import atomic_write_text

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
        # Extends sign-only `ngram < 1` to the portfolio positive-int contract
        # (#40). The same shape landed in `rag-production-kit#43`,
        # `embedding-model-shootout#36`, and `prompt-regression-suite#38`.
        # Sign-only accepted `True` (silently bound; unigram embedding had
        # worse retrieval quality but no error → cache hit-rate silently
        # degraded), float (bound, then `_ngrams` raised TypeError deep in
        # the call chain), and `NaN`/`Inf` (bound, surfaced as cryptic
        # range/overflow errors).
        if not isinstance(ngram, int) or isinstance(ngram, bool) or ngram <= 0:
            raise ValueError(f"ngram must be a positive integer; got {ngram!r}")
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
            # Degenerate input: fewer tokens than the n-gram width (an empty or
            # whitespace-only prompt at ngram=2; any single-word prompt at
            # ngram>=3). A *constant* vector here makes ALL such inputs collide
            # at cosine 1.0 regardless of model id or content — silently
            # defeating model-scoping (D-005) and serving a false-positive
            # cache hit (D-006/D-007), e.g. model A's response returned to a
            # model B caller (#98). Seeding a *single* slot from a hash of the
            # full (model-scoped) text — the original #98 fix — still leaves a
            # unit basis vector: two degenerate inputs whose one slot collides
            # are cosine 1.0 at the 128-slot birthday rate, the exact failure
            # #102 closed on the single-bigram path below (#110). Blend the same
            # 4 *independent* slots from disjoint SHA-256 windows the one-ngram
            # path uses, then fall through to L2-normalize (don't return early):
            # identical degenerate inputs still map to identical slots (correct
            # hit preserved), but a false hit now needs all 4 windows to collide
            # simultaneously (~(1/128)^4), not just one.
            h = hashlib.sha256(text.encode("utf-8")).digest()
            for i in range(0, 16, 4):  # 4 independent slots from 16 of the 32 bytes
                vec[int.from_bytes(h[i : i + 4], "big") % HASH_EMBEDDING_DIM] += 1.0
        for ng in ngrams:
            h = hashlib.sha256(ng.encode("utf-8")).digest()
            slot = int.from_bytes(h[:4], "big") % HASH_EMBEDDING_DIM
            vec[slot] += 1.0
        # A vector with a *single* occupied slot is a unit basis vector: any two
        # such vectors that share that slot collide at cosine 1.0 regardless of
        # content, serving a false-positive hit (D-006/D-007). #98 fixed the
        # zero-ngram form of this (lines above); the adjacent one-ngram form is
        # just as exposed — a single-word prompt becomes the 2-token string
        # "[model=m] word", which yields exactly one bigram, hence one slot, so
        # distinct one-word prompts collide at the 128-slot birthday rate. Blend
        # in several *independent* content slots derived from the full
        # (model-scoped) text so two distinct single-bigram inputs differ in at
        # least one slot. With only 128 slots a single discriminator collides at
        # the birthday rate, and a one-word prompt's single bigram string equals
        # its scoped text — so the slots must come from DISJOINT byte windows of
        # the text's SHA-256, each an independent slot. A false hit then needs
        # every window to collide simultaneously (~(1/128)^k), not just one.
        # Identical inputs map to identical slots (cosine 1.0 preserved);
        # multi-slot vectors are untouched, so near-identical multi-word prompts
        # keep their similarity.
        if sum(1 for v in vec if v) == 1:
            h = hashlib.sha256(text.encode("utf-8")).digest()
            for i in range(0, 16, 4):  # 4 independent slots from 16 of the 32 bytes
                vec[int.from_bytes(h[i : i + 4], "big") % HASH_EMBEDDING_DIM] += 1.0
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
    # The model this entry was stored under. Lookup hard-filters on it (D-005)
    # so model isolation does not depend on embedding separation, which washes
    # out for long prompts (#125). Defaults to "" so Storage-level constructions
    # and pre-#125 Redis blobs (which lack the field) stay backward-compatible —
    # such a record only ever matches a lookup that also passes model="".
    model: str = ""


class Storage(Protocol):
    """Persistence + nearest-vector + tag-membership operations.

    **Payload contract** (#192). A `CacheRecord.payload` reaching an
    implementation is guaranteed to survive a JSON round-trip unchanged --
    `SemanticCache.put` enforces that at its own seam via `_validate_payload`,
    so a backend may serialize freely and two conforming backends serve the
    same object for the same record. This is the same "backends must not
    disagree about the same cache" property `find_nearest`'s tie-break section
    below spells out, at the level of what is stored rather than which record
    wins.
    """

    def put(self, record: CacheRecord) -> None: ...
    def find_nearest(
        self, vector: list[float], *, model: str | None = None
    ) -> tuple[CacheRecord, float] | None:
        """Return (best_record, best_similarity) or None if no record matches.

        When ``model`` is given, records stored under a *different* model are
        excluded from the search entirely (D-005). This must be a
        selection-time filter, not a post-filter on the single global best:
        a higher-cosine cross-model record would otherwise mask a valid,
        above-threshold same-model record and turn a legitimate hit into a
        miss (#133). ``model=None`` (the default) scans every record, so
        direct callers and pre-#133 code keep their behavior.

        **Ties are part of this contract** (#188). When two records score the
        same cosine, an implementation must return the one with the greater
        ``record.key`` — not whichever it happened to scan first. Iteration
        order is a property of how a backend stores things, so resolving a tie
        by position makes the served payload depend on write order and lets two
        conforming backends disagree about the same cache. ``key`` is a hash of
        the ``{model, prompt}`` object, so every backend can agree on it
        without coordinating. A backend that grows a server-side nearest-vector
        query (RediSearch, pgvector) must carry the tiebreak into its ``ORDER
        BY`` rather than dropping it.
        """

    def invalidate_by_tag(self, tag: str) -> int:
        """Drop every record tagged `tag`. Return count dropped.

        **Tagged means the record says so** (#194). A backend that keeps a
        tag->keys index must treat that index as a hint and check
        `tag in record.tags` before dropping anything, because an index entry
        outlives the record it names -- through a sibling tag's invalidation,
        and through TTL expiry -- while keys are reused, being a deterministic
        hash of `(prompt, model)`. Acting on the index alone evicts live records
        that do not carry the tag, which `InMemoryStorage` (no index, filters the
        records directly) never does. Same "backends must not disagree about the
        same cache" property as the payload contract above and `find_nearest`'s
        tie-break below, at the level of which records a tag names.
        """

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
        # Isolate the payload from the caller's live reference on ingress, so a
        # caller that mutates the object it passed to `SemanticCache.put` can't
        # retroactively corrupt the stored entry. `RedisStorage.put` gets this
        # for free by json.dumps-ing the payload; the dep-free in-memory backend
        # (the default) otherwise held the exact object by reference (#131).
        #
        # That equivalence is about *isolation* only. It was not true of
        # *fidelity*: a `deepcopy` preserves a tuple and an int dict key, and a
        # JSON round-trip does not, so the two backends served different objects
        # for the same record until `SemanticCache.put` started rejecting
        # payloads that don't round-trip (#192).
        # vector (tuple) / tags (frozenset) are already immutable — only the
        # arbitrary payload needs the copy.
        self._records[record.key] = replace(record, payload=copy.deepcopy(record.payload))

    def find_nearest(
        self, vector: list[float], *, model: str | None = None
    ) -> tuple[CacheRecord, float] | None:
        if not self._records:
            return None
        best: tuple[CacheRecord, float] | None = None
        for r in self._records.values():
            # Selection-time model filter (D-005/#133): a cross-model record
            # must never be *considered*, so it can't outscore and mask a
            # valid same-model candidate.
            if model is not None and r.model != model:
                continue
            sim = cosine(vector, list(r.vector))
            # Ties break on `record.key`, not on scan position (#188). A bare
            # `sim > best[1]` let the FIRST record scanned win, and "first" here
            # is `dict.values()` insertion order — a property of how the cache
            # was written, not of what it holds.
            #
            # Ties are ordinary, not exotic: `_tokenize` is `text.lower().split()`,
            # so casing and whitespace runs vanish from the EMBEDDING, while
            # `_make_key` hashes the exact prompt text. Four casings of one
            # question are therefore four distinct records sharing one vector,
            # all at cosine 1.0. Measured: across the 24 insertion permutations
            # of those four, `lookup` served four different payloads, and
            # `measure_false_positive_rate` read 0.00 for 12 orders and 1.00 for
            # the other 12 on identical cache contents — that rate is the
            # offline number D-007 exists to produce.
            #
            # `key` is a SHA-256 digest of the `{model, prompt}` object, so it is
            # unique and derived from CONTENT. That is what makes `RedisStorage`
            # land on the same record: a tiebreak on a scan *position* would
            # remove the dependence on the iteration mechanism but not on the
            # write order, which is the actual defect (llm-eval-harness#206,
            # agent-orchestration-platform#120).
            #
            # It does not make the winner the *right* record — among tied
            # entries it is arbitrary-but-fixed, and identical across backends.
            # "Freshest wins" would be better semantics but needs a `created_at`
            # on `CacheRecord`, i.e. a stored-format change; see `model`'s
            # backward-compatibility note above for why that wants its own issue.
            if best is None or (sim, r.key) > (best[1], best[0].key):
                best = (r, sim)
        if best is None:
            return None
        # Isolate on egress too, mirroring RedisStorage._load which reconstructs
        # a fresh object from JSON on every read. Without this a caller mutating
        # `lookup(...).payload` (e.g. appending to a citations list) would poison
        # the committed cache entry served to every later hit, including
        # semantically-similar prompts that match the same record (#131).
        record, sim = best
        return replace(record, payload=copy.deepcopy(record.payload)), sim

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
        now_fn: Any = time.time,
    ) -> None:
        if client is None:
            try:
                import redis
            except ImportError as e:
                raise ImportError(
                    "RedisStorage requires the optional 'redis' extra. "
                    "Install with: pip install 'cost-optimizer[redis]'"
                ) from e
            client = redis.Redis.from_url(url or "redis://localhost:6379/0")
        self.client = client
        self.key_prefix = key_prefix
        self.tag_prefix = tag_prefix
        # Must be the SAME clock `SemanticCache` computes `expires_at` from —
        # `put` below turns that absolute timestamp back into a relative TTL,
        # and the subtraction is meaningless across two clocks (#172).
        # `SemanticCache.__init__` rejects a mismatch rather than letting it
        # degrade quietly.
        self.now_fn = now_fn

    def _record_key(self, key: str) -> str:
        return f"{self.key_prefix}:{key}"

    def _tag_key(self, tag: str) -> str:
        return f"{self.tag_prefix}:{tag}"

    def put(self, record: CacheRecord) -> None:
        import json
        from binascii import b2a_base64

        # A re-put under an existing key may carry a different tag set. Redis
        # tag membership is additive (the `sadd` loop below), so without
        # pruning, a tag the record no longer has would still point at this
        # key — and a later `invalidate_by_tag` on that lost tag would wrongly
        # evict the record. Drop those stale memberships first, matching
        # InMemoryStorage, whose `put` replaces the whole record so old tags
        # simply vanish.
        existing = self._load(self._record_key(record.key))
        if existing is not None:
            for stale_tag in existing.tags - record.tags:
                self.client.srem(self._tag_key(stale_tag), record.key)

        # Store the vector + payload as a JSON-encoded blob; Redis hashes
        # don't natively carry numeric arrays.
        blob = json.dumps(
            {
                "key": record.key,
                "vector": list(record.vector),
                "payload": record.payload,
                "tags": sorted(record.tags),
                "expires_at": record.expires_at,
                "model": record.model,
            }
        ).encode("utf-8")
        # b2a_base64 to keep bytes safe for any Redis-client encoding mode.
        self.client.set(self._record_key(record.key), b2a_base64(blob).decode("ascii"))
        if record.expires_at is not None:
            # `record.expires_at` is an absolute timestamp on the *cache's*
            # clock (`SemanticCache.now_fn() + ttl_s`), so the subtraction has
            # to use that same clock. This read `time.time()` directly, which
            # is only correct when `now_fn` happens to be the wall clock —
            # under the injected clock the parameter exists for, a 3600s ttl
            # produced `int(3600ish - 1.78e9)`, and `max(1, ...)` turned that
            # into a **1-second** TTL. The record vanished after one real
            # second while the in-memory backend still served it, so the
            # Redis-backed cache silently stopped caching (#172).
            #
            # `max(1, ...)` stays: it keeps an already-expired record from
            # becoming `EXPIRE key 0` (an immediate delete). It is only the
            # floor, not the clock, that was doing the papering-over.
            ttl = max(1, int(record.expires_at - self.now_fn()))
            self.client.expire(self._record_key(record.key), ttl)
        for tag in record.tags:
            self.client.sadd(self._tag_key(tag), record.key)

    def _load(self, redis_key: str) -> CacheRecord | None:
        import json
        from binascii import a2b_base64

        # `self.client` is a *sync* redis.Redis; redis-py's type stubs give
        # every command a `ResponseT = Awaitable[Any] | Any` return, so the
        # never-taken async arm is cast away at each storage-boundary call.
        raw = cast("bytes | str | None", self.client.get(redis_key))
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
            # `.get` for pre-#125 blobs written before the field existed; they
            # decode with model="" and only match a lookup passing model="".
            model=data.get("model", ""),
        )

    def find_nearest(
        self, vector: list[float], *, model: str | None = None
    ) -> tuple[CacheRecord, float] | None:
        best: tuple[CacheRecord, float] | None = None
        cursor = 0
        match = f"{self.key_prefix}:*"
        while True:
            cursor, keys = cast(
                "tuple[int, list[bytes]]", self.client.scan(cursor=cursor, match=match)
            )
            for k in keys:
                record = self._load(k.decode("utf-8") if isinstance(k, bytes) else k)
                if record is None:
                    continue
                # Selection-time model filter (D-005/#133), mirroring
                # InMemoryStorage: skip cross-model records so they can't mask
                # a valid same-model candidate. Client-side over SCAN — a
                # server-side pre-filter would need a RediSearch index (out of
                # scope, per the class docstring).
                if model is not None and record.model != model:
                    continue
                sim = cosine(vector, list(record.vector))
                # Same content tiebreak as InMemoryStorage (#188) — see the long
                # note there. This half is why the fix is not cosmetic: `SCAN`
                # returns keys in an order Redis does not define, so the two
                # backends resolved a tie to *different records* on the same
                # populated cache with the same insertion order (measured:
                # in-memory 250a127aab1750fb, Redis 1a5ec8281b4bf5ff). This
                # module already asserts cross-backend parity in its test
                # suite; the tiebreak is what makes that hold on ties too.
                if best is None or (sim, record.key) > (best[1], best[0].key):
                    best = (record, sim)
            if cursor == 0:
                break
        return best

    def invalidate_by_tag(self, tag: str) -> int:
        """Drop every record tagged `tag`, validating the index before acting (#194).

        The `tag:<name>` SET is an *index*, not the truth. A key leaves a
        record's world in two ordinary ways that this index never hears about:

        - `invalidate_by_tag` on a *different* tag deletes the record and drops
          only that one tag's SET, leaving the key inside every other SET it
          belonged to.
        - native Redis TTL expires the record. Nothing touches any SET.

        `SemanticCache._make_key` is a deterministic hash of `(prompt, model)`,
        so re-caching the same prompt reuses that exact key. The stale
        membership then names a *live record that does not carry the tag*, and
        deleting it on the strength of the index alone silently evicted an entry
        `InMemoryStorage` -- which has no index and filters `tag in r.tags` on
        the records themselves -- correctly kept. Measured through the public
        API, on both roads::

            c.put(P, tags=["v1", "geography"]); c.invalidate(tag="v1")
            c.put(P, tags=["v2"]); c.invalidate(tag="geography")
            mem   -> returned 0, still cached, len 1
            redis -> returned 1, evicted,      len 0

        The harm is a wrong eviction, which is a cache miss, which is a paid API
        call -- the failure mode this package exists to prevent -- and it is
        quiet, because `invalidate` reports `1` and looks like it worked.

        `put` already names this hazard exactly ("a tag the record no longer has
        would still point at this key -- and a later `invalidate_by_tag` on that
        lost tag would wrongly evict the record"), but its pruning only runs on
        the branch where `_load(existing)` returns a record. When the key was
        deleted or expired there is nothing to diff against, so the hazard `put`
        documents is left open by `put`'s own guard. Validating here covers both
        branches with one rule, and is self-healing: a stale entry is `srem`-ed
        the first time it is encountered, so the index converges instead of
        growing forever.
        """
        members = cast("set[bytes]", self.client.smembers(self._tag_key(tag))) or set()
        count = 0
        for member in members:
            key = member.decode("utf-8") if isinstance(member, bytes) else member
            record = self._load(self._record_key(key))
            # `record is None`: deleted or TTL-expired out from under this index.
            # `tag not in record.tags`: the key was reused by a later `put` whose
            # tag set does not include `tag`. Either way the index entry is stale
            # -- drop the membership, and do not touch the record it names.
            if record is None or tag not in record.tags:
                self.client.srem(self._tag_key(tag), key)
                continue
            if cast("int", self.client.delete(self._record_key(key))) > 0:
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
            cursor, keys = cast(
                "tuple[int, list[bytes]]", self.client.scan(cursor=cursor, match=match)
            )
            count += len(keys)
            if cursor == 0:
                break
        return count


# ----------------------------------------------------------------------
# Math helpers
# ----------------------------------------------------------------------


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0.0 if either vector is zero.

    Non-finite *components* are rejected upstream at the `put`/`lookup` seam
    (`_validate_embedding`), but `find_nearest` scans every stored record
    through here in a loop, so this stays defensive: a non-finite *result*
    (e.g. a vector whose huge-but-finite components overflow `sum(x*x)` to
    `inf`) returns the same well-defined 0.0 fallback as the zero-vector
    branch rather than poisoning `find_nearest`'s running best with `nan`
    (`sim > best` is always False against `nan`, so a real match could never
    overtake it).
    """
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    sim = dot / (na * nb)
    if not math.isfinite(sim):
        return 0.0
    return sim


def _validate_embedding(vector: list[float], *, where: str) -> None:
    """Reject a non-finite embedding component at the BYO-embedder seam.

    The `Embedder` is a BYO Protocol (the `HashEmbedder` docstring points
    production callers at it): a normalization divide-by-zero, an Inf
    overflow, or a NaN-poisoned model output can hand back a `NaN`/`±Inf`
    component. Unvalidated, it flows into `cosine()` and makes every
    similarity `nan`, so `nan >= threshold` is always False — an identical
    prompt that must be a hit is silently reported as a miss, the cache goes
    fully bypassed, and `nan` leaks into `similarity`/`hit_rate`. Reject it
    loudly here rather than store/scan a poisoned vector, the same contract
    the ttl guards apply (#36/#85) and the sibling prompt-regression-suite
    #67 fix applies to candidate embeddings.

    A *present-but-non-numeric* component (a `str`/`None`/list off the same
    BYO seam — a JSON-decoded row, a truncated SDK response, an adapter that
    returns the wrong element type) cannot go through `math.isfinite`, which
    raises a raw `TypeError`. That escaped `put`/`lookup` uncaught, the
    non-numeric sibling of the NaN/Inf branch (#140/#138 fixed the same
    present-but-non-numeric-coercion gap at the router logprob/judge seams).
    Reject it at the seam too, with the same field/index-naming `ValueError`.
    """

    def _corrupt(v: object) -> bool:
        if not isinstance(v, (int, float)):
            return True  # present-but-non-numeric (str/None/list/...) off the BYO seam
        return not math.isfinite(v)  # NaN / +/-Inf

    bad = next((i for i, v in enumerate(vector) if _corrupt(v)), None)
    if bad is not None:
        val = vector[bad]
        if isinstance(val, (int, float)):
            raise ValueError(
                f"embedding from {where} has a non-finite component at index {bad}: "
                f"{val!r}. The embedder returned a corrupt vector — a NaN/Inf "
                "component makes every cosine similarity NaN and silently disables the "
                "cache (every lookup misses, hit_rate reads 0). Fix the embedder."
            )
        raise ValueError(
            f"embedding from {where} has a non-numeric component at index {bad}: "
            f"{val!r} ({type(val).__name__}). The embedder returned a corrupt vector — a "
            "non-numeric component cannot enter cosine similarity and would otherwise "
            "raise a raw TypeError deep in the scan; reject it at the seam. Fix the "
            "embedder."
        )


#: The exact types a JSON round-trip preserves. Membership is tested with
#: ``type(node) in`` rather than ``isinstance``, so a subclass is *not* a
#: member -- see ``_validate_payload`` (#207).
_JSON_SCALAR_TYPES: frozenset[type] = frozenset({type(None), bool, int, float, str})

#: ``(base json.JSONEncoder dispatches on, type the value comes back as)``.
#:
#: Anything matching none of these bases is handed to ``default()``, which
#: raises ``TypeError``. This is a *type-dispatch* question, deliberately
#: answered without calling ``json.dumps``: see ``_unrepresentable_message``.
#:
#: The second element is not decoration and is not always the first: JSON has
#: one array type, so a ``tuple`` is written as an array and read back as a
#: ``list``. Naming the base would tell an operator their ``tuple`` comes back
#: as a ``tuple``, which is the whole defect. Order matters -- ``bool`` before
#: ``int``, since ``bool`` is an ``int`` subclass and the first match wins.
_JSON_ENCODABLE_BASES: tuple[tuple[type, str], ...] = (
    (dict, "dict"),
    (list, "list"),
    (tuple, "list"),
    (str, "str"),
    (bool, "bool"),
    (int, "int"),
    (float, "float"),
)


def _unrepresentable_message(path: str, node: Any) -> str:
    """The rejection message for a node that is not an exact JSON type.

    Two mechanisms reach this line and they are opposites, so one message
    cannot describe both. `json.dumps` **converts** a `tuple`, and every
    subclass of an encodable base, to its base type; it **raises** `TypeError`
    for a `set`, `bytes`, or a `datetime`. The single message this function
    replaces claimed the `TypeError` for everything, which was wrong for the
    very first row of the module docstring's own table::

        json.dumps(("a","b"))         -> '["a", "b"]'   # no TypeError
        json.dumps(defaultdict(list)) -> '{}'           # no TypeError
        json.dumps({"a"})             -> TypeError      # only here

    So the shapes whose harm is the *silent* one -- the one the docstring above
    calls worse -- were the shapes being told they would fail loudly.

    The mechanism is decided from `json`'s own **type dispatch**
    (`_JSON_ENCODABLE_BASES`) rather than by calling `json.dumps(node)` on the
    payload. Probing for real would answer this one question by serializing an
    arbitrarily large caller-supplied structure on an error path, and
    `json.dumps` recurses -- which is the failure mode the iterative walk in
    `_validate_payload` exists to avoid, and would turn a rejection into a
    `RecursionError`. `tests/test_semantic_cache_payload_parity.py` checks this
    classifier against real `json.dumps` calls over every shape in the table
    instead, so the cheap rule stays honest without production paying for it.

    One acknowledged imprecision: a *cyclic* subclass of an encodable base
    (`d = MyDict(); d["s"] = d`) is reported as "converts", while `json.dumps`
    would refuse it with `ValueError: Circular reference detected`. It is
    rejected either way and the type is the reason worth naming; the cycle is
    reported on its own message when the container is an exact `dict`/`list`.
    """
    kind = type(node).__name__
    for base, round_trips_as in _JSON_ENCODABLE_BASES:
        if isinstance(node, base):
            return (
                f"{path} is a {kind} ({node!r}), which does not survive a JSON round-trip "
                f"unchanged. `json.dumps` encodes it as a plain {round_trips_as}, so "
                f"RedisStorage stores and serves back a {round_trips_as} while "
                f"InMemoryStorage deepcopies and serves the {kind} -- the same code gets "
                "different objects depending on which backend is configured, and nothing "
                f"raises at write time on either. Convert it to a {round_trips_as} before "
                "caching."
            )
    return (
        f"{path} is a {kind} ({node!r}), which has no JSON "
        "representation. Cache payloads must survive a JSON round-trip unchanged: "
        "InMemoryStorage would store it as-is while RedisStorage raises "
        "`TypeError: Object of type "
        f"{kind} is not JSON serializable` from inside `put`, so the "
        "same code caches successfully on one backend and fails on the other. "
        "Convert it to a JSON type before caching."
    )


def _validate_payload(payload: Any) -> None:
    """Reject a payload the two shipped backends would not agree on (#192).

    `SemanticCache.put` takes `payload: Any` and the backends disagree about
    what that means. `InMemoryStorage` `deepcopy`s it, preserving exact types;
    `RedisStorage` `json.dumps` it. Measured over twelve payload shapes through
    the same public API, six diverged:

        payload                 in-memory              redis
        dict with tuple value   {'c': ('a', 'b')}      {'c': ['a', 'b']}
        bare tuple              ('a', 'b')             ['a', 'b']
        int-keyed dict          {1: 'one'}             {'1': 'one'}
        nested tuple in list    {'xs': [('a', 1)]}     {'xs': [['a', 1]]}
        set                     {'a', 'b'}             TypeError from put
        bytes                   b'Paris'               TypeError from put
        datetime                datetime(2026, 1, 1)   TypeError from put

    Two harms, and the quiet one is worse. `payload[1]` on an int-keyed dict
    works on the default backend and raises `KeyError: 1` on Redis, for an
    entry the cache reported as a *hit* -- nothing raises at write time and the
    cache is the last place anyone looks. The `set`/`bytes`/`datetime` rows at
    least fail loudly, but they fail from inside `RedisStorage.put`, so a
    request that would otherwise have succeeded fails *because it tried to
    cache its own result* -- and only after the backend swap `RedisStorage`'s
    own docstring recommends ("For larger caches use `RedisStorage`").

    So the rule is: a payload must survive a JSON round-trip **unchanged**,
    because a persistent backend has to serialize it. Enforced here, at the
    `put` seam, before any backend is touched -- the same placement and the
    same reasoning `put` already applies to `ttl_s` and `_validate_embedding`
    applies to the BYO embedder.

    Deliberately a structural walk, not `json.loads(json.dumps(x)) == x`.
    `nan != nan`, so the equality form would reject a `NaN`/`Infinity` payload
    value -- and those two shapes round-trip *identically* on both backends
    today, so they are not part of this defect and must keep working.

    Iterative rather than recursive: a payload is caller-supplied and can be
    arbitrarily deep, and a `RecursionError` here is not the `ValueError` the
    seam contracts to raise.

    That reason is about unbounded *depth*, and the rewrite to an explicit
    stack removed the only thing that bounded unbounded *revisits* (#203): a
    recursive walk over a self-referential payload at least dies with
    `RecursionError` in milliseconds, while the stack form ran until the
    process died -- 5.7 GB resident in 4 seconds through `SemanticCache.put`,
    because every pop of a cyclic container pushes its children back with a
    longer `path` string. A cyclic payload is squarely this function's business:
    at the storage seam `InMemoryStorage.put` accepts it (`copy.deepcopy`
    memoizes, so the cycle survives) and `RedisStorage.put` raises
    `ValueError: Circular reference detected` from `json.dumps` -- the exact
    backend disagreement the validator exists to catch, and the one shape it
    could not reach a verdict on.

    Cycle detection mirrors `json.dumps`'s own `check_circular=True`, because
    `json.dumps` *is* the reference behaviour this contract is written against:
    a container that is its own **ancestor** is refused, while a container
    reachable twice by different paths is not. `json.dumps({"a": x, "b": x})`
    emits `x` twice and both backends round-trip it, so a blanket
    "seen this id before -> reject" would fail payloads that work today. Hence
    the on-path set with explicit exit frames, rather than a visited set: the id
    is discarded on the way back up.

    Classification is on **exact type**, not `isinstance` (#207). The rule above
    is "is exactly a JSON type"; `isinstance` answers "is-a", and the two differ
    for every subclass of an accepted type. `json.dumps` encodes a subclass by
    its base, so an `IntEnum`, a `defaultdict`, a `Counter`, an `OrderedDict` or
    a plain `class MyDict(dict)` passed the old gate and then diverged exactly
    as `tuple` and the int-keyed dict did -- ten of eleven measured shapes.
    Both consequences are the quiet kind this docstring calls worse:
    `served["answers"]["q2"]` returns `[]` on the in-memory backend and raises
    `KeyError` on Redis for a `defaultdict` payload, and `served["status"].name`
    returns `'OK'` on one and raises `AttributeError` on the other for an
    `IntEnum`. Nothing raises at write time on either road.
    """
    # `(path, node, is_exit)`. An exit frame is pushed under a container's
    # children so `on_path` is popped back down when the subtree is done.
    stack: list[tuple[str, Any, bool]] = [("payload", payload, False)]
    on_path: set[int] = set()
    while stack:
        path, node, is_exit = stack.pop()
        if is_exit:
            on_path.discard(id(node))
            continue
        # `type(...) in`, not `isinstance(...)` (#207). `bool` is listed as
        # itself rather than inherited from the `int` arm -- which is also why
        # the exact-type form is the simpler one to state here: the old code
        # needed a comment explaining that `bool` came before `int` on purpose,
        # and an exact-type set has no subclass ordering to get wrong.
        if type(node) in _JSON_SCALAR_TYPES:
            continue
        if type(node) is dict or type(node) is list:
            if id(node) in on_path:
                raise ValueError(
                    f"{path} is a {type(node).__name__} that contains itself; cache "
                    "payloads must survive a JSON round-trip unchanged, and a circular "
                    "reference has no JSON representation at all -- RedisStorage raises "
                    "`ValueError: Circular reference detected` from inside `put` while "
                    "InMemoryStorage stores the cycle intact, so the same payload caches "
                    "on one backend and fails on the other. Break the cycle before "
                    "caching. (A structure merely reachable by two different paths is "
                    "fine and is not this error.)"
                )
            on_path.add(id(node))
            stack.append((path, node, True))
        if type(node) is dict:
            for k, v in node.items():
                # Exact type here too: `json.dumps` writes a `str` subclass key
                # as a plain `str`, so a `MyStr("a")` key diverges the same way
                # an `int` key does -- and unlike an `int` key it does not even
                # change the key's *text*, only its type, which makes it the
                # quieter half of the same defect (#207).
                if type(k) is not str:
                    # A `str` subclass key keeps its text through `json.dumps`
                    # and loses only its type, so the "would store this key as
                    # {str(k)!r}" clause -- written for the int-keyed case,
                    # where the text visibly changes -- reads as a no-op there.
                    # Name the type change instead when that is what happens.
                    changed = (
                        f"store this key as {str(k)!r}"
                        if not isinstance(k, str)
                        else f"store this key as a plain str rather than a {type(k).__name__}"
                    )
                    raise ValueError(
                        f"{path} has a non-string key {k!r} ({type(k).__name__}); cache "
                        "payloads must survive a JSON round-trip unchanged, and JSON "
                        f"object keys are strings -- RedisStorage would {changed} "
                        "and serve back a dict the in-memory backend never "
                        "would. Convert the key before caching."
                    )
                stack.append((f"{path}[{k!r}]", v, False))
            continue
        if type(node) is list:
            for i, v in enumerate(node):
                stack.append((f"{path}[{i}]", v, False))
            continue
        raise ValueError(_unrepresentable_message(path, node))


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

    def to_dict(self) -> dict[str, Any]:
        """JSON-stable dict for observability/logging sinks (#52).

        Mirrors ``CacheTelemetry.to_dict`` in ``cache_wrapper.py``: the
        raw counter fields plus the two derived properties so log
        consumers don't have to recompute them (and risk drift if the
        formula ever changes). Pairs with
        ``SemanticCache.dump_stats_json`` for the on-disk path; metric
        backends consume the in-process dict directly.
        """
        return {
            "hits": self.hits,
            "misses": self.misses,
            "invalidations": self.invalidations,
            "expired_purged": self.expired_purged,
            "total_lookups": self.total_lookups,
            "hit_rate": self.hit_rate,
        }


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
        # `isinstance` first (short-circuits before the chained comparison): a
        # present-but-non-numeric threshold (a str/None from a JSON/YAML/env
        # config table) hit the bare `0.0 < x <= 1.0` and raised a raw
        # TypeError instead of this field-named ValueError — the cache-config
        # sibling of the #142 `_validate_embedding` / #144 pricing-coercion gap.
        # `bool` is an `int` subclass, so `True`/`False` coerce to 1.0/0.0 and
        # `similarity_threshold=True` (→1.0) slips through IN (0, 1] silently,
        # mis-setting the hit gate. Reject `bool` explicitly (leh#178).
        if (
            isinstance(similarity_threshold, bool)
            or not isinstance(similarity_threshold, (int, float))
            or not (0.0 < similarity_threshold <= 1.0)
        ):
            raise ValueError(
                f"similarity_threshold must be a number in (0, 1]; got {similarity_threshold!r}"
            )
        # Extend the existing sign-only check to finiteness (#36). A NaN ttl
        # would store as expires_at = now + NaN = NaN, then every subsequent
        # `now < expires_at` comparison is false → every entry reads as
        # expired → the cache silently goes fully bypassed without diagnostic.
        # `isinstance` first, same reason as above: `math.isfinite("60")`
        # raises a raw TypeError, not this ValueError. Reject `bool` too (an
        # `int` subclass): `default_ttl_s=True` (→1s) silently expires entries
        # almost immediately, the same silent-eviction harm as a negative ttl
        # (leh#178).
        if default_ttl_s is not None and (
            isinstance(default_ttl_s, bool)
            or not isinstance(default_ttl_s, (int, float))
            or not math.isfinite(default_ttl_s)
            or default_ttl_s <= 0
        ):
            raise ValueError(
                f"default_ttl_s must be a finite positive number; got {default_ttl_s!r}"
            )
        # A storage backend that has its own clock must be given *this* one.
        # `expires_at` is computed here as `now_fn() + ttl_s`, and
        # `RedisStorage.put` turns that absolute timestamp back into a relative
        # TTL — arithmetic that is meaningless across two clocks. Left
        # unchecked it degraded silently: with a fake clock on the cache and
        # the default wall clock on the storage, `max(1, int(expires_at - now))`
        # made every entry a 1-second TTL, so a Redis-backed cache stopped
        # caching and every lookup went to the model (#172).
        #
        # Identity, not equality: the default is the same `time.time` object on
        # both sides, so an untouched construction passes. The only shape this
        # rejects is a clock configured on one side and not the other, which is
        # exactly the mistake. Backends without a clock (`InMemoryStorage`,
        # which reads `now` from `purge_expired`'s argument) are unaffected.
        storage_now_fn = getattr(storage, "now_fn", None)
        if storage_now_fn is not None and storage_now_fn is not now_fn:
            raise ValueError(
                f"{type(storage).__name__} carries its own now_fn "
                f"({storage_now_fn!r}) that differs from this cache's "
                f"({now_fn!r}); expires_at is computed from the cache's clock "
                "and converted back to a TTL by the storage, so they must be "
                "the same callable — pass now_fn to both"
            )
        self.embedder = embedder
        self.storage = storage
        self.similarity_threshold = similarity_threshold
        self.default_ttl_s = default_ttl_s
        self.now_fn = now_fn
        self.stats = CacheStats()

    def _make_key(self, prompt: str, model: str) -> str:
        # Keys include the model so the same prompt → two different models are
        # two cache entries (D-005).
        #
        # Hashed as a structured JSON object, not a concatenated string. Two
        # earlier attempts both hand-built a delimiter and both could be forged
        # from field content: `f"{model} {prompt}"` slid on a space, so ("b c",
        # "a") and ("c", "a b") shared a key; `f"[model={model}] {prompt}"`
        # slid on `] `, so ("claude-opus-4] extra", "PROMPT") and
        # ("claude-opus-4", "extra] PROMPT") shared one (#182). Storage keys
        # records by `record.key`, so each time the second `put` silently
        # overwrote the first.
        #
        # The comment on the second attempt asserted the `[model=...]`
        # delimiter "can't be produced by any other split". It could, and that
        # assertion is why nobody re-checked. So this doesn't pick a better
        # delimiter — there isn't one. `json.dumps` escapes field content, so a
        # boundary cannot be produced from inside a field *by construction*,
        # which is a property that holds without anyone having to be clever
        # about which characters a model id can contain. Byte-shape matches
        # `batch.py`'s payload hash (sort_keys, tight separators), which is the
        # same problem solved the same way one module over.
        blob = json.dumps(
            {"model": model, "prompt": prompt}, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        h = hashlib.sha256(blob).hexdigest()
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
        _validate_embedding(vector, where="lookup embedder")
        # D-005 hard guard: model isolation must not depend on embedding
        # separation. `_scoped_prompt` prepends `[model=<id>] `, but for a prompt
        # of ~20+ tokens that single differing bigram washes out below the 0.95
        # threshold — cosine of the two scoped vectors is (n-1)/n — so an
        # unfiltered vector NN could return a record stored under a *different*
        # model and serve e.g. a Haiku response to an Opus caller (#125).
        #
        # Scope the search to the query model (#133): the filter must run at
        # *selection* time, inside `find_nearest`, not as a post-filter on the
        # single global best. Post-filtering (the pre-#133 approach) failed when
        # a higher-cosine cross-model record outscored a valid, above-threshold
        # same-model record — `find_nearest` returned the cross-model record, the
        # `record.model == model` post-check rejected it, and a legitimate hit
        # was silently reported as a miss, masking the same-model runner-up. With
        # a selection-time filter the cross-model record is never considered, so
        # a same-model record above the threshold always wins when present. The
        # `record.model == model` assertion below is now belt-and-suspenders.
        best = self.storage.find_nearest(vector, model=model)
        if best is None:
            self.stats.misses += 1
            return CacheLookupResult(
                hit=False, payload=None, similarity=0.0, matched_record_key=None
            )

        record, similarity = best
        if similarity >= self.similarity_threshold and record.model == model:
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
        """Store `payload` under the embedding of `prompt`. Returns the cache key.

        `payload` must survive a JSON round-trip unchanged -- `str`, `int`,
        `float`, `bool`, `None`, `list`, and `dict` with string keys, nested
        arbitrarily. A persistent backend has to serialize it, so anything else
        either changes type between backends (a `tuple` is served back as a
        `list` by `RedisStorage`) or cannot be stored at all. Rejected here with
        a path-naming `ValueError` rather than at whichever backend happens to
        be configured (#192). See `_validate_payload`.
        """
        # The per-call override takes precedence over `default_ttl_s`, so it needs
        # the same guard the constructor applies (#36/#85). An unchecked negative
        # ttl stores `expires_at = now + ttl` in the past → the entry is evicted on
        # the next lookup with no diagnostic; a non-finite ttl corrupts `expires_at`
        # entirely. Reject at this seam rather than store a poisoned record.
        # `isinstance` first: a present-but-non-numeric ttl_s (str/None-typed
        # config) hits `math.isfinite` and raises a raw TypeError otherwise.
        # Reject `bool` too (an `int` subclass) — `ttl_s=True` (→1s) silently
        # expires the entry almost immediately, mirroring the constructor guard
        # and leh#178.
        if ttl_s is not None and (
            isinstance(ttl_s, bool)
            or not isinstance(ttl_s, (int, float))
            or not math.isfinite(ttl_s)
            or ttl_s <= 0
        ):
            raise ValueError(f"ttl_s must be a finite positive number; got {ttl_s!r}")
        ttl = ttl_s if ttl_s is not None else self.default_ttl_s
        expires_at = (self.now_fn() + ttl) if ttl is not None else None
        # Third input to this function, same seam, same standard as the two
        # above it: reject a payload the configured backend would silently
        # reshape or choke on, rather than store it (#192).
        _validate_payload(payload)
        raw_vector = self.embedder.embed(self._scoped_prompt(prompt, model))
        _validate_embedding(raw_vector, where="put embedder")
        vector = tuple(raw_vector)
        key = self._make_key(prompt, model)
        record = CacheRecord(
            key=key,
            vector=vector,
            payload=payload,
            tags=frozenset(tags),
            expires_at=expires_at,
            model=model,
        )
        self.storage.put(record)
        return key

    def invalidate(self, *, tag: str) -> int:
        """Drop every record carrying `tag`. Returns count dropped."""
        n = self.storage.invalidate_by_tag(tag)
        self.stats.invalidations += n
        return n

    def dump_stats_json(self, path: str | Path) -> None:
        """Write the current cache stats to ``path`` as JSON (#52).

        Atomic on POSIX — uses ``cost_optimizer.io_utils.atomic_write_text``
        so a Ctrl-C / disk-full / OOM between truncate and flush can't
        leave a log-tailer reading a half-written file. Byte-shape parity
        with ``PromptCacheWrapper.dump_aggregate_json`` from the prompt-
        cache layer: sorted keys, indent=2, trailing newline. Operators
        can tail / diff the file across restarts.
        """
        payload = json.dumps(self.stats.to_dict(), sort_keys=True, indent=2) + "\n"
        atomic_write_text(path, payload)

    def _scoped_prompt(self, prompt: str, model: str) -> str:
        # Embedding input includes the model id so the two-model "different
        # entries for the same prompt" property holds at the embedding layer
        # too (not just the synthetic key).
        #
        # Deliberately still a readable `[model=...] prompt` string, and
        # deliberately no longer shared with `_make_key` (#182). The two uses
        # have different constraints: a *key* must make field boundaries
        # unforgeable, which is a job for JSON escaping, while an *embedding
        # input* is text handed to a tokenizer, where a JSON blob would put
        # braces and quotes into the token stream and degrade the very
        # similarity signal this exists to produce. The model prefix's
        # embedding behaviour was also tuned against the 0.95 threshold in #125
        # and #133, so it is not free to change shape.
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

    The measurement is telemetry-neutral: it drives `cache.lookup`, which
    increments `cache.stats` (hits/misses/hit_rate), but D-007's whole point
    is that the offline helper must not bleed into production signals. Running
    it against a *populated* cache — the only useful way to measure a real FP
    rate — would otherwise inflate the very `hit_rate` the savings dashboard
    reports (and in the optimistic direction: offline lookups that hit make
    the reported hit_rate read higher than reality). Snapshot the stats
    counters up front and restore them in a `finally`, so the diagnostic
    reports the FP rate without touching the cache's reported telemetry.
    """
    stats_snapshot = replace(cache.stats)
    samples: list[FalsePositiveSample] = []
    fp_count = 0
    hit_count = 0
    try:
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
    finally:
        # Restore the pre-measurement telemetry: the offline diagnostic is
        # side-effect-free on `cache.stats` (D-007). `finally` so an exception
        # from a caller-supplied `call_model`/`equality` can't leak partial
        # measurement counts into production either.
        cache.stats = stats_snapshot
    rate = (fp_count / hit_count) if hit_count > 0 else 0.0
    return rate, samples
