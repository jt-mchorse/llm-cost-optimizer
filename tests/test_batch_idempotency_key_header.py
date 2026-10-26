"""`idempotency_key` is an HTTP header value, on every backend (#363).

`AnthropicBatchBackend.submit` sends the key as `Idempotency-Key`. Before #363
the shared validator checked it only for blankness, so the two backends split:
`'a\\nb'` was accepted in-memory and failed in production as an
`APIConnectionError` (h11 `LocalProtocolError`, nothing sent) — a class the SDK
retries, so a bad argument read as a network fault. `42` raised a raw
`AttributeError` from `.strip()`; `b'k'` passed in-memory and raised in the SDK.

The bound is visible ASCII with spaces only between visible characters (see
`_IDEMPOTENCY_KEY_RE`). The fake-client arms run in CI with no `anthropic`
import (D-002); the wire arm drives the real SDK against a local socket server
and skips without the `anthropic` extra. No real API call anywhere.
"""

from __future__ import annotations

import socket
import string
import threading

import pytest

from cost_optimizer import batch as batch_mod
from cost_optimizer.batch import (
    AnthropicBatchBackend,
    BatchRequest,
    InMemoryBatchBackend,
)


class _Ns:
    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


class _RecordingBatches:
    """Records every `create`; a refused key must leave `calls` empty."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, *, requests, extra_headers=None):
        self.calls.append({"requests": requests, "extra_headers": extra_headers})
        counts = _Ns(processing=len(requests), succeeded=0, errored=0, canceled=0)
        return _Ns(
            id=f"msgbatch_{len(self.calls):04d}",
            processing_status="in_progress",
            request_counts=counts,
            created_at="2026-10-26T00:00:00Z",
        )


def _fake_client() -> _Ns:
    return _Ns(messages=_Ns(batches=_RecordingBatches()))


def _req() -> list[BatchRequest]:
    return [BatchRequest(custom_id="r1", user="hi", model="claude-haiku-4-5", max_tokens=8)]


# Every row the issue measured plus the neighbours h11 treats differently:
# the first group fails in the transport, the second is sent verbatim.
_REFUSED = [
    "a\nb",
    "a\rb",
    "a\x00b",
    "a\x0bb",
    "a\x0cb",
    " lead",
    "trail ",
    "\tlead",
    "a\r\nX-Injected: 1",
    "a\tb",
    "a\x01b",
    "a\x1fb",
    "a\x7fb",
    "café",
    "a\x85b",
    "a b",
    "k\U0001f600",
]

_NON_STR = [42, b"k", ["k"], 1.5]

# Controls: ordinary keys, the README's own example shape, inner spaces, and
# every visible ASCII character at once.
_ACCEPTED = [
    "ok-key",
    "2026-05-16-batch-1",
    "a b",
    "a   b",
    "shard 3 of 8",
    "".join(chr(c) for c in range(0x21, 0x7F)),
]


@pytest.fixture(params=["in_memory", "anthropic"])
def backend(request):
    if request.param == "in_memory":
        return InMemoryBatchBackend()
    return AnthropicBatchBackend(_fake_client())


@pytest.mark.parametrize("key", _REFUSED)
def test_submit_refuses_a_key_that_is_not_a_header_value(backend, key: str) -> None:
    with pytest.raises(ValueError, match=r"idempotency_key must be visible ASCII"):
        backend.submit(_req(), idempotency_key=key)


@pytest.mark.parametrize("key", _NON_STR, ids=lambda k: type(k).__name__)
def test_submit_refuses_a_non_str_key(backend, key) -> None:
    with pytest.raises(
        ValueError, match=rf"idempotency_key must be a non-empty string; got {type(key).__name__}"
    ):
        backend.submit(_req(), idempotency_key=key)


@pytest.mark.parametrize("key", _REFUSED + _NON_STR)
def test_refused_key_never_reaches_the_client(key) -> None:
    client = _fake_client()
    with pytest.raises(ValueError, match="idempotency_key"):
        AnthropicBatchBackend(client).submit(_req(), idempotency_key=key)
    assert client.messages.batches.calls == []


@pytest.mark.parametrize("key", _ACCEPTED)
def test_submit_accepts_a_header_safe_key_and_forwards_it_verbatim(key: str) -> None:
    """Control: the guard must not refuse ordinary keys or keys with inner spaces."""
    assert InMemoryBatchBackend().submit(_req(), idempotency_key=key).idempotency_key == key
    client = _fake_client()
    AnthropicBatchBackend(client).submit(_req(), idempotency_key=key)
    assert client.messages.batches.calls[0]["extra_headers"] == {"Idempotency-Key": key}


def test_every_accepted_key_is_one_the_transport_sends() -> None:
    """The bound must sit inside h11's own header grammar, searched not hand-picked.

    Every Latin-1 character in the first, middle and last position of a key:
    whatever the validator accepts, h11 must accept. Without this a bound could
    drift wider than the transport and reopen the split.
    """
    h11 = pytest.importorskip("h11")
    for c in map(chr, range(0x100)):
        for key in (c + "k", "k" + c + "k", "k" + c):
            if batch_mod._IDEMPOTENCY_KEY_RE.fullmatch(key) is None:
                continue
            h11.Request(
                method="POST",
                target="/",
                headers=[("Host", "x"), ("Idempotency-Key", key.encode("ascii"))],
            )


def test_bound_is_exactly_visible_ascii_plus_inner_spaces() -> None:
    accepted = {
        c for c in map(chr, range(0x100)) if batch_mod._IDEMPOTENCY_KEY_RE.fullmatch("k" + c + "k")
    }
    assert accepted == set(string.printable) - set("\t\n\r\x0b\x0c")


# --- real SDK, local socket: what actually goes on the wire ----------------


class _WireServer:
    """Accepts connections, records the raw request head, answers one batch."""

    _BODY = (
        b'{"id":"msgbatch_1","type":"message_batch","processing_status":"in_progress",'
        b'"request_counts":{"processing":1,"succeeded":0,"errored":0,"canceled":0,"expired":0},'
        b'"created_at":"2026-10-26T00:00:00Z","expires_at":"2026-10-27T00:00:00Z",'
        b'"ended_at":null,"cancel_initiated_at":null,"archived_at":null,"results_url":null}'
    )

    def __init__(self) -> None:
        self.heads: list[bytes] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                self.heads.append(data)
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: %d\r\nConnection: close\r\n\r\n"
                    % len(self._BODY)
                    + self._BODY
                )

    def idempotency_headers(self) -> list[bytes]:
        return [
            line
            for head in self.heads
            for line in head.split(b"\r\n")
            if line.lower().startswith(b"idempotency-key:")
        ]


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


@pytest.mark.parametrize("key", _ACCEPTED)
def test_accepted_key_arrives_on_the_wire_byte_identical(wire, key: str) -> None:
    server, backend = wire
    backend.submit(_req(), idempotency_key=key)
    assert server.idempotency_headers() == [b"Idempotency-Key: " + key.encode("ascii")]


@pytest.mark.parametrize("key", _REFUSED + _NON_STR)
def test_refused_key_is_a_value_error_not_a_connection_error(wire, key) -> None:
    server, backend = wire
    with pytest.raises(ValueError, match="idempotency_key"):
        backend.submit(_req(), idempotency_key=key)
    assert server.heads == []
