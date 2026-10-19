"""A `prices` key must be the model id its quote prices (#325).

`compare_realtime_vs_batch` read the model from the quote on its single-model
path and looked rows up by key; nothing compared the two. Measured on main:

    {"claude-haiku-4-5": opus, "claude-opus-4-8": haiku}, two haiku rows
        -> realtime_usd 10.0  (priced at Opus; expected 2.0), no error
    {"haiku": haiku}, model_of omitted
        -> KeyError "prices missing entry for model='claude-haiku-4-5'"
"""

from __future__ import annotations

import pytest

from cost_optimizer.batch import BatchCostQuote, BatchResultRow, compare_realtime_vs_batch

ROWS = [BatchResultRow("r1", "ok", 1_000_000, 0), BatchResultRow("r2", "ok", 1_000_000, 0)]
HAIKU = BatchCostQuote("claude-haiku-4-5", 1.0, 5.0)
OPUS = BatchCostQuote("claude-opus-4-8", 5.0, 25.0)
BOTH_HAIKU = {"r1": "claude-haiku-4-5", "r2": "claude-haiku-4-5"}


def test_swapped_keys_are_refused_instead_of_mispriced() -> None:
    with pytest.raises(
        ValueError, match=r"prices\['claude-haiku-4-5'\] holds the quote for 'claude-opus-4-8'"
    ):
        compare_realtime_vs_batch(
            ROWS, {"claude-haiku-4-5": OPUS, "claude-opus-4-8": HAIKU}, model_of=BOTH_HAIKU
        )


@pytest.mark.parametrize(
    "model_of", [None, {"r1": "haiku", "r2": "haiku"}], ids=["implicit", "explicit"]
)
def test_an_alias_key_is_refused_naming_both(model_of: dict[str, str] | None) -> None:
    with pytest.raises(
        ValueError, match=r"prices\['haiku'\] holds the quote for 'claude-haiku-4-5'"
    ):
        compare_realtime_vs_batch(ROWS, {"haiku": HAIKU}, model_of=model_of)


def test_a_value_that_is_not_a_quote_is_refused() -> None:
    with pytest.raises(
        ValueError, match=r"prices\['claude-haiku-4-5'\] must be a BatchCostQuote; got dict"
    ):
        compare_realtime_vs_batch(ROWS, {"claude-haiku-4-5": {"input_per_mtok": 1.0}})  # type: ignore[dict-item]


def test_a_bad_table_is_refused_even_for_an_empty_batch() -> None:
    with pytest.raises(ValueError, match="holds the quote for"):
        compare_realtime_vs_batch([], {"haiku": HAIKU})


def test_well_formed_tables_price_as_before() -> None:
    single = compare_realtime_vs_batch(ROWS, {"claude-haiku-4-5": HAIKU})
    assert single.realtime_usd == pytest.approx(2.0)
    multi = compare_realtime_vs_batch(
        ROWS,
        {"claude-haiku-4-5": HAIKU, "claude-opus-4-8": OPUS},
        model_of={"r1": "claude-haiku-4-5", "r2": "claude-opus-4-8"},
    )
    assert multi.realtime_usd == pytest.approx(6.0)
