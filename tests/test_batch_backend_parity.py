"""`BatchBackend`'s two implementations, cell by cell (#198).

README tells operators to develop against `InMemoryBatchBackend` because it is
the "same protocol" as the production one. Run as a differential grid — same
input, both backends, verdicts side by side — that claim had five diverging
cells. Three were local oversights and are closed here; two hinge on
`AnthropicBatchBackend` holding no state and are #199, pinned below as current
behaviour so that issue has to edit this file on purpose.

The grid includes the cells that legitimately *agree*. A parity suite made only
of the broken cells passes vacuously the moment someone deletes a guard from
both sides.

Hermetic: the SDK is duck-typed per D-002, so the fake below is just an object
with a `.messages.batches` surface. No `anthropic` import anywhere.
"""

from __future__ import annotations

import pytest

from cost_optimizer.batch import (
    AnthropicBatchBackend,
    BatchRequest,
    IdempotencyConflict,
    InMemoryBatchBackend,
    JobNotFound,
    _is_not_found_error,
)


class NotFoundError(Exception):
    """Stand-in for `anthropic.NotFoundError`: carries 404 *and* that name.

    Both handles are exercised separately in the classifier tests below; the
    real SDK exposes both, so the fake does too.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status_code = 404


class _ServerError(Exception):
    """A non-404 SDK failure. Must never be claimed as `JobNotFound`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.status_code = 500


class _Resp:
    def __init__(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


class _FakeBatches:
    def __init__(self) -> None:
        self.jobs: dict[str, _Resp] = {}
        self.n = 0
        self.created_payloads: list = []

    def create(self, *, requests, extra_headers=None):
        self.n += 1
        jid = f"msgbatch_{self.n:04d}"
        self.created_payloads.append(requests)
        counts = _Resp(processing=0, succeeded=len(requests), errored=0, canceled=0)
        self.jobs[jid] = _Resp(
            id=jid,
            processing_status="ended",
            request_counts=counts,
            created_at="2026-08-28T00:00:00Z",
        )
        return self.jobs[jid]

    def retrieve(self, job_id):
        if job_id not in self.jobs:
            raise NotFoundError(f"batch {job_id} not found")
        return self.jobs[job_id]

    def results(self, job_id):
        return []


class _FakeClient:
    def __init__(self) -> None:
        self.messages = _Resp(batches=_FakeBatches())


def _req(custom_id: str = "row-0", user: str = "hello") -> BatchRequest:
    return BatchRequest(custom_id=custom_id, user=user, model="claude-opus-4-7", max_tokens=16)


@pytest.fixture(params=["in_memory", "anthropic"])
def backend(request):
    """Every test taking this fixture runs against *both* implementations."""
    if request.param == "in_memory":
        return InMemoryBatchBackend()
    return AnthropicBatchBackend(_FakeClient())


# --- cells that must agree ------------------------------------------------


def test_submit_rejects_empty_requests(backend) -> None:
    with pytest.raises(ValueError, match="at least one request"):
        backend.submit([], idempotency_key="k")


@pytest.mark.parametrize("key", ["", "   ", "\t\n"])
def test_submit_rejects_blank_idempotency_key(backend, key: str) -> None:
    with pytest.raises(ValueError, match="idempotency_key must be a non-empty string"):
        backend.submit([_req()], idempotency_key=key)


def test_submit_rejects_duplicate_custom_ids(backend) -> None:
    """The cell that was enforced in CI and absent in production.

    Results correlate to the caller by `custom_id`, so a repeated one produces
    rows the caller cannot attribute — and #97's proposed fix rests on these
    being "already enforced at `submit`", which was true of one backend.
    """
    with pytest.raises(ValueError, match=r"duplicate custom_ids within one batch: \['dup'\]"):
        backend.submit([_req("dup"), _req("dup", "different text")], idempotency_key="k")


def test_duplicate_custom_ids_rejected_before_any_network_call() -> None:
    """The production guard must fire *before* the batch is sent, not after.

    A server-side rejection would also be a failure, but only after paying a
    round trip — and it would arrive as an SDK error rather than the
    `ValueError` the in-memory backend documents.
    """
    client = _FakeClient()
    with pytest.raises(ValueError, match="duplicate custom_ids"):
        AnthropicBatchBackend(client).submit([_req("d"), _req("d")], idempotency_key="k")
    assert client.messages.batches.created_payloads == []


def test_poll_unknown_job_raises_job_not_found(backend) -> None:
    """`JobNotFound` is in `__all__`; both backends must be able to raise it."""
    with pytest.raises(JobNotFound, match="unknown job_id"):
        backend.poll("no-such-job")


def test_results_unknown_job_raises_job_not_found(backend) -> None:
    """`results` polls first, so it inherits the translation."""
    with pytest.raises(JobNotFound, match="unknown job_id"):
        backend.results("no-such-job")


def test_submit_is_idempotent_for_an_identical_payload(backend) -> None:
    """An agreeing cell, kept so the grid can't pass by everything raising."""
    first = backend.submit([_req()], idempotency_key="replay-1")
    second = backend.submit([_req()], idempotency_key="replay-1")
    assert first.n_requests == second.n_requests == 1


# --- the classifier, and the neighbours it must not claim -----------------


def test_not_found_classifier_matches_status_code_and_class_name() -> None:
    assert _is_not_found_error(NotFoundError("gone"))

    class NamedOnly(Exception):
        """No `status_code` — an SDK that stopped exposing one."""

    NamedOnly.__name__ = "NotFoundError"
    assert _is_not_found_error(NamedOnly("gone"))


@pytest.mark.parametrize(
    "exc",
    [
        _ServerError("boom"),
        ValueError("a plain error from our own code"),
        TimeoutError("connection"),
    ],
    ids=["sdk-500", "our-valueerror", "timeout"],
)
def test_not_found_classifier_rejects_its_neighbours(exc: Exception) -> None:
    """Claiming a non-404 would assert the job is absent when we don't know."""
    assert not _is_not_found_error(exc)


def test_a_bool_status_code_is_rejected_without_needing_a_bool_guard() -> None:
    """Documents why this classifier carries no `bool` guard.

    `_is_malformed_count_part` in the same module needs one, because there a
    `bool` is *summed* and `True` fabricated a request count of 1 (#166). Here
    the status is only compared against `404`, and no `bool` equals `404`, so
    an explicit guard is unfalsifiable — one was written for symmetry and
    deleted when reverting it turned nothing red. This asserts the outcome, so
    it stays honest if the comparison ever widens into arithmetic.
    """

    class Weird(Exception):
        status_code = True

    assert not _is_not_found_error(Weird())


def test_non_404_sdk_error_propagates_unchanged() -> None:
    """No blanket `except`: an SDK 500 must reach the caller as itself."""
    client = _FakeClient()
    client.messages.batches.retrieve = _raise(_ServerError("upstream down"))
    with pytest.raises(_ServerError, match="upstream down"):
        AnthropicBatchBackend(client).poll("any")


def _raise(exc: BaseException):
    def _f(*_a, **_kw):
        raise exc

    return _f


# --- deferred to #199, pinned as current behaviour ------------------------


def test_idempotency_conflict_is_in_memory_only_for_now() -> None:
    """Pinned, not endorsed — #199.

    Detecting "same key, different payload" needs the payload hash of a prior
    submission. `InMemoryBatchBackend` keeps one; `AnthropicBatchBackend` keeps
    no state at all, so it cannot. Recording the asymmetry here means #199 has
    to change this test deliberately rather than let the contract drift.
    """
    mem = InMemoryBatchBackend()
    mem.submit([_req("a")], idempotency_key="k")
    with pytest.raises(IdempotencyConflict):
        mem.submit([_req("b")], idempotency_key="k")

    ant = AnthropicBatchBackend(_FakeClient())
    ant.submit([_req("a")], idempotency_key="k")
    ant.submit([_req("b")], idempotency_key="k")  # no conflict detected


def test_poll_loses_the_idempotency_key_on_the_stateless_backend() -> None:
    """Pinned, not endorsed — #199.

    `poll` reads `_idempotency_key`, which nothing in this repo or the SDK ever
    sets, so the fallback is unconditional and the public field comes back
    empty. `BatchJobMeta.__post_init__` validates `n_requests` and none of its
    other four fields, so nothing catches it at the construction boundary
    either.
    """
    mem = InMemoryBatchBackend()
    m = mem.submit([_req()], idempotency_key="KEY-1")
    assert mem.poll(m.job_id).idempotency_key == "KEY-1"

    ant = AnthropicBatchBackend(_FakeClient())
    a = ant.submit([_req()], idempotency_key="KEY-1")
    assert a.idempotency_key == "KEY-1"
    assert ant.poll(a.job_id).idempotency_key == ""
