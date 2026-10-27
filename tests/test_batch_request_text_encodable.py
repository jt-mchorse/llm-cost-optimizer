"""Request text must have a UTF-8 encoding, on every backend (#365).

`AnthropicBatchBackend.submit` sends the batch as a UTF-8 JSON body. A lone
surrogate in `user`, `system` or `model` has no UTF-8 encoding, so before #365
the real SDK raised a raw `UnicodeEncodeError` with nothing on the wire while
`InMemoryBatchBackend` accepted the same request — and the parity suite pinned
that acceptance as correct because it only ever ran a fake client. Lone
surrogates are ordinary input: `json.loads('"\\ud800"')`, or bytes decoded with
`surrogateescape`.

The fake-client arms run in CI with no `anthropic` import (D-002); the wire arm
drives the real SDK against a local socket server and skips without the
`anthropic` extra. No real API call anywhere.
"""

from __future__ import annotations

import json

import pytest

from cost_optimizer import batch as batch_mod
from cost_optimizer.batch import AnthropicBatchBackend, BatchRequest, InMemoryBatchBackend
from tests.test_batch_idempotency_key_header import _fake_client, _WireServer

_FIELDS = ["user", "system", "model"]

# The issue's three rows plus the neighbours: both surrogate edges, a
# *paired* high+low surrogate (two code units in a Python str, still not
# encodable; built from code points so no tool can rewrite it into the emoji),
# and the realistic sources.
_SURROGATE_TEXT = [
    "a\ud800b",
    "\udc80",
    "\ud83d",
    "\udfff",
    chr(0xD83D) + chr(0xDE00),
    json.loads('"x\\ud800"'),
    b"caf\xe9".decode("utf-8", "surrogateescape"),
]

# Controls: non-ASCII that *is* encodable, including the code points either
# side of the surrogate block and the last code point.
_ENCODABLE_TEXT = ["hello", "café", "中文", "\U0001f600", "\ud7ff", "\ue000", "\U0010ffff"]


def _req(i: int = 0, **kw) -> BatchRequest:
    fields = {"user": "hi", "model": "claude-haiku-4-5", "system": None} | kw
    return BatchRequest(custom_id=f"r{i}", max_tokens=8, **fields)


@pytest.fixture(params=["in_memory", "anthropic"])
def backend(request):
    if request.param == "in_memory":
        return InMemoryBatchBackend()
    return AnthropicBatchBackend(_fake_client())


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("text", _SURROGATE_TEXT, ids=ascii)
def test_submit_refuses_a_lone_surrogate(backend, field: str, text: str) -> None:
    with pytest.raises(ValueError, match=rf"requests\[0\]\.{field} must be UTF-8-encodable"):
        backend.submit([_req(**{field: text})], idempotency_key="k")


@pytest.mark.parametrize("field", _FIELDS)
def test_error_names_the_row_and_the_offset_not_the_whole_text(backend, field: str) -> None:
    long = "x" * 10_000 + "\ud800"
    reqs = [_req(0), _req(1, **{field: long})]
    with pytest.raises(ValueError, match="UTF-8-encodable") as ei:
        backend.submit(reqs, idempotency_key="k")
    msg = str(ei.value)
    assert f"requests[1].{field}" in msg
    assert "at index 10000" in msg
    assert len(msg) < 300


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("text", _SURROGATE_TEXT, ids=ascii)
def test_refused_text_never_reaches_the_client(field: str, text: str) -> None:
    client = _fake_client()
    with pytest.raises(ValueError, match=rf"requests\[0\]\.{field}"):
        AnthropicBatchBackend(client).submit([_req(**{field: text})], idempotency_key="k")
    assert client.messages.batches.calls == []


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("text", _ENCODABLE_TEXT, ids=ascii)
def test_encodable_text_is_accepted_and_forwarded_verbatim(field: str, text: str) -> None:
    """Control: the rule is UTF-8-encodability, not ASCII."""
    assert InMemoryBatchBackend().submit([_req(**{field: text})], idempotency_key="k")
    client = _fake_client()
    AnthropicBatchBackend(client).submit([_req(**{field: text})], idempotency_key="k")
    sent = client.messages.batches.calls[0]["requests"][0]["params"]
    got = {"user": sent["messages"][0]["content"], "system": sent.get("system"), **sent}
    assert got[field] == text


def test_refused_set_is_exactly_the_surrogate_block() -> None:
    """Searched, not hand-picked: every code point around the block, refused iff a surrogate."""
    refused = set()
    for cp in range(0xD700, 0xE100):
        try:
            batch_mod._validate_submit_args([_req(user=chr(cp))], "k")
        except ValueError:
            refused.add(cp)
    assert refused == set(range(0xD800, 0xE000))


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


@pytest.mark.parametrize("field", _FIELDS)
@pytest.mark.parametrize("text", _SURROGATE_TEXT, ids=ascii)
def test_refused_text_is_a_value_error_not_a_unicode_error(wire, field: str, text: str) -> None:
    server, backend = wire
    with pytest.raises(ValueError, match=rf"requests\[0\]\.{field}") as ei:
        backend.submit([_req(**{field: text})], idempotency_key="k")
    assert not isinstance(ei.value, UnicodeError)
    assert server.heads == []


@pytest.mark.parametrize("field", _FIELDS)
def test_encodable_non_ascii_text_reaches_the_wire(wire, field: str) -> None:
    """Control: the real SDK sends what the validator accepts."""
    server, backend = wire
    backend.submit([_req(**{field: "café 中文 \U0001f600"})], idempotency_key="k")
    assert len(server.heads) == 1
