"""Batch-API wrapper for non-realtime LLM workloads.

Follows the same seam pattern as the rest of the toolkit (D-002): a
single-method Protocol, a dep-free in-memory default that drives the
hermetic tests, and a lazy-imported Anthropic-backed production
binding behind the existing ``[anthropic]`` extra.

Lifecycle:

    backend = InMemoryBatchBackend()           # or AnthropicBatchBackend()
    job = backend.submit(requests, idempotency_key="2026-05-16-batch-1")
    while job.status not in {"ended_succeeded", "ended_failed", "ended_canceled"}:
        job = backend.poll(job.job_id)
    rows = backend.results(job.job_id)

Idempotency (D-010): the caller supplies an ``idempotency_key`` —
typically a deterministic id derived from the workload (e.g.,
``f"{shard_id}-{date}"``). Resubmitting *the same payload* with *the
same key* returns the same ``job_id``; resubmitting *a different
payload* with *the same key* raises ``IdempotencyConflict``. The
payload hash is content-only (request count, custom_ids, model,
prompts, max_tokens) so a duplicate retry from a flaky caller
doesn't double-charge.

Cost reporting: ``compare_realtime_vs_batch(rows, prices)`` applies
``BATCH_DISCOUNT_FACTOR = 0.5`` to both prompt and completion tokens
on the batch side, sourced from Anthropic's public Batch API discount
(cite docs on use; rates move). Prices are caller-supplied — no
defaults shipped, same posture as D-003 for prompt caching.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

# Anthropic's public batch API charges 50% of standard input/output
# rates on both axes; the same factor applies across the current Claude
# family at time of writing. Source the up-to-date figure from
# https://docs.anthropic.com/en/api/messages-batches at use time —
# this constant is the documented value as of 2026-05.
BATCH_DISCOUNT_FACTOR = 0.5


# ----------------------------------------------------------------------
# Public types
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class BatchRequest:
    """One row in a batch submission.

    ``custom_id`` is an arbitrary stable identifier the caller chooses
    so they can correlate the response row back to their own work
    item; it does not need to be globally unique across batches, but
    *within* one batch it must be unique (the backend rejects
    duplicates on submit).
    """

    custom_id: str
    user: str  # the user-content for this request
    model: str
    max_tokens: int = 1024
    system: str | None = None  # optional system prompt

    def __post_init__(self) -> None:
        # max_tokens=0 silently submits a 400-bound payload; negative or
        # non-int (incl. bool, which is an int subclass in Python) makes
        # the in-memory backend store garbage. Mirrors the contract-tightening
        # sweep (pricing.py, router.py).
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
            raise ValueError(
                f"BatchRequest.max_tokens must be an int >= 1; got {self.max_tokens!r}"
            )
        if self.max_tokens < 1:
            raise ValueError(f"BatchRequest.max_tokens must be an int >= 1; got {self.max_tokens}")


@dataclass(frozen=True)
class BatchResultRow:
    """One result row coming back from a completed batch.

    ``error`` is populated only when the per-row request failed; on
    success it's ``None`` and the response/usage fields are populated.
    """

    custom_id: str
    response_text: str | None
    prompt_tokens: int
    completion_tokens: int
    error: str | None = None

    def __post_init__(self) -> None:
        # Zero is valid (failed rows surface as 0/0 — see tests/test_batch.py
        # line 88), but negative or non-int would silently invert cost math
        # when aggregated upstream. Bool is rejected explicitly because
        # `bool` is an `int` subclass in Python.
        for name, value in (
            ("prompt_tokens", self.prompt_tokens),
            ("completion_tokens", self.completion_tokens),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"BatchResultRow.{name} must be an int >= 0; got {value!r}")
            if value < 0:
                raise ValueError(f"BatchResultRow.{name} must be an int >= 0; got {value}")


# Status strings deliberately match the Anthropic Messages-Batch API's
# canonical processing-status values so the in-memory backend behaves
# the same way the production backend does.
PENDING = "pending"
IN_PROGRESS = "in_progress"
ENDED_SUCCEEDED = "ended_succeeded"
ENDED_FAILED = "ended_failed"
ENDED_CANCELED = "ended_canceled"
TERMINAL_STATUSES = frozenset({ENDED_SUCCEEDED, ENDED_FAILED, ENDED_CANCELED})


@dataclass(frozen=True)
class BatchJobMeta:
    """Metadata-only view of a submitted batch job.

    Doesn't carry the request payload or the results; callers retrieve
    those via ``backend.results(job_id)`` once the status is terminal.
    """

    job_id: str
    idempotency_key: str
    status: str
    n_requests: int
    created_at_iso: str

    def __post_init__(self) -> None:
        # A zero-request batch is meaningless. The cast in `_extract_meta`
        # (`int(n or 0)`) bottoms out at 0 if the response omits the count
        # field — that's a backend-shape bug, not a valid batch, so reject
        # it here at the construction boundary.
        if isinstance(self.n_requests, bool) or not isinstance(self.n_requests, int):
            raise ValueError(
                f"BatchJobMeta.n_requests must be an int >= 1; got {self.n_requests!r}"
            )
        if self.n_requests < 1:
            raise ValueError(f"BatchJobMeta.n_requests must be an int >= 1; got {self.n_requests}")


class IdempotencyConflict(Exception):
    """Raised when the same idempotency key is reused with a different payload."""


class JobNotFound(KeyError):
    """Raised when ``poll`` or ``results`` is called with an unknown ``job_id``."""


class JobNotComplete(Exception):
    """Raised when ``results`` is called before the job has reached a terminal status."""


# ----------------------------------------------------------------------
# Protocol
# ----------------------------------------------------------------------


class BatchBackend(Protocol):
    """Single-method-per-step backend so callers can swap real vs in-memory."""

    def submit(self, requests: Sequence[BatchRequest], *, idempotency_key: str) -> BatchJobMeta:
        """Submit a batch. Returns the assigned job metadata."""

    def poll(self, job_id: str) -> BatchJobMeta:
        """Return current metadata for the job; status reflects progress."""

    def results(self, job_id: str) -> list[BatchResultRow]:
        """Return the per-request results. Raises ``JobNotComplete`` if not terminal."""


# ----------------------------------------------------------------------
# Payload canonicalization for idempotency
# ----------------------------------------------------------------------


def _canonical_payload_hash(requests: Sequence[BatchRequest]) -> str:
    """Stable SHA-256 hash of the canonical batch payload.

    Used by the in-memory backend to detect "same key, different
    payload" so a caller who reuses an idempotency key by accident
    gets a loud failure rather than silent overwrite.
    """
    canonical = [
        {
            "custom_id": r.custom_id,
            "user": r.user,
            "system": r.system,
            "model": r.model,
            "max_tokens": r.max_tokens,
        }
        for r in requests
    ]
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# ----------------------------------------------------------------------
# In-memory backend (hermetic CI; test-driven workflow)
# ----------------------------------------------------------------------


@dataclass
class _StoredJob:
    meta: BatchJobMeta
    payload_hash: str
    requests: tuple[BatchRequest, ...]
    results: list[BatchResultRow] = field(default_factory=list)


class InMemoryBatchBackend:
    """Dep-free in-memory ``BatchBackend`` for hermetic tests and offline demos.

    The backend keeps every submitted job in process memory; calling
    ``submit`` with the same ``idempotency_key`` and the same payload
    returns the existing job's metadata. Different payload + same key
    raises ``IdempotencyConflict``.

    Jobs start in ``pending`` status. To exercise the lifecycle in
    tests, ``advance(job_id)`` walks the status forward by one step,
    and ``complete(job_id, results)`` jumps it directly to
    ``ended_succeeded`` with the supplied per-row results (or
    ``ended_failed`` if a global error is supplied).

    Production callers should not depend on the test helpers — those
    are exposed only so the lifecycle can be driven deterministically
    in unit tests.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, _StoredJob] = {}
        self._by_idempotency: dict[str, str] = {}
        self._counter = 0

    # ---- Backend protocol ---------------------------------------------------

    def submit(self, requests: Sequence[BatchRequest], *, idempotency_key: str) -> BatchJobMeta:
        if not requests:
            raise ValueError("submit requires at least one request")
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        custom_ids = [r.custom_id for r in requests]
        if len(set(custom_ids)) != len(custom_ids):
            dups = sorted(c for c in set(custom_ids) if custom_ids.count(c) > 1)
            raise ValueError(f"duplicate custom_ids within one batch: {dups}")

        payload_hash = _canonical_payload_hash(requests)
        if idempotency_key in self._by_idempotency:
            existing_id = self._by_idempotency[idempotency_key]
            existing = self._jobs[existing_id]
            if existing.payload_hash != payload_hash:
                raise IdempotencyConflict(
                    f"idempotency_key={idempotency_key!r} already used for a different payload "
                    f"(existing job_id={existing_id!r})"
                )
            return existing.meta

        self._counter += 1
        job_id = f"batch_{self._counter:08d}"
        created_at = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        meta = BatchJobMeta(
            job_id=job_id,
            idempotency_key=idempotency_key,
            status=PENDING,
            n_requests=len(requests),
            created_at_iso=created_at,
        )
        self._jobs[job_id] = _StoredJob(
            meta=meta, payload_hash=payload_hash, requests=tuple(requests)
        )
        self._by_idempotency[idempotency_key] = job_id
        return meta

    def poll(self, job_id: str) -> BatchJobMeta:
        try:
            return self._jobs[job_id].meta
        except KeyError as e:
            raise JobNotFound(f"unknown job_id={job_id!r}") from e

    def results(self, job_id: str) -> list[BatchResultRow]:
        try:
            stored = self._jobs[job_id]
        except KeyError as e:
            raise JobNotFound(f"unknown job_id={job_id!r}") from e
        if stored.meta.status not in TERMINAL_STATUSES:
            raise JobNotComplete(
                f"job_id={job_id!r} status={stored.meta.status!r}; results not available"
            )
        return list(stored.results)

    # ---- Test helpers -------------------------------------------------------

    def advance(self, job_id: str) -> BatchJobMeta:
        """Walk the job's status forward one step (test-driven lifecycle)."""
        stored = self._jobs[job_id]
        next_status = {
            PENDING: IN_PROGRESS,
            IN_PROGRESS: ENDED_SUCCEEDED,
        }.get(stored.meta.status)
        if next_status is None:
            raise ValueError(f"job_id={job_id!r} is already in a terminal status")
        new_meta = BatchJobMeta(
            job_id=stored.meta.job_id,
            idempotency_key=stored.meta.idempotency_key,
            status=next_status,
            n_requests=stored.meta.n_requests,
            created_at_iso=stored.meta.created_at_iso,
        )
        stored.meta = new_meta
        return new_meta

    def complete(
        self, job_id: str, *, results: Iterable[BatchResultRow] | None = None, failed: bool = False
    ) -> BatchJobMeta:
        """Jump the job to a terminal status with the supplied per-row results.

        ``results`` defaults to one zero-token success row per request when
        omitted, so most tests can drive the lifecycle in a single line.
        """
        stored = self._jobs[job_id]
        if results is None:
            rows = [
                BatchResultRow(
                    custom_id=r.custom_id,
                    response_text="",
                    prompt_tokens=0,
                    completion_tokens=0,
                )
                for r in stored.requests
            ]
        else:
            rows = list(results)
        stored.results = rows
        new_meta = BatchJobMeta(
            job_id=stored.meta.job_id,
            idempotency_key=stored.meta.idempotency_key,
            status=ENDED_FAILED if failed else ENDED_SUCCEEDED,
            n_requests=stored.meta.n_requests,
            created_at_iso=stored.meta.created_at_iso,
        )
        stored.meta = new_meta
        return new_meta


# ----------------------------------------------------------------------
# Anthropic-backed production backend (duck-typed; D-002)
# ----------------------------------------------------------------------


class AnthropicBatchBackend:
    """Production binding over the Anthropic Messages-Batch API.

    The Anthropic SDK is duck-typed per D-002: the backend takes a
    pre-constructed client (any object with the ``messages.batches``
    surface), so the package imports without ``anthropic`` installed.
    Operator code constructs the SDK client themselves and hands it in.

    Real-API smoke is operator-triggered with ``ANTHROPIC_API_KEY`` and
    a budget; CI uses ``InMemoryBatchBackend``.
    """

    def __init__(self, client: Any) -> None:
        if client is None:
            raise ValueError(
                "AnthropicBatchBackend requires a client (duck-typed; pass your SDK client)"
            )
        # Stash the surface we need rather than the whole client so test fakes can
        # implement just `messages.batches.create / retrieve / results`.
        try:
            self._batches = client.messages.batches
        except AttributeError as e:
            raise TypeError(
                "client must expose `.messages.batches` (Anthropic SDK shape); got "
                f"{type(client).__name__}"
            ) from e

    def submit(self, requests: Sequence[BatchRequest], *, idempotency_key: str) -> BatchJobMeta:
        if not requests:
            raise ValueError("submit requires at least one request")
        if not idempotency_key or not idempotency_key.strip():
            raise ValueError("idempotency_key must be a non-empty string")
        sdk_requests = [_to_sdk_request(r) for r in requests]
        resp = self._batches.create(
            requests=sdk_requests,
            extra_headers={"Idempotency-Key": idempotency_key},
        )
        return _from_sdk_batch(resp, idempotency_key=idempotency_key)

    def poll(self, job_id: str) -> BatchJobMeta:
        resp = self._batches.retrieve(job_id)
        return _from_sdk_batch(resp, idempotency_key=getattr(resp, "_idempotency_key", "") or "")

    def results(self, job_id: str) -> list[BatchResultRow]:
        meta = self.poll(job_id)
        if meta.status not in TERMINAL_STATUSES:
            raise JobNotComplete(f"job_id={job_id!r} status={meta.status!r}; results not available")
        rows: list[BatchResultRow] = []
        for entry in self._batches.results(job_id):
            rows.append(_from_sdk_result_row(entry))
        return rows


def _to_sdk_request(r: BatchRequest) -> dict[str, Any]:
    """Convert a BatchRequest to the SDK's expected dict shape."""
    params: dict[str, Any] = {
        "model": r.model,
        "max_tokens": r.max_tokens,
        "messages": [{"role": "user", "content": r.user}],
    }
    if r.system is not None:
        params["system"] = r.system
    return {"custom_id": r.custom_id, "params": params}


def _from_sdk_batch(resp: Any, *, idempotency_key: str) -> BatchJobMeta:
    """Project an SDK Batch object to ``BatchJobMeta``."""
    status_raw = (
        getattr(resp, "processing_status", None) or getattr(resp, "status", None) or "pending"
    )
    status = {
        "pending": PENDING,
        "in_progress": IN_PROGRESS,
        "ended": ENDED_SUCCEEDED,
        "canceling": IN_PROGRESS,
        "canceled": ENDED_CANCELED,
        "failed": ENDED_FAILED,
    }.get(str(status_raw), str(status_raw))
    n_requests = getattr(resp, "request_counts", None)
    if n_requests is not None:
        # SDK exposes processing/succeeded/errored/canceled counts; total is the sum.
        n = sum(
            getattr(n_requests, k, 0) for k in ("processing", "succeeded", "errored", "canceled")
        )
    else:
        n = getattr(resp, "n_requests", 0)
    return BatchJobMeta(
        job_id=getattr(resp, "id", ""),
        idempotency_key=idempotency_key,
        status=status,
        n_requests=int(n or 0),
        created_at_iso=str(getattr(resp, "created_at", "") or ""),
    )


def _from_sdk_result_row(entry: Any) -> BatchResultRow:
    """Project an SDK batch result entry to ``BatchResultRow``."""
    custom_id = getattr(entry, "custom_id", "")
    result = getattr(entry, "result", None)
    if result is None:
        return BatchResultRow(
            custom_id=custom_id,
            response_text=None,
            prompt_tokens=0,
            completion_tokens=0,
            error="missing result",
        )
    if getattr(result, "type", None) != "succeeded":
        return BatchResultRow(
            custom_id=custom_id,
            response_text=None,
            prompt_tokens=0,
            completion_tokens=0,
            error=str(getattr(result, "error", None) or getattr(result, "type", "unknown")),
        )
    message = getattr(result, "message", None)
    content = getattr(message, "content", []) or []
    text_parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            text_parts.append(text)
    usage = getattr(message, "usage", None)
    prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    return BatchResultRow(
        custom_id=custom_id,
        response_text="".join(text_parts),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


# ----------------------------------------------------------------------
# Cost comparison
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class BatchCostQuote:
    """Per-model rate quoted by the caller for the cost comparison.

    Prices are caller-supplied — no defaults shipped (D-003 posture
    extended to the batch axis): a downstream operator's actual
    contract may differ from public list, and we refuse to fabricate.
    """

    model: str
    input_per_mtok: float
    output_per_mtok: float

    def __post_init__(self) -> None:
        # Mirror `ModelPricing.__post_init__` (#71) on the batch axis: a
        # negative rate inverts the sign of the savings math in
        # `compare_realtime_vs_batch`, and a non-finite rate (NaN/+/-Inf)
        # poisons `realtime_total`/`batch_total` → the rounded NaN/Inf
        # propagates into `CostComparison` and onto the savings dashboard.
        # The `savings_pct … if realtime_total > 0 else 0.0` guard makes it
        # worse by masking the percentage to a clean 0.0 while the dollar
        # fields carry garbage. Sign-only checks miss non-finite values
        # (`NaN < 0.0` and `inf < 0.0` are both False), so widen to finiteness
        # like the portfolio-wide sweep already applied to `ModelPricing`.
        if not isinstance(self.model, str) or not self.model:
            raise ValueError(f"model must be a non-empty string; got {self.model!r}")
        for name, value in (
            ("input_per_mtok", self.input_per_mtok),
            ("output_per_mtok", self.output_per_mtok),
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be a finite number >= 0.0; got {value}")


@dataclass(frozen=True)
class CostComparison:
    realtime_usd: float
    batch_usd: float
    savings_usd: float
    savings_pct: float
    n_rows: int


def compare_realtime_vs_batch(
    rows: Sequence[BatchResultRow],
    prices: dict[str, BatchCostQuote],
    *,
    discount: float = BATCH_DISCOUNT_FACTOR,
    model_of: dict[str, str] | None = None,
) -> CostComparison:
    """Compare realtime vs batch USD totals for the per-row token counts.

    ``prices`` is a dict mapping model id → ``BatchCostQuote``.
    ``model_of`` maps ``custom_id → model`` so the function can pick
    the right rate per row when a batch spans multiple models; if
    omitted, the function assumes a single-model batch and reads the
    model from the first matching ``BatchCostQuote`` (raises if there
    are ≠1 quote entries).
    """
    if not 0.0 <= discount <= 1.0:
        raise ValueError(f"discount must be in [0.0, 1.0]; got {discount}")
    if not rows:
        return CostComparison(0.0, 0.0, 0.0, 0.0, 0)
    if model_of is None:
        if len(prices) != 1:
            raise ValueError(
                "model_of=None requires exactly one entry in prices "
                f"(got {len(prices)}); pass model_of for multi-model batches"
            )
        only_model = next(iter(prices.values())).model
        model_of = {r.custom_id: only_model for r in rows}

    realtime_total = 0.0
    batch_total = 0.0
    counted_rows = 0
    for row in rows:
        if row.error is not None:
            continue  # failed rows aren't billed for either path
        model = model_of.get(row.custom_id)
        if model is None:
            raise KeyError(f"model_of missing entry for custom_id={row.custom_id!r}")
        quote = prices.get(model)
        if quote is None:
            raise KeyError(f"prices missing entry for model={model!r}")
        prompt_usd = row.prompt_tokens * quote.input_per_mtok / 1_000_000
        completion_usd = row.completion_tokens * quote.output_per_mtok / 1_000_000
        realtime_total += prompt_usd + completion_usd
        batch_total += (prompt_usd + completion_usd) * discount
        counted_rows += 1

    savings = realtime_total - batch_total
    savings_pct = (savings / realtime_total) if realtime_total > 0 else 0.0
    return CostComparison(
        realtime_usd=round(realtime_total, 6),
        batch_usd=round(batch_total, 6),
        savings_usd=round(savings, 6),
        savings_pct=round(savings_pct, 4),
        n_rows=counted_rows,
    )


__all__ = [
    "AnthropicBatchBackend",
    "BATCH_DISCOUNT_FACTOR",
    "BatchBackend",
    "BatchCostQuote",
    "BatchJobMeta",
    "BatchRequest",
    "BatchResultRow",
    "CostComparison",
    "ENDED_CANCELED",
    "ENDED_FAILED",
    "ENDED_SUCCEEDED",
    "IN_PROGRESS",
    "IdempotencyConflict",
    "InMemoryBatchBackend",
    "JobNotComplete",
    "JobNotFound",
    "PENDING",
    "TERMINAL_STATUSES",
    "compare_realtime_vs_batch",
]
