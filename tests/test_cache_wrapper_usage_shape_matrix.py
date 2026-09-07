"""`_get_usage` must tolerate dict shape at *both* levels, not one (#209).

"Dict-shaped" is a property of the response and, independently, of the `usage`
it carries. That is a 2x2, and `_get_usage` used to decide once for both — it
returned `response.usage` raw off the attribute road and wrapped
`response["usage"]` unconditionally off the dict road. The two combinations
where the levels agree read correctly; the two mixed ones both read **zero**,
and neither raised.

Zero is the worst possible wrong answer here, because it is also a *legitimate*
one. `_coerce_token_count` is documented to abstain to `0` on a malformed usage
field, so "well-formed value in the other container" and "this call did no
caching" produce byte-identical telemetry. It reaches the savings dashboard as
`$0.00`.

The whole 2x2 is asserted below rather than the two rows that were broken,
because each of the two obvious half-fixes closes exactly one row and reads as
complete. `test_neither_half_fix_shipped` builds and runs both.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from cost_optimizer.cache_wrapper import PromptCacheWrapper, _DictAttr, _get_usage

# A 20k-token cache read on claude-opus-4-8 ($5.00/MTok, 0.10x read multiplier):
# 20000 * 5.00/1e6 * 0.90 = $0.09 exactly.
MODEL = "claude-opus-4-8"
READ_TOKENS = 20_000
WRITE_TOKENS = 0
EXPECTED_SAVED = 0.09

USAGE_FIELDS: dict[str, int] = {
    "cache_creation_input_tokens": WRITE_TOKENS,
    "cache_read_input_tokens": READ_TOKENS,
}


def _as_object(d: dict[str, int]) -> Any:
    return SimpleNamespace(**d)


def _as_dict(d: dict[str, int]) -> Any:
    return dict(d)


# The 2x2 itself: (label, response container, usage container).
SHAPE_MATRIX: tuple[tuple[str, str, str], ...] = (
    ("object response, object usage", "object", "object"),
    ("object response, dict usage", "object", "dict"),
    ("dict response, object usage", "dict", "object"),
    ("dict response, dict usage", "dict", "dict"),
)


def _build_response(response_kind: str, usage_kind: str) -> Any:
    usage = _as_object(USAGE_FIELDS) if usage_kind == "object" else _as_dict(USAGE_FIELDS)
    if response_kind == "object":
        return SimpleNamespace(usage=usage)
    return {"usage": usage}


class _FakeClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **_kwargs: Any) -> Any:
        return self.response


def _telemetry_for(response: Any) -> Any:
    wrapper = PromptCacheWrapper(_FakeClient(response), MODEL)
    return wrapper.create(
        system="a long cacheable system prompt",
        messages=[{"role": "user", "content": "hi"}],
    ).telemetry


# --------------------------------------------------------------------------
# The matrix
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "response_kind", "usage_kind"), SHAPE_MATRIX, ids=[c[0] for c in SHAPE_MATRIX]
)
def test_all_four_container_combinations_read_the_same_telemetry(
    label: str, response_kind: str, usage_kind: str
) -> None:
    telem = _telemetry_for(_build_response(response_kind, usage_kind))
    assert telem.tokens_cached == READ_TOKENS
    assert telem.tokens_written == WRITE_TOKENS
    assert telem.hits == 1
    assert telem.misses == 0
    assert telem.dollars_saved == pytest.approx(EXPECTED_SAVED)


def test_the_four_combinations_are_indistinguishable_from_each_other() -> None:
    """Stated as an equality between rows, not four separate expectations.

    This is the assertion that survives a future change to the rate table or
    the multipliers: whatever the right number is, the container the SDK
    happened to use must not change it.
    """
    dicts = [_telemetry_for(_build_response(r, u)).to_dict() for _, r, u in SHAPE_MATRIX]
    assert dicts[1:] == dicts[:-1], f"container shape changed the telemetry: {dicts}"


# --------------------------------------------------------------------------
# The degenerate rows still abstain — this fix is not about malformed input
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "response"),
    [
        ("no usage attribute at all", SimpleNamespace()),
        ("usage is None", SimpleNamespace(usage=None)),
        ("dict response, no usage key", {}),
        ("dict response, usage is None", {"usage": None}),
        ("usage is a list", SimpleNamespace(usage=[1, 2])),
        ("usage is a string", SimpleNamespace(usage="20000")),
        ("usage is an int", SimpleNamespace(usage=20_000)),
        ("response is a bare string", "not a response"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_unusable_usage_still_abstains_to_zero(label: str, response: Any) -> None:
    """The "abstain, don't crash on malformed SDK shapes" contract (#114/#136).

    Deliberately unchanged. The bug was a *well-formed* value being read as
    absent, not a malformed one being tolerated — so widening #209 into "make
    every shape work" would be a different, larger change.
    """
    telem = _telemetry_for(response)
    assert (telem.hits, telem.misses, telem.tokens_cached, telem.tokens_written) == (0, 0, 0, 0)
    assert telem.dollars_saved == 0.0


# --------------------------------------------------------------------------
# Precedence between the two roads is unchanged
# --------------------------------------------------------------------------


class _DictWithAttr(dict[str, Any]):
    """A dict subclass that also carries a `.usage` attribute.

    Both roads are open for this object. The attribute road won before #209 and
    must still win, or the fix has quietly changed which value a caller reads.
    """

    usage: Any


def test_attribute_road_still_wins_over_the_dict_road() -> None:
    response = _DictWithAttr({"usage": {"cache_read_input_tokens": 1}})
    response.usage = SimpleNamespace(cache_read_input_tokens=999, cache_creation_input_tokens=0)
    assert _telemetry_for(response).tokens_cached == 999


# --------------------------------------------------------------------------
# Neither half-fix shipped
# --------------------------------------------------------------------------


def test_neither_half_fix_shipped() -> None:
    """Both obvious one-liners close exactly one mixed row and read as complete.

    They are built and run here rather than described, because each one passes
    every test derived from the *other* row — which is how a half-fix ships.
    """

    def half_fix_a(response: Any) -> Any:
        """Teach `_DictAttr` to fall back to `getattr` on a non-mapping.

        Closes `dict` response + `object` usage. Leaves `object` + `dict`
        exactly as broken as before, because that road never reaches
        `_DictAttr` at all.
        """
        if hasattr(response, "usage"):
            return response.usage
        if isinstance(response, dict) and "usage" in response:
            inner = response["usage"]
            return inner if not isinstance(inner, dict) else _DictAttr(inner)
        return _DictAttr({})

    def half_fix_b(response: Any) -> Any:
        """Wrap a dict `usage` found on the attribute road.

        Closes `object` response + `dict` usage. Leaves `dict` + `object`
        broken, because that road still wraps unconditionally.
        """
        if hasattr(response, "usage"):
            u = response.usage
            return _DictAttr(u) if isinstance(u, dict) else u
        if isinstance(response, dict) and "usage" in response:
            return _DictAttr(response["usage"])
        return _DictAttr({})

    def read(get_usage: Any, response: Any) -> int:
        usage = get_usage(response)
        return int(getattr(usage, "cache_read_input_tokens", 0))

    mixed_object_dict = _build_response("object", "dict")
    mixed_dict_object = _build_response("dict", "object")

    # Each half-fix is genuinely a fix — for its own row. Asserting that first
    # is what makes the failures below mean "incomplete", not "broken".
    assert read(half_fix_a, mixed_dict_object) == READ_TOKENS
    assert read(half_fix_a, mixed_object_dict) == 0, "half-fix A leaves object+dict broken"
    assert read(half_fix_b, mixed_object_dict) == READ_TOKENS
    assert read(half_fix_b, mixed_dict_object) == 0, "half-fix B leaves dict+object broken"

    # What shipped closes both.
    assert read(_get_usage, mixed_object_dict) == READ_TOKENS
    assert read(_get_usage, mixed_dict_object) == READ_TOKENS


def test_the_swallowed_attributeerror_is_the_reason_the_dict_road_was_silent() -> None:
    """`_DictAttr` over a non-mapping raises, and the caller's `getattr` eats it.

    Recorded as an executable fact because it is the non-obvious half of the
    bug: the defensive three-argument `getattr` is what turned the failure into
    a plausible zero instead of a traceback someone would have noticed.
    """
    bad = _DictAttr(SimpleNamespace(cache_read_input_tokens=5))  # type: ignore[arg-type]
    with pytest.raises(AttributeError):
        _ = bad.cache_read_input_tokens
    assert getattr(bad, "cache_read_input_tokens", 0) == 0


# --------------------------------------------------------------------------
# The harm, at the surface an operator reads
# --------------------------------------------------------------------------


def test_aggregate_and_dumped_json_report_the_real_savings(tmp_path: Path) -> None:
    """`merge` only ever adds, so a zeroed call is never recovered downstream.

    Ten calls against an object response carrying a dict usage used to roll up
    to `$0.00` saved with zero hits — a savings tool reporting that its own
    product does nothing.
    """
    wrapper = PromptCacheWrapper(_FakeClient(_build_response("object", "dict")), MODEL)
    for _ in range(10):
        wrapper.create(system="s", messages=[{"role": "user", "content": "hi"}])

    agg = wrapper.aggregate
    assert agg.hits == 10
    assert agg.tokens_cached == 10 * READ_TOKENS
    assert agg.dollars_saved == pytest.approx(10 * EXPECTED_SAVED)

    out = tmp_path / "aggregate.json"
    wrapper.dump_aggregate_json(out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["hits"] == 10
    assert payload["dollars_saved"] == pytest.approx(10 * EXPECTED_SAVED)
    assert payload["net_dollars_saved"] == pytest.approx(10 * EXPECTED_SAVED)
