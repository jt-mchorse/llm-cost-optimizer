"""Content-block request text must have a UTF-8 encoding too, on every backend (#370).

#365 refused a lone surrogate in `user` / `system` / `model` when the field is a
`str`. The SDK also takes content-block lists (`[{"type": "text", "text": ...}]`),
and `BatchRequest` passes them through, but the #365 check skipped every
non-`str` value. A surrogate one level down therefore still raised a raw
`UnicodeEncodeError`. That hit both backends, because #351's body-size bound
encodes the JSON before either backend sees the batch. The rule now covers
every string the JSON body carries: dict keys and values, list and tuple items,
at any depth.

Surrogates are built from code points so no tool can normalise them away.
"""

from __future__ import annotations

import json

import pytest

from cost_optimizer import batch as batch_mod
from cost_optimizer.batch import AnthropicBatchBackend, BatchRequest, InMemoryBatchBackend
from tests.test_batch_idempotency_key_header import _fake_client, _WireServer

SUR = chr(0xD800)


def _req(**kw) -> BatchRequest:
    fields = {"user": "hi", "model": "claude-haiku-4-5", "system": None} | kw
    return BatchRequest(custom_id="r0", max_tokens=8, **fields)


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


# (field, value, the path the error must name). Paths use the JSON-shaped
# indexing a caller would write to reach the bad string.
_BAD = [
    ("user", [_text_block(SUR)], "requests[0].user[0]['text']"),
    ("system", [_text_block("ok"), _text_block("a" + SUR)], "requests[0].system[1]['text']"),
    ("user", (_text_block(SUR),), "requests[0].user[0]['text']"),
    (
        "user",
        [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": [_text_block("fine"), _text_block("x" + chr(0xDFFF))],
            }
        ],
        "requests[0].user[0]['content'][1]['text']",
    ),
    (
        "user",
        [{"type": "text", "text": "ok", "cache_control": {"type": SUR}}],
        "requests[0].user[0]['cache_control']['type']",
    ),
    ("user", [_text_block(json.loads('"x\\ud800"'))], "requests[0].user[0]['text']"),
    (
        "user",
        [_text_block(b"caf\xe9".decode("utf-8", "surrogateescape"))],
        "requests[0].user[0]['text']",
    ),
]
_IDS = [
    "user-block",
    "system-second-block",
    "tuple",
    "tool-result-nested",
    "nested-dict",
    "json-loads",
    "surrogateescape",
]


@pytest.fixture(params=["in_memory", "anthropic"])
def backend(request):
    if request.param == "in_memory":
        return InMemoryBatchBackend()
    return AnthropicBatchBackend(_fake_client())


@pytest.mark.parametrize(("field", "value", "path"), _BAD, ids=_IDS)
def test_submit_refuses_a_surrogate_inside_content_blocks(backend, field, value, path) -> None:
    with pytest.raises(ValueError, match="must be UTF-8-encodable") as ei:
        backend.submit([_req(**{field: value})], idempotency_key="k")
    assert not isinstance(ei.value, UnicodeError)
    assert str(ei.value).startswith(f"{path} must be UTF-8-encodable")


@pytest.mark.parametrize(("field", "value", "path"), _BAD, ids=_IDS)
def test_refused_blocks_never_reach_the_client(field, value, path) -> None:
    client = _fake_client()
    with pytest.raises(ValueError, match="UTF-8-encodable"):
        AnthropicBatchBackend(client).submit([_req(**{field: value})], idempotency_key="k")
    assert client.messages.batches.calls == []


def test_a_surrogate_in_a_dict_key_is_refused_and_named_as_a_key(backend) -> None:
    value = [{"type": "text", "text": "ok", "x" + SUR: 1}]
    with pytest.raises(ValueError, match=r"^a dict key in requests\[0\]\.user\[0\] ") as ei:
        backend.submit([_req(user=value)], idempotency_key="k")
    assert "at index 1" in str(ei.value)


def test_the_first_bad_string_in_emission_order_is_reported() -> None:
    """A key is emitted before its value, and items in order: report what JSON hits first."""
    value = [{"type": "a" + SUR, "zz" + SUR: "b" + SUR}]
    with pytest.raises(ValueError, match=r"^requests\[0\]\.user\[0\]\['type'\] "):
        batch_mod._validate_submit_args([_req(user=value)], "k")
    value = [{"zz" + SUR: "b" + SUR}]
    with pytest.raises(ValueError, match=r"^a dict key in requests\[0\]\.user\[0\] "):
        batch_mod._validate_submit_args([_req(user=value)], "k")


def test_the_row_index_is_the_bad_rows() -> None:
    reqs = [
        BatchRequest("ok", [_text_block("fine")], "m"),
        BatchRequest("bad", [_text_block(SUR)], "m"),
    ]
    with pytest.raises(ValueError, match=r"^requests\[1\]\.user\[0\]\['text'\] "):
        batch_mod._validate_submit_args(reqs, "k")


def test_deep_nesting_is_walked_without_recursion() -> None:
    """Iterative: nesting past the interpreter's recursion limit still gets the named error."""
    value: object = [_text_block(SUR)]
    for _ in range(5_000):
        value = [value]
    found = batch_mod._first_unencodable_leaf(value, "p")
    assert found is not None
    assert found[0] == "p" + "[0]" * 5_001 + "['text']"


def test_a_shared_container_is_walked_once_without_changing_the_answer() -> None:
    """The same block twice is legal JSON. Walking it once is enough: if its first visit held
    no bad string, neither does its second."""
    block = _text_block("fine")
    bad = _text_block(SUR)
    value = [block, block, bad, bad]
    found = batch_mod._first_unencodable_leaf(value, "p")
    assert found is not None
    assert found[0] == "p[2]['text']"
    assert batch_mod._first_unencodable_leaf([bad, bad], "p")[0] == "p[0]['text']"
    # The second visit to a shared bad block is still found when it is the first bad one.
    assert batch_mod._first_unencodable_leaf([block, [bad], bad], "p")[0] == "p[1][0]['text']"


def test_a_cycle_terminates_and_is_left_to_json() -> None:
    cyc: list = [_text_block("ok")]
    cyc.append(cyc)
    assert batch_mod._first_unencodable_leaf(cyc, "p") is None
    with pytest.raises(ValueError, match="Circular reference"):
        InMemoryBatchBackend().submit([_req(user=cyc)], idempotency_key="k")


# --- controls ----------------------------------------------------------------


_GOOD = [
    [_text_block("café 中文 \U0001f600")],
    [_text_block(chr(0xD7FF) + chr(0xE000) + chr(0x10FFFF))],
    ({"type": "text", "text": "tuple"},),
    [
        {
            "type": "tool_result",
            "tool_use_id": "t",
            "content": [_text_block("ok")],
            "is_error": False,
        }
    ],
    [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}],
    [{"type": "text", "text": "n", "cache_control": {"type": "ephemeral"}, "count": 3, "x": None}],
]


@pytest.mark.parametrize("field", ["user", "system"])
@pytest.mark.parametrize("value", _GOOD, ids=range(len(_GOOD)))
def test_encodable_blocks_are_accepted_and_forwarded_verbatim(field, value) -> None:
    assert InMemoryBatchBackend().submit([_req(**{field: value})], idempotency_key="k")
    client = _fake_client()
    AnthropicBatchBackend(client).submit([_req(**{field: value})], idempotency_key="k")
    sent = client.messages.batches.calls[0]["requests"][0]["params"]
    got = sent["messages"][0]["content"] if field == "user" else sent["system"]
    assert got is value


def test_a_str_field_keeps_its_365_message() -> None:
    """Parity: the top-level `str` case still names the bare field, as #365 shipped it."""
    with pytest.raises(ValueError, match=r"^requests\[0\]\.user must be UTF-8-encodable"):
        batch_mod._validate_submit_args([_req(user="a" + SUR)], "k")


# --- real SDK, local socket --------------------------------------------------


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


@pytest.mark.parametrize(("field", "value", "path"), _BAD, ids=_IDS)
def test_refused_blocks_are_a_value_error_on_the_real_sdk(wire, field, value, path) -> None:
    server, backend = wire
    with pytest.raises(ValueError, match="UTF-8-encodable") as ei:
        backend.submit([_req(**{field: value})], idempotency_key="k")
    assert not isinstance(ei.value, UnicodeError)
    assert server.heads == []


def test_encodable_blocks_reach_the_wire(wire) -> None:
    server, backend = wire
    backend.submit([_req(user=[_text_block("café \U0001f600")])], idempotency_key="k")
    assert len(server.heads) == 1
