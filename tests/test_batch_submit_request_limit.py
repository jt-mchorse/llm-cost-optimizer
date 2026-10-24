"""batch.submit refuses > 100,000 requests on both backends (#351).

The Message Batches API rejects a whole batch over 100,000 requests with a
413 request_too_large before any request is processed.  Previously
_validate_submit_args (shared by both backends) had no such guard, so:

- InMemoryBatchBackend accepted 100,001 requests and stored them in memory.
- AnthropicBatchBackend sent 100,001 requests to the API and received a 413.

Both now refuse before any work is done, matching the existing custom_id
pattern and duplicate checks that were written for the same reason (#329).
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest

from cost_optimizer.batch import (
    BATCH_MAX_REQUESTS,
    AnthropicBatchBackend,
    BatchRequest,
    InMemoryBatchBackend,
    _validate_submit_args,
)


def _make_requests(n: int) -> list[BatchRequest]:
    """Build *n* BatchRequests with unique custom_ids."""
    return [
        BatchRequest(custom_id=f"r{i}", user="hi", model="claude-haiku-4-5")
        for i in range(n)
    ]


class TestBatchMaxRequestsConstant:
    def test_value_is_100_000(self) -> None:
        assert BATCH_MAX_REQUESTS == 100_000


class TestValidateSubmitArgsDirect:
    """_validate_submit_args is the shared guard — test it directly."""

    def test_100_000_requests_accepted(self) -> None:
        reqs = _make_requests(BATCH_MAX_REQUESTS)
        # Should not raise.
        _validate_submit_args(reqs, "key-ok")

    def test_100_001_requests_refused(self) -> None:
        reqs = _make_requests(BATCH_MAX_REQUESTS + 1)
        with pytest.raises(ValueError, match="100,001"):
            _validate_submit_args(reqs, "key-over")

    def test_error_message_mentions_limit(self) -> None:
        reqs = _make_requests(BATCH_MAX_REQUESTS + 1)
        with pytest.raises(ValueError, match="100,000"):
            _validate_submit_args(reqs, "key-over")

    def test_error_message_mentions_api_docs(self) -> None:
        reqs = _make_requests(BATCH_MAX_REQUESTS + 1)
        with pytest.raises(ValueError, match="docs.anthropic.com"):
            _validate_submit_args(reqs, "key-over")


class TestInMemoryBatchBackendLimit:
    """InMemoryBatchBackend.submit must also enforce the limit (via _validate_submit_args)."""

    def test_exactly_at_limit_submits(self) -> None:
        backend = InMemoryBatchBackend()
        reqs = _make_requests(BATCH_MAX_REQUESTS)
        job = backend.submit(reqs, idempotency_key="at-limit")
        assert job.n_requests == BATCH_MAX_REQUESTS

    def test_one_over_limit_raises_before_any_storage(self) -> None:
        backend = InMemoryBatchBackend()
        reqs = _make_requests(BATCH_MAX_REQUESTS + 1)
        with pytest.raises(ValueError, match="100,001"):
            backend.submit(reqs, idempotency_key="over-limit")
        # Nothing was stored.
        assert len(backend._jobs) == 0

    def test_far_over_limit_raises(self) -> None:
        backend = InMemoryBatchBackend()
        reqs = _make_requests(200_000)
        with pytest.raises(ValueError, match="200,000"):
            backend.submit(reqs, idempotency_key="way-over")


class TestAnthropicBatchBackendLimit:
    """AnthropicBatchBackend.submit must also enforce the limit (via _validate_submit_args)
    and must NOT make any network call when the limit is exceeded."""

    def _backend(self) -> tuple[AnthropicBatchBackend, MagicMock]:
        batches_mock = MagicMock()
        client = types.SimpleNamespace(
            messages=types.SimpleNamespace(batches=batches_mock)
        )
        return AnthropicBatchBackend(client), batches_mock  # type: ignore[arg-type]

    def test_one_over_limit_raises_with_no_network_call(self) -> None:
        backend, batches_mock = self._backend()
        reqs = _make_requests(BATCH_MAX_REQUESTS + 1)
        with pytest.raises(ValueError, match="100,001"):
            backend.submit(reqs, idempotency_key="over-net")
        # create() was never called — no API round-trip.
        batches_mock.create.assert_not_called()

    def test_exactly_at_limit_calls_create(self) -> None:
        backend, batches_mock = self._backend()
        # Provide a minimal fake response so _from_sdk_batch doesn't crash.
        batches_mock.create.return_value = types.SimpleNamespace(
            id="msgbatch_ok",
            processing_status="in_progress",
            cancel_initiated_at=None,
            request_counts=types.SimpleNamespace(
                processing=BATCH_MAX_REQUESTS,
                succeeded=0,
                errored=0,
                canceled=0,
                expired=0,
            ),
            created_at="2026-10-22T00:00:00Z",
        )
        reqs = _make_requests(BATCH_MAX_REQUESTS)
        backend.submit(reqs, idempotency_key="at-limit-net")
        batches_mock.create.assert_called_once()
