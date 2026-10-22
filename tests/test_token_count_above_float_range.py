"""A usage token count too large for a float abstains; it does not lose the response (#350).

`_coerce_token_count` caught `int(inf)`'s `OverflowError`, but an int that is
*already* above float range passed straight through ``int()`` and then crashed
the float math downstream. Measured on 9771c24:

    cache_read_input_tokens / cache_creation_input_tokens = 10**400 (a JSON
    ``1`` + 400 zeros), its string form, or 10**309
        -> PromptCacheWrapper.create raised OverflowError after the paid call
    batch usage.input_tokens = 10**400
        -> _from_sdk_result_row built a row with error=None, and
           compare_realtime_vs_batch raised OverflowError
    a row with prompt_tokens = 10**308 (a float CAN hold it)
        -> compare_realtime_vs_batch returned realtime_usd=inf,
           savings_usd=nan, savings_pct=nan, silently

`inf`, a normal count and `10**308` in the cache wrapper are controls: they
behaved before and must behave the same now.
"""

from __future__ import annotations

import json
import math
from types import SimpleNamespace
from typing import Any

import pytest

from cost_optimizer.batch import (
    BatchCostQuote,
    BatchResultRow,
    _from_sdk_result_row,
    compare_realtime_vs_batch,
)
from cost_optimizer.cache_wrapper import PromptCacheWrapper
from cost_optimizer.pricing import _coerce_token_count

BIG = json.loads("1" + "0" * 400)  # what a JSON decoder hands over
HUGE = {"10**400": BIG, "10**400-as-str": str(BIG), "10**309": 10**309}
MODEL = "claude-haiku-4-5"
QUOTES = {"m": BatchCostQuote("m", 3.0, 15.0)}


class _Client:
    def __init__(self, **usage: Any) -> None:
        self.response = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="the paid answer")],
            usage=SimpleNamespace(**usage),
        )
        self.messages = SimpleNamespace(create=lambda **_: self.response)


def _succeeded_entry(input_tokens: Any, output_tokens: Any = 7) -> Any:
    usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
    message = SimpleNamespace(content=[SimpleNamespace(type="text", text="x")], usage=usage)
    return SimpleNamespace(custom_id="a", result=SimpleNamespace(type="succeeded", message=message))


# --- red on 9771c24 ------------------------------------------------------------


@pytest.mark.parametrize("value", list(HUGE.values()), ids=list(HUGE))
def test_coerce_abstains_on_an_int_too_large_for_a_float(value: Any) -> None:
    assert _coerce_token_count(value) == 0


@pytest.mark.parametrize("value", list(HUGE.values()), ids=list(HUGE))
@pytest.mark.parametrize("field", ["cache_read_input_tokens", "cache_creation_input_tokens"])
def test_cache_wrapper_returns_the_response_and_savings_abstain(field: str, value: Any) -> None:
    client = _Client(**{field: value})
    wrapper = PromptCacheWrapper(client, MODEL)
    result = wrapper.create(messages=[])
    assert result.response is client.response
    t = result.telemetry
    assert (t.tokens_cached, t.tokens_written) == (0, 0)
    assert (t.dollars_saved, t.dollars_write_premium) == (0.0, 0.0)


@pytest.mark.parametrize("value", list(HUGE.values()), ids=list(HUGE))
def test_batch_row_abstains_and_the_comparison_prices_the_rest(value: Any) -> None:
    row = _from_sdk_result_row(_succeeded_entry(value))
    assert row.error is None
    assert (row.prompt_tokens, row.completion_tokens) == (0, 7)
    cmp = compare_realtime_vs_batch([row], QUOTES)
    assert math.isfinite(cmp.realtime_usd)
    assert math.isfinite(cmp.savings_pct)
    assert cmp.realtime_usd == round(7 * 15.0 / 1_000_000, 6)


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens"])
def test_comparison_names_a_hand_built_row_above_float_range(field: str) -> None:
    kw = {"prompt_tokens": 1, "completion_tokens": 1, field: BIG}
    row = BatchResultRow(custom_id="r1", response_text="x", **kw)
    with pytest.raises(ValueError, match=rf"custom_id='r1'.*{field}"):
        compare_realtime_vs_batch([row], QUOTES)


@pytest.mark.parametrize("field", ["prompt_tokens", "completion_tokens"])
def test_comparison_refuses_rather_than_publishing_inf_and_nan(field: str) -> None:
    # 10**308 fits a float, but `10**308 * 3.0` (or `* 15.0`) does not.
    kw = {"prompt_tokens": 0, "completion_tokens": 0, field: 10**308}
    row = BatchResultRow(custom_id="a", response_text="x", **kw)
    with pytest.raises(ValueError, match="overflow a float"):
        compare_realtime_vs_batch([row], QUOTES)


# --- controls: green before and after -------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5, 5),
        ("5", 5),
        (None, 0),
        (float("inf"), 0),
        (float("nan"), 0),
        (-3, 0),
        (10**308, 10**308),
    ],
    ids=["int", "str", "None", "inf", "nan", "negative", "10**308"],
)
def test_coerce_is_unchanged_on_every_other_shape(value: Any, expected: int) -> None:
    assert _coerce_token_count(value) == expected


@pytest.mark.parametrize("value", [float("inf"), 10**300], ids=["inf", "10**300"])
def test_cache_wrapper_already_survived_inf_and_a_large_int_that_fits(value: Any) -> None:
    result = PromptCacheWrapper(_Client(cache_read_input_tokens=value), MODEL).create(messages=[])
    assert math.isfinite(result.telemetry.dollars_saved)


def test_a_normal_count_prices_exactly_as_before() -> None:
    client = _Client(cache_read_input_tokens=20_000, cache_creation_input_tokens=1_000)
    t = PromptCacheWrapper(client, MODEL).create(messages=[]).telemetry
    assert (t.tokens_cached, t.tokens_written) == (20_000, 1_000)
    assert t.dollars_saved > 0
    row = _from_sdk_result_row(_succeeded_entry(1_000, 500))
    cmp = compare_realtime_vs_batch([row], QUOTES)
    assert cmp.realtime_usd == round(1_000 * 3.0 / 1e6 + 500 * 15.0 / 1e6, 6)
    assert cmp.savings_pct == 0.5
