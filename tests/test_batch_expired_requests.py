"""Expired batch requests are counted, so an expired batch can be polled and read (#269).

`_sdk_request_total` summed processing/succeeded/errored/canceled. The SDK's
`MessageBatchRequestCounts` also has `expired` (checked in the published
`anthropic` wheel's `types/messages/message_batch_request_counts.py`), which
counts requests that sat past the 24h window. Measured on `main` with a fake
client whose `retrieve` returns `processing_status="ended"`:

    {succeeded: 2, expired: 3} -> n_requests=2           (should be 5)
    {expired: 5}               -> ValueError: BatchJobMeta.n_requests must be
                                  an int >= 1; got 0      (poll AND results)
"""

from __future__ import annotations

import types

import pytest

from cost_optimizer.batch import AnthropicBatchBackend, _from_sdk_batch


def _counts(**kw: int) -> types.SimpleNamespace:
    base = {"processing": 0, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0}
    return types.SimpleNamespace(**{**base, **kw})


def _resp(counts: types.SimpleNamespace) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id="msgbatch_x",
        processing_status="ended",
        request_counts=counts,
        created_at="2026-10-07T00:00:00Z",
    )


def _entry(custom_id: str, kind: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(custom_id=custom_id, result=types.SimpleNamespace(type=kind))


class _Batches:
    def __init__(self, counts: types.SimpleNamespace, entries: list) -> None:
        self._resp = _resp(counts)
        self._entries = entries

    def retrieve(self, job_id: str) -> types.SimpleNamespace:
        return self._resp

    def results(self, job_id: str) -> list:
        return list(self._entries)


def _backend(counts: types.SimpleNamespace, entries: list) -> AnthropicBatchBackend:
    return AnthropicBatchBackend(
        types.SimpleNamespace(messages=types.SimpleNamespace(batches=_Batches(counts, entries)))
    )


def test_a_partly_expired_batch_counts_every_request() -> None:
    assert (
        _from_sdk_batch(_resp(_counts(succeeded=2, expired=3)), idempotency_key="k").n_requests == 5
    )


def test_a_wholly_expired_batch_polls() -> None:
    meta = _backend(_counts(expired=5), []).poll("msgbatch_x")
    assert meta.n_requests == 5


def test_a_wholly_expired_batch_returns_its_rows_as_expired_errors() -> None:
    entries = [_entry(f"r{i}", "expired") for i in range(5)]
    rows = _backend(_counts(expired=5), entries).results("msgbatch_x")
    assert [r.custom_id for r in rows] == [f"r{i}" for i in range(5)]
    assert all(r.error == "expired" and r.response_text is None for r in rows)


def test_counts_without_an_expired_field_still_sum(  # an older SDK or a fake
) -> None:
    counts = types.SimpleNamespace(processing=0, succeeded=3, errored=1, canceled=0)
    assert _from_sdk_batch(_resp(counts), idempotency_key="k").n_requests == 4


@pytest.mark.parametrize("bad", ["many", None, True, float("nan")])
def test_a_malformed_expired_count_takes_the_clean_error_path(bad: object) -> None:
    with pytest.raises(ValueError, match=r"BatchJobMeta\.n_requests must be an int >= 1"):
        _from_sdk_batch(_resp(_counts(succeeded=1, expired=bad)), idempotency_key="k")  # type: ignore[arg-type]
