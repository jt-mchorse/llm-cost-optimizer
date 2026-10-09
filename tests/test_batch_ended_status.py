"""An ended Anthropic batch polls as how it ended, not always as succeeded (#285).

The SDK's `processing_status` is `Literal["in_progress", "canceling", "ended"]`
(the published `anthropic` wheel's `types/messages/message_batch.py`), so the
`"canceled"`/`"failed"` arms of `_from_sdk_batch`'s map could never fire.
Measured on `main` through `AnthropicBatchBackend.poll`:

    expired 3 of 3                        -> ended_succeeded
    canceled 3 of 3 (cancel_initiated_at) -> ended_succeeded
    errored 3 of 3                        -> ended_succeeded

while `InMemoryBatchBackend.complete(failed=True)` reports `ended_failed`.
"""

from __future__ import annotations

import types

import pytest

from cost_optimizer.batch import (
    ENDED_CANCELED,
    ENDED_FAILED,
    ENDED_SUCCEEDED,
    IN_PROGRESS,
    TERMINAL_STATUSES,
    AnthropicBatchBackend,
)


def _resp(status: str = "ended", cancel: str | None = None, **counts: int) -> types.SimpleNamespace:
    base = {"processing": 0, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0}
    return types.SimpleNamespace(
        id="msgbatch_x",
        processing_status=status,
        cancel_initiated_at=cancel,
        request_counts=types.SimpleNamespace(**{**base, **counts}),
        created_at="2026-10-09T00:00:00Z",
    )


def _entry(custom_id: str, kind: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(custom_id=custom_id, result=types.SimpleNamespace(type=kind))


def _backend(resp: types.SimpleNamespace, entries: list | None = None) -> AnthropicBatchBackend:
    batches = types.SimpleNamespace(
        retrieve=lambda job_id: resp, results=lambda job_id: list(entries or [])
    )
    return AnthropicBatchBackend(
        types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
    )


@pytest.mark.parametrize(
    ("resp", "expected"),
    [
        (_resp(succeeded=3), ENDED_SUCCEEDED),
        # A partial success is still worth reading; each failed row has its error.
        (_resp(succeeded=1, errored=1, expired=1), ENDED_SUCCEEDED),
        (_resp(expired=3), ENDED_FAILED),
        (_resp(errored=3), ENDED_FAILED),
        (_resp(errored=1, expired=2), ENDED_FAILED),
        (_resp(cancel="2026-10-09T01:00:00Z", canceled=3), ENDED_CANCELED),
        # Canceled after some requests finished: still the caller's cancel.
        (_resp(cancel="2026-10-09T01:00:00Z", succeeded=2, canceled=1), ENDED_CANCELED),
    ],
    ids=[
        "all-ok",
        "partial",
        "all-expired",
        "all-errored",
        "errored+expired",
        "canceled",
        "canceled-late",
    ],
)
def test_an_ended_batch_polls_as_how_it_ended(resp: types.SimpleNamespace, expected: str) -> None:
    assert _backend(resp).poll("msgbatch_x").status == expected


@pytest.mark.parametrize("status", ["in_progress", "canceling"])
def test_in_flight_statuses_are_unchanged(status: str) -> None:
    # `canceling` with a cancel time set is still in flight, not canceled yet.
    resp = _resp(status=status, cancel="2026-10-09T01:00:00Z", processing=3)
    assert _backend(resp).poll("msgbatch_x").status == IN_PROGRESS


def test_every_terminal_status_still_returns_its_rows() -> None:
    entries = [_entry("r0", "expired"), _entry("r1", "errored"), _entry("r2", "canceled")]
    for resp in (_resp(expired=1, errored=1, canceled=1), _resp(cancel="t", canceled=3)):
        backend = _backend(resp, entries)
        assert backend.poll("msgbatch_x").status in TERMINAL_STATUSES - {ENDED_SUCCEEDED}
        rows = backend.results("msgbatch_x")
        assert [r.custom_id for r in rows] == ["r0", "r1", "r2"]
        assert all(r.error for r in rows)


def test_a_client_without_request_counts_keeps_the_old_answer() -> None:
    # A duck-typed client (D-002) with only `n_requests` says nothing about
    # failure, so it is not read as one.
    resp = types.SimpleNamespace(id="b", processing_status="ended", n_requests=2, created_at="")
    assert _backend(resp).poll("b").status == ENDED_SUCCEEDED


def test_a_malformed_count_still_gets_the_field_named_error() -> None:
    with pytest.raises(ValueError, match="n_requests"):
        _backend(_resp(succeeded="3")).poll("msgbatch_x")  # type: ignore[arg-type]
