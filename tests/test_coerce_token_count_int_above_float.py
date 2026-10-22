"""_coerce_token_count abstains when the int is too large for a float (#350).

Previously ``_coerce_token_count`` caught ``OverflowError`` from
``int(float("inf"))`` but a Python int *already* too large for a float
(e.g. ``json.loads("1" + "0" * 400)``) passes ``int()`` unchanged and then
crashes in ``_dollars_saved`` / ``compare_realtime_vs_batch`` float arithmetic.

The fix: call ``is_finite`` on the coerced int and abstain to 0 when False.
"""

from __future__ import annotations

import json
import types

import pytest

from cost_optimizer.pricing import _coerce_token_count

# What a JSON config / SDK response with an absurdly large number hands over.
BIG = json.loads("1" + "0" * 400)
BIG_STR = "1" + "0" * 400  # string form of BIG


class TestCoerceTokenCountDirectly:
    """Unit tests for _coerce_token_count itself."""

    def test_big_int_abstains_to_zero(self) -> None:
        assert _coerce_token_count(BIG) == 0

    def test_negative_big_int_abstains_to_zero(self) -> None:
        assert _coerce_token_count(-BIG) == 0

    def test_big_int_as_string_abstains_to_zero(self) -> None:
        # json.loads of a 401-digit string is already a big int; passing the
        # *string* form still goes through int() which makes a big int → abstain.
        assert _coerce_token_count(BIG_STR) == 0

    def test_10_to_309_abstains(self) -> None:
        # 10**309 is larger than the float max (≈1.8e308).
        assert _coerce_token_count(10**309) == 0

    def test_float_inf_abstains(self) -> None:
        assert _coerce_token_count(float("inf")) == 0

    def test_float_nan_abstains(self) -> None:
        assert _coerce_token_count(float("nan")) == 0

    # --- controls: normal values are unchanged ---

    def test_normal_int_unchanged(self) -> None:
        assert _coerce_token_count(42) == 42

    def test_zero_unchanged(self) -> None:
        assert _coerce_token_count(0) == 0

    def test_none_coerces_to_zero(self) -> None:
        assert _coerce_token_count(None) == 0

    def test_negative_int_abstains(self) -> None:
        assert _coerce_token_count(-1) == 0

    def test_10_to_308_is_accepted(self) -> None:
        # 10**308 fits a float (barely), so it is a valid token count.
        assert _coerce_token_count(10**308) == 10**308

    def test_float_inf_neg_abstains(self) -> None:
        assert _coerce_token_count(float("-inf")) == 0

    def test_string_non_numeric_abstains(self) -> None:
        assert _coerce_token_count("abc") == 0

    def test_normal_float_coerces(self) -> None:
        # A finite float coerces to int.
        assert _coerce_token_count(7.9) == 7

    def test_large_valid_int_accepted(self) -> None:
        # 10**15 is well within float range.
        assert _coerce_token_count(10**15) == 10**15

