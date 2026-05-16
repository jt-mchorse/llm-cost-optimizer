"""Tests for the Batch-API wrapper (#4, D-010)."""

from __future__ import annotations

import pytest

from cost_optimizer.batch import (
    BATCH_DISCOUNT_FACTOR,
    ENDED_FAILED,
    ENDED_SUCCEEDED,
    IN_PROGRESS,
    PENDING,
    AnthropicBatchBackend,
    BatchBackend,
    BatchCostQuote,
    BatchJobMeta,
    BatchRequest,
    BatchResultRow,
    IdempotencyConflict,
    InMemoryBatchBackend,
    JobNotComplete,
    JobNotFound,
    compare_realtime_vs_batch,
)


def _make_requests(n: int = 2, model: str = "fake-model") -> list[BatchRequest]:
    return [
        BatchRequest(custom_id=f"r-{i}", user=f"user content {i}", model=model, max_tokens=128)
        for i in range(n)
    ]


# ----------------------------------------------------------------------
# Lifecycle on InMemoryBatchBackend
# ----------------------------------------------------------------------


def test_submit_returns_pending_job_with_metadata():
    backend = InMemoryBatchBackend()
    job = backend.submit(_make_requests(3), idempotency_key="k-1")
    assert job.status == PENDING
    assert job.n_requests == 3
    assert job.idempotency_key == "k-1"
    assert job.job_id.startswith("batch_")
    assert job.created_at_iso.endswith("Z")


def test_poll_returns_current_status():
    backend = InMemoryBatchBackend()
    job = backend.submit(_make_requests(2), idempotency_key="k-2")
    assert backend.poll(job.job_id).status == PENDING
    backend.advance(job.job_id)
    assert backend.poll(job.job_id).status == IN_PROGRESS
    backend.advance(job.job_id)
    assert backend.poll(job.job_id).status == ENDED_SUCCEEDED


def test_results_returns_per_row_results_after_completion():
    backend = InMemoryBatchBackend()
    job = backend.submit(_make_requests(2), idempotency_key="k-3")
    rows = [
        BatchResultRow(custom_id="r-0", response_text="hello", prompt_tokens=10, completion_tokens=5),
        BatchResultRow(custom_id="r-1", response_text="world", prompt_tokens=12, completion_tokens=8),
    ]
    backend.complete(job.job_id, results=rows)
    out = backend.results(job.job_id)
    assert out == rows


def test_results_before_terminal_raises():
    backend = InMemoryBatchBackend()
    job = backend.submit(_make_requests(1), idempotency_key="k-4")
    with pytest.raises(JobNotComplete):
        backend.results(job.job_id)


def test_complete_with_default_results_populates_zero_token_rows():
    backend = InMemoryBatchBackend()
    job = backend.submit(_make_requests(3), idempotency_key="k-5")
    backend.complete(job.job_id)
    rows = backend.results(job.job_id)
    assert len(rows) == 3
    assert all(r.prompt_tokens == 0 and r.completion_tokens == 0 for r in rows)


def test_complete_failed_marks_status_ended_failed():
    backend = InMemoryBatchBackend()
    job = backend.submit(_make_requests(1), idempotency_key="k-6")
    backend.complete(job.job_id, failed=True)
    assert backend.poll(job.job_id).status == ENDED_FAILED


def test_poll_unknown_job_raises_jobnotfound():
    backend = InMemoryBatchBackend()
    with pytest.raises(JobNotFound):
        backend.poll("missing")


# ----------------------------------------------------------------------
# Idempotency (D-010)
# ----------------------------------------------------------------------


def test_idempotency_same_key_same_payload_returns_existing_job():
    backend = InMemoryBatchBackend()
    reqs = _make_requests(2)
    j1 = backend.submit(reqs, idempotency_key="k-idem")
    j2 = backend.submit(reqs, idempotency_key="k-idem")
    assert j1.job_id == j2.job_id


def test_idempotency_same_key_different_payload_raises_conflict():
    backend = InMemoryBatchBackend()
    backend.submit(_make_requests(2), idempotency_key="k-clash")
    with pytest.raises(IdempotencyConflict, match="k-clash"):
        backend.submit(_make_requests(3), idempotency_key="k-clash")


def test_idempotency_different_keys_produce_different_jobs():
    backend = InMemoryBatchBackend()
    j1 = backend.submit(_make_requests(2), idempotency_key="k-a")
    j2 = backend.submit(_make_requests(2), idempotency_key="k-b")
    assert j1.job_id != j2.job_id


def test_idempotency_payload_hash_ignores_request_ordering_inside_batch():
    """Two batches with the same requests in a different order are semantically distinct.

    The payload hash is content-only and order-sensitive — we don't try
    to canonicalize across orderings. That's a deliberate choice: a
    caller who reorders their batch is materially submitting a
    different workload (different custom_id ↔ position mapping).
    """
    backend = InMemoryBatchBackend()
    reqs = _make_requests(3)
    backend.submit(reqs, idempotency_key="k-order")
    reordered = list(reversed(reqs))
    with pytest.raises(IdempotencyConflict):
        backend.submit(reordered, idempotency_key="k-order")


# ----------------------------------------------------------------------
# Submit-time validation
# ----------------------------------------------------------------------


def test_submit_rejects_empty_request_list():
    backend = InMemoryBatchBackend()
    with pytest.raises(ValueError, match="at least one request"):
        backend.submit([], idempotency_key="k")


def test_submit_rejects_blank_idempotency_key():
    backend = InMemoryBatchBackend()
    with pytest.raises(ValueError, match="non-empty string"):
        backend.submit(_make_requests(1), idempotency_key="")
    with pytest.raises(ValueError, match="non-empty string"):
        backend.submit(_make_requests(1), idempotency_key="   ")


def test_submit_rejects_duplicate_custom_ids():
    backend = InMemoryBatchBackend()
    bad = [
        BatchRequest(custom_id="dup", user="a", model="m"),
        BatchRequest(custom_id="dup", user="b", model="m"),
    ]
    with pytest.raises(ValueError, match="duplicate custom_ids"):
        backend.submit(bad, idempotency_key="k-dup")


# ----------------------------------------------------------------------
# Cost comparison
# ----------------------------------------------------------------------


def _quote() -> BatchCostQuote:
    """Fixture price quote — explicitly synthetic; not real list prices."""
    return BatchCostQuote(model="fake-big", input_per_mtok=10.0, output_per_mtok=40.0)


def test_compare_realtime_vs_batch_known_math():
    rows = [
        BatchResultRow(custom_id="r-0", response_text="x", prompt_tokens=1_000_000, completion_tokens=500_000),
    ]
    cmp_ = compare_realtime_vs_batch(rows, prices={"fake-big": _quote()})
    # Realtime: 1M tok * $10/MTok + 0.5M * $40/MTok = $10 + $20 = $30.
    assert cmp_.realtime_usd == pytest.approx(30.0)
    # Batch at 50% discount = $15.
    assert cmp_.batch_usd == pytest.approx(15.0)
    assert cmp_.savings_usd == pytest.approx(15.0)
    assert cmp_.savings_pct == pytest.approx(0.5)
    assert cmp_.n_rows == 1


def test_compare_realtime_vs_batch_uses_documented_discount_constant():
    """The default discount must match the documented BATCH_DISCOUNT_FACTOR."""
    rows = [BatchResultRow(custom_id="r-0", response_text="x", prompt_tokens=1_000_000, completion_tokens=0)]
    cmp_ = compare_realtime_vs_batch(rows, prices={"fake-big": _quote()})
    assert cmp_.batch_usd == pytest.approx(10.0 * BATCH_DISCOUNT_FACTOR)


def test_compare_realtime_vs_batch_skips_failed_rows():
    rows = [
        BatchResultRow(custom_id="ok", response_text="x", prompt_tokens=1_000_000, completion_tokens=0),
        BatchResultRow(custom_id="err", response_text=None, prompt_tokens=0, completion_tokens=0, error="boom"),
    ]
    cmp_ = compare_realtime_vs_batch(rows, prices={"fake-big": _quote()})
    assert cmp_.n_rows == 1
    assert cmp_.realtime_usd == pytest.approx(10.0)


def test_compare_realtime_vs_batch_multi_model_requires_model_of():
    rows = [
        BatchResultRow(custom_id="a", response_text="x", prompt_tokens=1_000_000, completion_tokens=0),
    ]
    prices = {
        "fake-big": _quote(),
        "fake-small": BatchCostQuote("fake-small", 1.0, 4.0),
    }
    with pytest.raises(ValueError, match="exactly one entry"):
        compare_realtime_vs_batch(rows, prices=prices)
    # With model_of supplied, the comparison runs against the named model.
    cmp_ = compare_realtime_vs_batch(rows, prices=prices, model_of={"a": "fake-small"})
    assert cmp_.realtime_usd == pytest.approx(1.0)


def test_compare_realtime_vs_batch_unknown_model_raises():
    rows = [BatchResultRow(custom_id="a", response_text="x", prompt_tokens=10, completion_tokens=0)]
    # model_of points to a model that isn't in `prices` → KeyError on the lookup.
    with pytest.raises(KeyError, match="fake-missing"):
        compare_realtime_vs_batch(
            rows,
            prices={"fake-big": _quote()},
            model_of={"a": "fake-missing"},
        )


def test_compare_realtime_vs_batch_model_of_missing_custom_id_raises():
    rows = [BatchResultRow(custom_id="a", response_text="x", prompt_tokens=10, completion_tokens=0)]
    with pytest.raises(KeyError, match="model_of"):
        compare_realtime_vs_batch(
            rows,
            prices={"fake-big": _quote(), "fake-small": BatchCostQuote("fake-small", 1.0, 4.0)},
            model_of={"other": "fake-big"},
        )


def test_compare_realtime_vs_batch_empty_rows_returns_zero():
    cmp_ = compare_realtime_vs_batch([], prices={"fake-big": _quote()})
    assert cmp_.realtime_usd == 0.0
    assert cmp_.savings_pct == 0.0


def test_compare_realtime_vs_batch_rejects_out_of_range_discount():
    rows = [BatchResultRow(custom_id="a", response_text="x", prompt_tokens=10, completion_tokens=0)]
    with pytest.raises(ValueError, match="0.0, 1.0"):
        compare_realtime_vs_batch(rows, prices={"fake-big": _quote()}, discount=-0.1)
    with pytest.raises(ValueError, match="0.0, 1.0"):
        compare_realtime_vs_batch(rows, prices={"fake-big": _quote()}, discount=1.5)


# ----------------------------------------------------------------------
# Protocol conformance + AnthropicBatchBackend (duck-typed, with a fake client)
# ----------------------------------------------------------------------


def test_inmemory_backend_conforms_to_backend_protocol():
    """Mypy-style: InMemoryBatchBackend must be usable where BatchBackend is."""
    backend: BatchBackend = InMemoryBatchBackend()
    job = backend.submit(_make_requests(1), idempotency_key="k-proto")
    assert isinstance(job, BatchJobMeta)


class _FakeUsage:
    def __init__(self, prompt: int, completion: int) -> None:
        self.input_tokens = prompt
        self.output_tokens = completion


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str, prompt: int, completion: int) -> None:
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage(prompt, completion)


class _FakeResult:
    def __init__(self, *, succeeded: bool, message: _FakeMessage | None = None, error: str | None = None) -> None:
        self.type = "succeeded" if succeeded else "errored"
        self.message = message
        self.error = error


class _FakeResultEntry:
    def __init__(self, custom_id: str, result: _FakeResult) -> None:
        self.custom_id = custom_id
        self.result = result


class _FakeBatch:
    def __init__(self, *, id_: str, status: str, n: int) -> None:
        self.id = id_
        self.processing_status = status
        self.created_at = "2026-05-16T00:00:00Z"

        class _Counts:
            pass

        counts = _Counts()
        counts.processing = 0
        counts.succeeded = n
        counts.errored = 0
        counts.canceled = 0
        self.request_counts = counts


class _FakeBatches:
    def __init__(self) -> None:
        self._created: list[dict] = []
        self.created_obj = _FakeBatch(id_="anth_batch_1", status="ended", n=2)

    def create(self, *, requests, extra_headers):  # noqa: ANN001
        self._created.append({"requests": requests, "extra_headers": extra_headers})
        return self.created_obj

    def retrieve(self, job_id):  # noqa: ANN001
        assert job_id == "anth_batch_1"
        return self.created_obj

    def results(self, job_id):  # noqa: ANN001
        return [
            _FakeResultEntry(
                "r-0",
                _FakeResult(succeeded=True, message=_FakeMessage("hello", prompt=10, completion=5)),
            ),
            _FakeResultEntry(
                "r-1",
                _FakeResult(succeeded=False, error="rate_limit"),
            ),
        ]


class _FakeMessagesNamespace:
    def __init__(self) -> None:
        self.batches = _FakeBatches()


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _FakeMessagesNamespace()


def test_anthropic_backend_submit_forwards_idempotency_header():
    client = _FakeClient()
    backend = AnthropicBatchBackend(client)
    job = backend.submit(_make_requests(2), idempotency_key="k-anth-1")
    assert job.job_id == "anth_batch_1"
    assert job.idempotency_key == "k-anth-1"
    assert client.messages.batches._created[0]["extra_headers"] == {"Idempotency-Key": "k-anth-1"}
    sdk_requests = client.messages.batches._created[0]["requests"]
    assert sdk_requests[0]["custom_id"] == "r-0"
    assert sdk_requests[0]["params"]["model"] == "fake-model"
    assert sdk_requests[0]["params"]["messages"][0]["content"] == "user content 0"


def test_anthropic_backend_maps_status_and_results():
    backend = AnthropicBatchBackend(_FakeClient())
    backend.submit(_make_requests(2), idempotency_key="k-anth-2")
    rows = backend.results("anth_batch_1")
    assert len(rows) == 2
    assert rows[0].response_text == "hello"
    assert rows[0].prompt_tokens == 10
    assert rows[0].completion_tokens == 5
    assert rows[0].error is None
    assert rows[1].response_text is None
    assert rows[1].error == "rate_limit"


def test_anthropic_backend_rejects_bad_client_shape():
    class _NoMessages:
        pass

    with pytest.raises(TypeError, match="messages.batches"):
        AnthropicBatchBackend(_NoMessages())


def test_anthropic_backend_rejects_none_client():
    with pytest.raises(ValueError, match="requires a client"):
        AnthropicBatchBackend(None)
