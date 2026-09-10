"""What shape is this decoded value? — the JSON-shape vocabulary, in one place.

Two modules in this package read nested values off a duck-typed SDK response and
have to decide, of each one, *what kind of thing it is* before reading it. They
had two vocabularies.

``batch.py`` argued the rule out in prose when ``#213`` widened
``_succeeded_row_shape_error``:

    Partitioned on that property rather than hand-listed type by type, because
    the list that grows one entry at a time is exactly what left this half
    open. […] ``Mapping``/``Sequence`` rather than ``dict``/``list`` so the JSON
    alphabet's near relatives — a tuple of blocks' worth of decoded values, a
    ``MappingProxyType`` — land on the same side.

``cache_wrapper.py`` still said ``dict`` and ``list``, at four sites, and the
consequence was the exact harm ``_get_usage``'s own docstring was written
about — a well-formed value read as absent, landing as a silent ``$0.00`` on the
savings dashboard (``#215``). Measured, 20 000 cached tokens in every row::

    dict response  + dict usage      (control)   cache_read_input_tokens -> 20000
    dict response  + Mapping usage               cache_read_input_tokens -> 0
    dict response  + UserDict usage              cache_read_input_tokens -> 0
    Mapping response (MappingProxyType)          cache_read_input_tokens -> 0
    UserDict response                            cache_read_input_tokens -> 0
    object response + dict usage      (control)  cache_read_input_tokens -> 20000
    object response + Mapping usage              cache_read_input_tokens -> 0
    object response + object usage    (control)  cache_read_input_tokens -> 20000

``collections.UserDict`` is the sharp member: not a ``dict`` subclass, but a
``Mapping``, and the ordinary base for a gateway/proxy client's response
wrapper.

This module holds only what both callers genuinely ask. The rest of each
module's shape logic stays where it is — a false parity between two different
questions is worse than two honest rules.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: What a JSON decoder produces. A value that is one of these is a *decoded
#: payload* rather than an object the SDK models — the shape a
#: ``model_dump()``-style payload, a gateway/proxy client, or a ``json.loads``
#: of a raw response hands over.
#:
#: ``Mapping``/``Sequence`` rather than ``dict``/``list``: a ``MappingProxyType``
#: and a ``UserDict`` are mappings and not dicts, a tuple is a sequence and not a
#: list, and every one of them is something a decoder or a wrapper hands you. A
#: modelled block object is none of them.
DECODED_JSON_TYPES: tuple[type, ...] = (
    bool,
    int,
    float,
    str,
    bytes,
    bytearray,
    Mapping,
    Sequence,
)

#: The two decoded types that are *also* iterable into something that looks like
#: content but is not: a ``str`` iterates into characters and a ``Mapping`` into
#: its keys. Named separately because "is this a sequence of blocks" has to
#: exclude them while "is this a decoded value" has to include them.
_NOT_A_BLOCK_SEQUENCE: tuple[type, ...] = (str, bytes, bytearray, Mapping)


def is_decoded_json_value(value: Any) -> bool:
    """True when *value* is a decoded JSON value rather than a modelled object.

    ``None`` is a member of the JSON alphabet and is checked separately, because
    ``isinstance(None, ...)`` is never true for a type in the tuple above.

    What this deliberately does **not** flag is an *object* that carries no
    payload — a ``tool_use`` content block is exactly that, and it is correct.
    The partition is on the shape of the value, never on whether reading it
    produced anything; D-017's load-bearing choice.
    """
    return value is None or isinstance(value, DECODED_JSON_TYPES)


def is_block_sequence(value: Any) -> bool:
    """True when *value* is a sequence of content blocks, as opposed to a
    decoded scalar, a string, or a mapping.

    The positive form of the test ``batch.py`` spells negatively across two
    branches (each of which keeps its own message, because they diagnose
    different things).

    ``str``/``bytes`` are excluded rather than merely unhandled. Both are
    ``Sequence``s, both iterate into something, and both have their own meaning
    at every call site — a ``str`` ``system`` prompt is promoted to a single
    text block, a ``str`` ``content`` is a shape error. All three call sites
    happen to test ``str`` on an earlier line, so today the exclusion is
    redundant with statement order; it is here because a guarantee that depends
    on statement order is not a guarantee, and the fourth call site is the one
    that will not know to check. Measured: dropping the exclusion turns only
    this function's own rows red, not the promote branch's.
    """
    return isinstance(value, Sequence) and not isinstance(value, _NOT_A_BLOCK_SEQUENCE)
