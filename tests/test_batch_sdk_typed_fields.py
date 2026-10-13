"""`AnthropicBatchBackend` reads the SDK's typed fields, not only fakes' strings (#296).

Every fake handed `_from_sdk_batch` a string `created_at` and
`_from_sdk_result_row` a string `error`. The installed SDK (anthropic 1.13.0)
hands it a `datetime` and an `ErrorResponse`, and a bare `str()` published:

    created_at_iso='2026-01-01 00:00:00.123000+00:00'   (in-memory: '...T...Z')
    error="ErrorResponse(error=InvalidRequestError(message='bad', type=...), ...)"

Measured by driving the real SDK over an `httpx2.MockTransport`. These fakes are
typed the way the SDK is.
"""

from __future__ import annotations

import datetime
import re
import types

import pytest

from cost_optimizer.batch import (
    AnthropicBatchBackend,
    BatchRequest,
    InMemoryBatchBackend,
    _from_sdk_result_row,
)

_ISO = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def _poll(created_at: object) -> str:
    resp = types.SimpleNamespace(
        id="msgbatch_x",
        processing_status="in_progress",
        cancel_initiated_at=None,
        request_counts=types.SimpleNamespace(
            processing=1, succeeded=0, errored=0, canceled=0, expired=0
        ),
        created_at=created_at,
    )
    batches = types.SimpleNamespace(retrieve=lambda job_id: resp, results=lambda job_id: [])
    backend = AnthropicBatchBackend(
        types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
    )
    return backend.poll("msgbatch_x").created_at_iso


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        (
            datetime.datetime(2026, 1, 1, 0, 0, 0, 123000, tzinfo=datetime.UTC),
            "2026-01-01T00:00:00Z",
        ),
        # Another offset is converted, not relabelled.
        (
            datetime.datetime(
                2026, 1, 1, 2, 30, tzinfo=datetime.timezone(datetime.timedelta(hours=2))
            ),
            "2026-01-01T00:30:00Z",
        ),
        # Naive: read as the UTC the API sends.
        (datetime.datetime(2026, 1, 1, 5, 6, 7), "2026-01-01T05:06:07Z"),
        # A duck-typed client's string passes through as before.
        ("2026-08-28T00:00:00Z", "2026-08-28T00:00:00Z"),
    ],
    ids=["utc-with-micros", "offset", "naive", "string"],
)
def test_created_at_is_published_as_the_in_memory_backend_does(
    created_at: object, expected: str
) -> None:
    assert _poll(created_at) == expected


def test_both_backends_publish_one_format() -> None:
    sdk = _poll(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))
    mem = (
        InMemoryBatchBackend()
        .submit(
            [BatchRequest(custom_id="a", model="claude-opus-4-8", user="hi", max_tokens=5)],
            idempotency_key="k",
        )
        .created_at_iso
    )
    assert _ISO.fullmatch(sdk), sdk
    assert _ISO.fullmatch(mem), mem


def _errored(error: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        custom_id="b", result=types.SimpleNamespace(type="errored", error=error)
    )


def test_an_sdk_error_response_reads_as_type_and_message() -> None:
    # The SDK's `ErrorResponse` shape: `.error` carries `type` and `message`.
    error = types.SimpleNamespace(
        type="error",
        request_id=None,
        error=types.SimpleNamespace(type="invalid_request_error", message="bad"),
    )
    assert _from_sdk_result_row(_errored(error)).error == "invalid_request_error: bad"


@pytest.mark.parametrize(
    ("error", "expected"),
    [("rate_limit", "rate_limit"), (None, "errored")],
    ids=["string", "absent"],
)
def test_other_error_shapes_keep_their_text(error: object, expected: str) -> None:
    assert _from_sdk_result_row(_errored(error)).error == expected
