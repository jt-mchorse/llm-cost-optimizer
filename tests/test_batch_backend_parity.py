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

import json

import pytest

from cost_optimizer import batch as batch_mod
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


# The Message Batches API's `custom_id` pattern is `^[a-zA-Z0-9_-]{1,64}$`; an
# id outside it fails the whole batch with a 400. Before #329 both backends
# accepted these: in-memory ran the batch, production sent it.
_API_REFUSED_CUSTOM_IDS = [
    "",
    "row 1",
    "doc/42",
    "a.b",
    "user@example.com",
    "\u00e9",
    "x" * 65,
    "row-1\n",  # `re.match` on the documented `...$` regex would accept this
    7,
]


@pytest.mark.parametrize(
    "cid",
    _API_REFUSED_CUSTOM_IDS,
    ids=["empty", "space", "slash", "dot", "at", "non-ascii", "65-chars", "trailing-nl", "int"],
)
def test_submit_rejects_a_custom_id_the_batches_api_refuses(backend, cid) -> None:
    with pytest.raises(ValueError, match=r"requests\[1\]\.custom_id must be 1-64 characters"):
        backend.submit([_req("ok-0"), _req(cid)], idempotency_key="k")


@pytest.mark.parametrize("cid", _API_REFUSED_CUSTOM_IDS[:1] + _API_REFUSED_CUSTOM_IDS[-3:])
def test_refused_custom_id_rejected_before_any_network_call(cid) -> None:
    client = _FakeClient()
    with pytest.raises(ValueError, match="custom_id must be 1-64 characters"):
        AnthropicBatchBackend(client).submit([_req(cid)], idempotency_key="k")
    assert client.messages.batches.created_payloads == []


@pytest.mark.parametrize(
    "cid", ["x" * 64, "a", "row_1-B", "0042"], ids=["64-chars", "1-char", "under-dash", "digits"]
)
def test_submit_accepts_custom_ids_at_the_api_pattern_edges(backend, cid: str) -> None:
    """Control: the guard must not narrow the pattern the API allows."""
    assert backend.submit([_req(cid)], idempotency_key="k").n_requests == 1


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


# The Message Batches API caps a batch at 100,000 requests or 256 MB, whichever
# comes first; either one fails the whole batch. Before #351 neither backend
# checked: in-memory ran 100,001 requests, production sent them.


def _n_reqs(n: int) -> list[BatchRequest]:
    return [_req(f"r{i}", "hi") for i in range(n)]


def test_batch_limits_are_the_documented_values() -> None:
    """Pinned so a typo in either constant is a red test, not a silent drift."""
    assert batch_mod._MAX_BATCH_REQUESTS == 100_000
    assert batch_mod._MAX_BATCH_BYTES == 256 * 1024 * 1024


def test_submit_accepts_exactly_the_request_limit(backend) -> None:
    """Control: the count guard must not refuse the API's own maximum."""
    assert backend.submit(_n_reqs(100_000), idempotency_key="k").n_requests == 100_000


def test_submit_refuses_one_request_over_the_limit(backend) -> None:
    with pytest.raises(
        ValueError, match=r"requests must hold at most 100,000 entries.*got 100,001"
    ):
        backend.submit(_n_reqs(100_001), idempotency_key="k")


def test_over_limit_request_count_refused_before_any_network_call() -> None:
    client = _FakeClient()
    with pytest.raises(ValueError, match="at most 100,000 entries"):
        AnthropicBatchBackend(client).submit(_n_reqs(100_001), idempotency_key="k")
    assert client.messages.batches.created_payloads == []


# The size limit is exercised with the constant lowered: a real 256 MiB body
# would need ~1 GB of copies in a CI process. The bound itself is checked
# against the payload the backend actually builds. `raising=False` so that,
# against code with no size guard, these fail on behaviour (accepts an
# over-size body) rather than on a missing attribute; the constant's existence
# is pinned above.


def _body_bytes(reqs: list[BatchRequest]) -> int:
    """The compact UTF-8 body for `reqs`, built independently of the guard."""
    payload = {"requests": [batch_mod._to_sdk_request(r) for r in reqs]}
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())


_SIZED = [_req("a", "x" * 500), _req("b", "é中" * 100), _req("c", "y")]


def test_size_bound_equals_the_compact_body_and_never_exceeds_the_default_one() -> None:
    """The guard's count is the smallest encoding; the SDK's can only be larger."""
    bound = batch_mod._request_body_lower_bound(_SIZED)
    assert bound == _body_bytes(_SIZED)
    default = {"requests": [batch_mod._to_sdk_request(r) for r in _SIZED]}
    assert bound <= len(json.dumps(default).encode())


def test_submit_accepts_a_body_exactly_at_the_size_limit(backend, monkeypatch) -> None:
    """Control: at the limit is allowed; the guard refuses only *over* it."""
    monkeypatch.setattr(batch_mod, "_MAX_BATCH_BYTES", _body_bytes(_SIZED), raising=False)
    assert backend.submit(_SIZED, idempotency_key="k").n_requests == 3


def test_submit_refuses_a_body_one_byte_over_the_size_limit(backend, monkeypatch) -> None:
    monkeypatch.setattr(batch_mod, "_MAX_BATCH_BYTES", _body_bytes(_SIZED) - 1, raising=False)
    with pytest.raises(ValueError, match=r"requests must serialize to at most .* bytes"):
        backend.submit(_SIZED, idempotency_key="k")


def test_over_size_body_refused_before_any_network_call(monkeypatch) -> None:
    monkeypatch.setattr(batch_mod, "_MAX_BATCH_BYTES", 1_000, raising=False)
    client = _FakeClient()
    with pytest.raises(ValueError, match="413"):
        AnthropicBatchBackend(client).submit([_req("big", "z" * 2_000)], idempotency_key="k")
    assert client.messages.batches.created_payloads == []


def test_a_lone_surrogate_is_refused_before_the_size_bound(backend) -> None:
    """Both backends refuse it with a field-named `ValueError` (#365).

    This used to assert both *accepted* it, which held only because the
    Anthropic arm is a fake client: the real SDK raised `UnicodeEncodeError`.
    See tests/test_batch_request_text_encodable.py for the full grid.
    """
    with pytest.raises(ValueError, match=r"requests\[0\]\.user must be UTF-8-encodable"):
        backend.submit([_req("s", "a\ud800b")], idempotency_key="k")
