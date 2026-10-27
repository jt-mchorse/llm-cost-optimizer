"""`poll`/`results` refuse a `job_id` that cannot name a job, on every backend (#366).

README's parity row "`poll` / `results`: unknown `job_id` | `JobNotFound` |
`JobNotFound`" is the #198 contract. Before #366 the in-memory backend called
`''`, `None`, `'.'` and `'..'` `JobNotFound` while the real SDK refused them
client-side with its own `ValueError`; a lone surrogate was a raw
`UnicodeEncodeError`; and `['x']` was a raw `TypeError` in-memory but sent as
`GET /v1/messages/batches/%5B'x'%5D` by the SDK. Both backends now refuse all
of them with a `ValueError` naming `job_id` before any lookup or request.

The fake-client arms run in CI with no `anthropic` import (D-002); the wire arm
drives the real SDK against a local socket server and skips without the
`anthropic` extra. No real API call anywhere.
"""

from __future__ import annotations

import pytest

from cost_optimizer.batch import AnthropicBatchBackend, InMemoryBatchBackend, JobNotFound
from tests.test_batch_backend_parity import _FakeClient, _req
from tests.test_batch_idempotency_key_header import _WireServer

_METHODS = ["poll", "results"]

# (job_id, the message the ValueError must carry). Every row the issue
# measured, plus the lone surrogate the SDK cannot encode and more non-str
# types; `42` used to be sent as `/batches/42` by the SDK.
_REFUSED = [
    ("", "job_id must be a non-empty string"),
    (None, "job_id must be a non-empty string; got NoneType"),
    (["x"], "job_id must be a non-empty string; got list"),
    (42, "job_id must be a non-empty string; got int"),
    (b"msgbatch_1", "job_id must be a non-empty string; got bytes"),
    (".", "job_id must not be a '.' or '..' path segment"),
    ("..", "job_id must not be a '.' or '..' path segment"),
    ("a\udc80b", "job_id must be UTF-8-encodable text"),
    ("\ud800", "job_id must be UTF-8-encodable text"),
]

# Controls: ids that are odd but sendable. The SDK percent-encodes `/`, `?`,
# `#` and space into one path segment, so these reach the server and are a
# 404 -> `JobNotFound` there, as in-memory.
_UNKNOWN = ["msgbatch_nope", "a/b", "../x", "./x", "...", " ", "a?b", "a#b", "café"]


@pytest.fixture(params=["in_memory", "anthropic"])
def backend(request):
    if request.param == "in_memory":
        return InMemoryBatchBackend()
    return AnthropicBatchBackend(_FakeClient())


def _ids(rows):
    return [ascii(r[0]) if isinstance(r, tuple) else ascii(r) for r in rows]


@pytest.mark.parametrize("method", _METHODS)
@pytest.mark.parametrize(("job_id", "msg"), _REFUSED, ids=_ids(_REFUSED))
def test_a_job_id_that_cannot_name_a_job_is_a_value_error(backend, method, job_id, msg) -> None:
    with pytest.raises(ValueError, match=msg) as ei:
        getattr(backend, method)(job_id)
    assert not isinstance(ei.value, UnicodeError)


@pytest.mark.parametrize("method", _METHODS)
@pytest.mark.parametrize(("job_id", "msg"), _REFUSED, ids=_ids(_REFUSED))
def test_refused_job_id_never_reaches_the_client(method, job_id, msg) -> None:
    client = _FakeClient()
    calls: list = []
    batches = client.messages.batches
    real_retrieve = batches.retrieve
    batches.retrieve = lambda jid: (calls.append(jid), real_retrieve(jid))[1]
    with pytest.raises(ValueError, match="job_id"):
        getattr(AnthropicBatchBackend(client), method)(job_id)
    assert calls == []


@pytest.mark.parametrize("method", _METHODS)
@pytest.mark.parametrize("job_id", _UNKNOWN, ids=ascii)
def test_an_unknown_but_sendable_job_id_is_still_job_not_found(backend, method, job_id) -> None:
    """Control: the guard must not swallow the #198 `JobNotFound` row."""
    with pytest.raises(JobNotFound):
        getattr(backend, method)(job_id)


def test_a_known_job_id_still_polls_on_both_backends() -> None:
    """Control: a real id passes the guard."""
    mem = InMemoryBatchBackend()
    meta = mem.submit([_req()], idempotency_key="k")
    assert mem.poll(meta.job_id).job_id == meta.job_id
    anth = AnthropicBatchBackend(_FakeClient())
    meta = anth.submit([_req()], idempotency_key="k")
    assert anth.poll(meta.job_id).job_id == meta.job_id
    assert anth.results(meta.job_id) == []


# --- real SDK, local socket: what actually goes on the wire ----------------


@pytest.fixture
def wire():
    anthropic = pytest.importorskip(
        "anthropic",
        reason="real-SDK wire arm; CI's test job does not install anthropic (D-002), "
        "the fake-client arms above are its CI coverage",
    )
    server = _WireServer()
    client = anthropic.Anthropic(
        api_key="test", base_url=f"http://127.0.0.1:{server.port}", max_retries=0
    )
    yield server, AnthropicBatchBackend(client)
    server.sock.close()


@pytest.mark.parametrize("method", _METHODS)
@pytest.mark.parametrize(("job_id", "msg"), _REFUSED, ids=_ids(_REFUSED))
def test_real_sdk_gets_our_value_error_not_its_own(wire, method, job_id, msg) -> None:
    server, backend = wire
    with pytest.raises(ValueError, match=msg):
        getattr(backend, method)(job_id)
    assert server.heads == []


@pytest.mark.parametrize("job_id", _UNKNOWN, ids=ascii)
def test_sendable_job_id_reaches_the_wire_as_one_path_segment(wire, job_id) -> None:
    """Control: the ids the guard passes are ones the real SDK sends."""
    server, backend = wire
    backend.poll(job_id)
    assert len(server.heads) == 1
    target = server.heads[0].split(b" ")[1]
    assert target.startswith(b"/v1/messages/batches/")
    assert b"/" not in target[len(b"/v1/messages/batches/") :]
