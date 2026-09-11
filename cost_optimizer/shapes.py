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
from typing import Any, TypeGuard

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


def is_item_sequence(value: Any) -> TypeGuard[Sequence[Any]]:
    """True when *value* is a sequence you can iterate for its elements.

    The general form. ``Sequence`` and not one of the decoded types that
    iterate into something misleading — a ``str``/``bytes`` into characters or
    bytes, a ``Mapping`` into its keys.

    Two callers ask this question about two different kinds of element, so the
    predicate is named for the shape and not for the payload:
    ``is_block_sequence`` below is this same test asked about content blocks,
    and ``router.py`` asks it about logprob nodes and about bare floats
    (``#217``). The router had a third copy spelled ``isinstance(x, list)`` at
    five sites, and the copy is what made a ``tuple`` of content blocks — what
    a frozen or proxying SDK wrapper returns — abstain the escalation signal to
    ``trip=False``, which is indistinguishable from a confident response.

    Which member of the exclusion is load-bearing was measured, not assumed
    (``#217``). Dropping it entirely turns exactly **one** row red, and it is
    the ``bytes`` one. ``#215`` had already noted that all three of its call
    sites test ``str`` on an earlier line; the router's guards are downstream
    rather than upstream, and they cover ``str`` and ``Mapping`` by accident:
    iterating either yields *strings* (characters, or a mapping's keys), and
    ``float("a")`` raises, so the ``#140`` non-numeric abstain fires anyway.
    Iterating ``bytes`` yields **ints** — ``float(97)`` is ``97.0`` and
    ``math.isfinite`` is happy — so a ``bytes`` ``first_token_logprobs`` is
    measured as a distribution of byte values with nothing to object to. That
    is the only row the exclusion saves, and it is the reason it stays rather
    than being trimmed to what looks redundant.

    Returns a ``TypeGuard`` rather than a bare ``bool`` because the
    ``isinstance(x, list)`` calls it replaces were *narrowing* calls: mypy
    knew ``x`` was not ``None`` inside the branch, and a plain predicate does
    not carry that. Three ``union-attr`` errors in ``router.py`` are what said
    so, and they are the reason this annotation is not cosmetic.
    """
    return isinstance(value, Sequence) and not isinstance(value, _NOT_A_BLOCK_SEQUENCE)


def is_block_sequence(value: Any) -> TypeGuard[Sequence[Any]]:
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

    Delegates to ``is_item_sequence`` rather than restating the test: this name
    carries the domain meaning the ``batch.py`` and ``cache_wrapper.py`` call
    sites have — their error messages say "blocks" — and that is worth a name,
    but not worth a second implementation. A second correct copy is exactly
    what left the first half of this open (``#215``).
    """
    return is_item_sequence(value)
