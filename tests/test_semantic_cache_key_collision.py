"""Cache keys must not be forgeable from field content (#182).

`_make_key` hand-built a delimiter twice, and both could be produced from
inside a field. `f"{model} {prompt}"` slid on a space; `f"[model={model}]
{prompt}"` slid on `] `. Storage keys records by `record.key`, so each time
the second `put` silently overwrote the first.

The second attempt's comment asserted the `[model=...]` delimiter "can't be
produced by any other split", which is what stopped anyone re-checking. These
tests exist so the property is measured rather than asserted: they build
adversarial pairs out of the delimiter's *own* characters, so they would have
caught either historical scheme without anyone having to guess the trick.
"""

from __future__ import annotations

import pytest

from cost_optimizer.semantic_cache import SemanticCache


class _KeyOnly:
    """`_make_key` needs no cache state; bind it without constructing one."""

    _make_key = SemanticCache._make_key
    _scoped_prompt = SemanticCache._scoped_prompt


def key(model: str, prompt: str) -> str:
    return _KeyOnly()._make_key(prompt, model)


# ---------------------------------------------------------------------------
# The confirmed collisions
# ---------------------------------------------------------------------------


def test_bracket_delimiter_cannot_be_forged_from_a_model_id():
    """The `] ` boundary slid exactly as the space did before it."""
    assert key("claude-opus-4] extra", "PROMPT") != key("claude-opus-4", "extra] PROMPT")


def test_minimal_bracket_collision():
    assert key("m] a", "b") != key("m", "a] b")


def test_the_original_space_collision_also_stays_closed():
    """The scheme before the bracket one; pinned so a revert can't be silent."""
    assert key("b c", "a") != key("c", "a b")


@pytest.mark.parametrize("sep", ["] ", " ", "]", "[model=", '"', "\\", ":", ",", "{", "}"])
def test_no_separator_can_move_a_character_between_the_fields(sep):
    """Property sweep: moving `sep` across the field boundary must change the key.

    Built from the delimiter's own characters plus JSON's metacharacters, so it
    covers both historical schemes and the escaping the current one relies on —
    without encoding which trick happens to work.
    """
    assert key(f"model{sep}tail", "prompt") != key("model", f"tail{sep}prompt")


# ---------------------------------------------------------------------------
# Locks: the properties the key exists to provide
# ---------------------------------------------------------------------------


def test_identical_pairs_share_a_key():
    """Without this the cache would never hit at all."""
    assert key("claude-opus-4", "what is the capital of France?") == key(
        "claude-opus-4", "what is the capital of France?"
    )


def test_distinct_models_with_the_same_prompt_do_not_share_a_key():
    """D-005: the same prompt to two models is two cache entries."""
    assert key("claude-opus-4", "same prompt") != key("claude-haiku-4-5", "same prompt")


def test_distinct_prompts_to_the_same_model_do_not_share_a_key():
    assert key("claude-opus-4", "prompt a") != key("claude-opus-4", "prompt b")


def test_key_shape_is_unchanged():
    """16 hex chars, as before — storage and any operator tooling see the same shape."""
    k = key("claude-opus-4", "prompt")

    assert len(k) == 16
    assert all(c in "0123456789abcdef" for c in k)


def test_unicode_and_empty_fields_are_handled():
    assert key("", "") != key("", " ")
    assert key("modèle", "prompt") != key("model", "prompt")
    assert key("m", "prömpt") == key("m", "prömpt")


# ---------------------------------------------------------------------------
# The embedding input is deliberately NOT the key encoding
# ---------------------------------------------------------------------------


def test_scoped_prompt_stays_readable_text_for_the_embedder():
    """#125/#133 tuned this prefix against the 0.95 threshold.

    A JSON blob would put braces and quotes into the token stream and degrade
    the similarity signal, so the two uses diverge on purpose.
    """
    scoped = _KeyOnly()._scoped_prompt("what is 2+2?", "claude-opus-4")

    assert scoped == "[model=claude-opus-4] what is 2+2?"
    assert "{" not in scoped
