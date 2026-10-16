"""The sweep's default dollars are the pricing table's, for the models it routes (#310).

`--cheap-dollars` / `--strong-dollars` defaulted to the literals `0.0008` /
`0.015`: 1000 input tokens at $0.80 and $15.00 per MTok, which are Haiku 3.5's
and Opus 4.1's rates. The router inside `sweep()` names `claude-haiku-4-5` and
`claude-opus-4-7`, which `cost_optimizer/pricing.py` prices at $1.00 and $5.00.
#90 refreshed Opus 4.7 in that table and regenerated the savings bench; the
refresh could not reach a number typed into this script's parser. Measured on
`origin/main` before this change::

    defaults cheap 0.0008 strong 0.015 ratio 18.75
    pricing table haiku-4-5 1.0 opus-4-7 5.0 ratio 5.0

So the committed `docs/threshold_demo.json` priced escalating everything at
$0.0158/request, 19.75x the cheap-only rows; at the table's rates it is $0.006,
6x. The quality-vs-cost curve this script exists to draw overstated the cost of
escalation 3.75x.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cost_optimizer.pricing import ModelPricing, get_pricing  # noqa: E402
from scripts import tune_threshold  # noqa: E402
from scripts.tune_threshold import (  # noqa: E402
    CHEAP_MODEL,
    REQUEST_INPUT_TOKENS,
    STRONG_MODEL,
    _build_parser,
    _build_sample_items,
    sweep,
)

COMMITTED = _REPO_ROOT / "docs" / "threshold_demo.json"


def _table_dollars(model: str) -> float:
    return REQUEST_INPUT_TOKENS * get_pricing(model).input_per_mtok / 1_000_000


def test_the_defaults_are_the_pricing_table_for_the_routed_models() -> None:
    args = _build_parser().parse_args([])
    assert args.cheap_dollars == _table_dollars(CHEAP_MODEL)
    assert args.strong_dollars == _table_dollars(STRONG_MODEL)


def test_the_defaults_are_not_the_retired_haiku_3_5_and_opus_4_1_rates() -> None:
    """The literal values, so a revert to them is red even if the table moves."""
    args = _build_parser().parse_args([])
    assert (args.cheap_dollars, args.strong_dollars) != (0.0008, 0.015)
    # Haiku 4.5 -> Opus 4.7 is 5x on input; the stale pair was 18.75x.
    assert args.strong_dollars / args.cheap_dollars == pytest.approx(
        get_pricing(STRONG_MODEL).input_per_mtok / get_pricing(CHEAP_MODEL).input_per_mtok
    )


def test_the_models_priced_are_the_models_routed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`sweep()` routes between the same two names the defaults are priced from.

    Records the `UncertaintyRouter` construction rather than trusting the
    constants: two string literals that merely happen to agree today is the
    shape that let the dollars drift in the first place.
    """
    seen: list[tuple[str, str]] = []
    real_router = tune_threshold.UncertaintyRouter

    def recording_router(*args, **kwargs):
        seen.append((kwargs["cheap_model"], kwargs["strong_model"]))
        return real_router(*args, **kwargs)

    monkeypatch.setattr(tune_threshold, "UncertaintyRouter", recording_router)
    sweep(_build_sample_items(), [0.5], cheap_dollars=0.0, strong_dollars=0.0)
    assert seen == [(CHEAP_MODEL, STRONG_MODEL)]


def test_a_pricing_table_refresh_reaches_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """The point of deriving them: the next #90-style refresh needs no edit here."""
    refreshed = {
        CHEAP_MODEL: ModelPricing(CHEAP_MODEL, 2.00),
        STRONG_MODEL: ModelPricing(STRONG_MODEL, 7.00),
    }
    monkeypatch.setattr(tune_threshold, "get_pricing", lambda m: refreshed[m])
    args = _build_parser().parse_args([])
    assert args.cheap_dollars == pytest.approx(0.002)
    assert args.strong_dollars == pytest.approx(0.007)


def test_an_explicit_flag_still_overrides_the_default() -> None:
    args = _build_parser().parse_args(["--cheap-dollars", "0.25", "--strong-dollars", "0.5"])
    assert (args.cheap_dollars, args.strong_dollars) == (0.25, 0.5)


def test_the_committed_artifact_is_priced_from_the_table() -> None:
    """`docs/threshold_demo.json` is what the README's command writes."""
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    cheap, strong = _table_dollars(CHEAP_MODEL), _table_dollars(STRONG_MODEL)
    assert committed["cheap_dollars_per_request"] == cheap
    assert committed["strong_dollars_per_request"] == strong
    by_threshold = {r["threshold"]: r for r in committed["rows"]}
    # Escalate-everything pays cheap *and* strong on every row; escalate-nothing
    # pays cheap only. Both ends are exact functions of the two rates.
    assert by_threshold[0.0]["escalation_rate"] == 1.0
    assert by_threshold[0.0]["dollars_per_request"] == pytest.approx(cheap + strong)
    assert by_threshold[2.0]["escalation_rate"] == 0.0
    assert by_threshold[2.0]["dollars_per_request"] == pytest.approx(cheap)
