"""`dollars_saved` omitted the cache-write premium the bench charges (#196).

`ModelPricing` declares, documents, defaults and *validates*
`cache_write_multiplier = 1.25` -- guarded for sign, finiteness, `bool`-ness and
type across #71, #142 and #158. Exactly one half of the repo read it.
`scripts/bench_savings.py`, which produces `docs/savings.{json,md}`, the README
table and the dashboard, charges it. `cache_wrapper._dollars_saved`, the runtime
path, did not. So the number the README quickstart prints and the number the
dashboard shows came from two different cost models over one pricing table.

Measured on `claude-opus-4-8` ($5.00/MTok) with a 20k-token prefix, before:

    stream               dollars_saved     true net
    1 write, 0 reads       +0.000000       -0.025000
    1 write, 9 reads       +0.810000       +0.785000
    8 writes, 2 reads      +0.180000       -0.020000    <- sign flip

The last row is the point: a *cost-optimization* tool reporting a profit on a
run that cost more than not caching. It is structural, not situational --
`dollars_saved` is `read * rate * (1 - read_mult)` with every factor
non-negative, so the field cannot express a loss at all.

The tests below are differential where it matters: `test_net_savings_matches_the_bench`
drives the real `_run_baseline` / `_run_prompt_cache` and compares their
`saved_usd` to the wrapper's `net_dollars_saved` on the same token stream. Two
implementations, one input table -- not two readings of the same arithmetic.
"""

from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest

from cost_optimizer.cache_wrapper import CacheTelemetry, PromptCacheWrapper
from cost_optimizer.pricing import ModelPricing, get_pricing

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_savings import (  # noqa: E402
    CHEAP_MODEL,
    WorkloadRow,
    _run_baseline,
    _run_prompt_cache,
    _ws_count,
)

MODEL = "claude-opus-4-8"


class _Usage:
    def __init__(self, written: int, read: int) -> None:
        self.cache_creation_input_tokens = written
        self.cache_read_input_tokens = read
        self.input_tokens = 0


class _Response:
    def __init__(self, written: int, read: int) -> None:
        self.usage = _Usage(written, read)


class _FakeClient:
    """Duck-typed `client.messages.create`, replaying a fixed usage stream."""

    def __init__(self, stream: list[tuple[int, int]]) -> None:
        self._stream = list(stream)
        self.messages = self

    def create(self, **_: Any) -> _Response:
        return _Response(*self._stream.pop(0))


def _drive(stream: list[tuple[int, int]], *, model: str = MODEL) -> CacheTelemetry:
    wrapper = PromptCacheWrapper(_FakeClient(stream), model)
    for _ in stream:
        wrapper.create(system="stable prefix", messages=[{"role": "user", "content": "q"}])
    return wrapper.aggregate


PREFIX = 20_000

# (label, usage stream as (written, read) per call, expected sign of the net)
STREAMS = [
    ("1 write, 0 reads", [(PREFIX, 0)], -1),
    ("1 write, 1 read", [(PREFIX, 0), (0, PREFIX)], +1),
    ("1 write, 9 reads", [(PREFIX, 0), *[(0, PREFIX)] * 9], +1),
    ("10 writes, 0 reads", [(PREFIX, 0)] * 10, -1),
    ("8 writes, 2 reads", [*[(PREFIX, 0)] * 8, *[(0, PREFIX)] * 2], -1),
    ("0 writes, 3 reads", [(0, PREFIX)] * 3, +1),
    ("no cache activity", [(0, 0)] * 4, 0),
]
STREAM_IDS = [s[0] for s in STREAMS]


@pytest.mark.parametrize(("label", "stream", "sign"), STREAMS, ids=STREAM_IDS)
def test_net_has_the_right_sign(label: str, stream: list[tuple[int, int]], sign: int) -> None:
    """The property `dollars_saved` structurally cannot have: a negative value."""
    net = _drive(stream).net_dollars_saved
    if sign < 0:
        assert net < 0, f"{label}: expected a net loss, got {net!r}"
    elif sign > 0:
        assert net > 0, f"{label}: expected a net saving, got {net!r}"
    else:
        assert net == 0.0


@pytest.mark.parametrize(("label", "stream", "sign"), STREAMS, ids=STREAM_IDS)
def test_gross_dollars_saved_is_unchanged(
    label: str, stream: list[tuple[int, int]], sign: int
) -> None:
    """`dollars_saved` keeps its exact prior meaning and value: gross read-side
    savings, `read * rate * (1 - read_multiplier)`. The fix is additive, so
    nothing reading this field today changes -- including the README quickstart's
    `print(wrapped.aggregate.dollars_saved)`."""
    telem = _drive(stream)
    pricing = get_pricing(MODEL)
    rate = pricing.input_per_mtok / 1_000_000
    read_total = sum(r for _w, r in stream)
    assert telem.dollars_saved == pytest.approx(
        read_total * rate * (1.0 - pricing.cache_read_multiplier)
    )
    assert telem.dollars_saved >= 0.0


# --- the differential: the runtime wrapper vs. the offline bench -------------


def _bench_saved_for(n_calls: int, prefix_words: int) -> tuple[float, list[tuple[int, int]]]:
    """Run the real bench over a workload with one stable system prefix.

    Returns the bench's own `saved_usd` and the equivalent (written, read)
    usage stream a real Anthropic response would have reported for it: the
    first row writes the prefix, every later row reads it. The user portion of
    each prompt is billed at full rate by *both* the baseline and the strategy,
    so it cancels out of `saved_usd` and has no wrapper counterpart.
    """
    system = " ".join(f"w{i}" for i in range(prefix_words))
    prefix_tokens = _ws_count(system)
    rows = [
        WorkloadRow(
            row_id=f"r{i}",
            class_="redundant",
            prompt="ask",
            system=system,
            prompt_tokens=prefix_tokens + 5,
            completion_tokens=10,
            first_token_logprobs=(-0.1,),
            cheap_quality=0.9,
            strong_quality=0.95,
        )
        for i in range(n_calls)
    ]
    baseline = _run_baseline(rows)
    cached = _run_prompt_cache(rows, baseline)
    stream = [(prefix_tokens, 0), *[(0, prefix_tokens)] * (n_calls - 1)]
    return cached.saved_usd, stream


@pytest.mark.parametrize("n_calls", [1, 2, 3, 10, 50])
def test_net_savings_matches_the_bench(n_calls: int) -> None:
    """One token stream, two independent cost models, same answer.

    This is the assertion the fix exists for. `_run_prompt_cache` prices a
    cached call as `written * rate * write_mult + read * rate * read_mult`
    against an uncached baseline of `(written + read) * rate`; the wrapper
    computes `read * rate * (1 - read_mult) - written * rate * (write_mult - 1)`.
    Those rearrange to each other, and before #196 the wrapper's half was
    missing its second term.
    """
    bench_saved, stream = _bench_saved_for(n_calls, prefix_words=4_000)
    # The bench prices `CHEAP_MODEL`; drive the wrapper on the same model.
    telem = _drive(stream, model=CHEAP_MODEL)
    assert telem.net_dollars_saved == pytest.approx(bench_saved, rel=1e-12, abs=1e-15)


def test_the_single_call_case_is_the_one_that_flips() -> None:
    """`n_calls=1` is a cache write with no read -- and the bench, which has
    always charged the write multiplier, has always called that a loss."""
    bench_saved, _stream = _bench_saved_for(1, prefix_words=4_000)
    assert bench_saved < 0


# --- the premium's own arithmetic -------------------------------------------


def test_premium_is_the_extra_over_not_caching_not_the_whole_rate() -> None:
    """`cache_creation_input_tokens` bills at 1.25x the input rate *instead of*
    1x, so the extra is 0.25x -- not 1.25x. Getting this wrong would overstate
    the cost fivefold."""
    telem = _drive([(PREFIX, 0)])
    pricing = get_pricing(MODEL)
    rate = pricing.input_per_mtok / 1_000_000
    assert telem.dollars_write_premium == pytest.approx(PREFIX * rate * 0.25)
    assert telem.dollars_write_premium != pytest.approx(PREFIX * rate * 1.25)


def test_sub_one_write_multiplier_is_a_saving_not_a_clamped_zero() -> None:
    """No `max(0.0, ...)` on the premium.

    `ModelPricing.__post_init__` validates `cache_write_multiplier >= 0.0`, not
    `>= 1.0`, and `tests/test_pricing.py` already constructs `0.0`. A multiplier
    below 1.0 means writing is genuinely cheaper than not caching; clamping
    would launder that real saving into a wrong zero, and the value would never
    reach the guard that questioned it.
    """
    free_writes = ModelPricing("cheap-writes", 5.00, cache_write_multiplier=0.0)
    wrapper = PromptCacheWrapper(_FakeClient([(PREFIX, 0)]), MODEL, pricing=free_writes)
    wrapper.create(system="s", messages=[{"role": "user", "content": "q"}])
    telem = wrapper.aggregate
    rate = free_writes.input_per_mtok / 1_000_000
    assert telem.dollars_write_premium == pytest.approx(-PREFIX * rate)
    assert telem.net_dollars_saved > 0.0


def test_net_is_derived_not_stored() -> None:
    """A stored net could drift from its inputs across `merge`. It is a
    property, so it cannot."""
    assert "net_dollars_saved" not in {f.name for f in fields(CacheTelemetry)}
    a = CacheTelemetry(1, 0, 100, 0, 0.5, 0.0)
    b = CacheTelemetry(0, 1, 0, 100, 0.0, 0.2)
    merged = a.merge(b)
    assert merged.dollars_write_premium == pytest.approx(0.2)
    assert merged.net_dollars_saved == pytest.approx(0.3)


def test_zero_telemetry_carries_the_new_field() -> None:
    z = CacheTelemetry.zero()
    assert z.dollars_write_premium == 0.0
    assert z.net_dollars_saved == 0.0


def test_new_field_defaults_so_existing_construction_still_works() -> None:
    """Five positional args is how this dataclass was constructed before #196,
    in this repo and possibly in a caller's. The new field is defaulted so that
    call site keeps working rather than becoming a TypeError."""
    legacy = CacheTelemetry(2, 1, 500, 100, 0.004)
    assert legacy.dollars_write_premium == 0.0
    assert legacy.net_dollars_saved == pytest.approx(0.004)


# --- the sink gets the net ---------------------------------------------------


def test_to_dict_and_dump_carry_the_net(tmp_path: Path) -> None:
    """Before #196 `to_dict` carried the benefit in *dollars* and the cost in
    *tokens*, and no rate -- so a statsd/Prometheus/dashboard consumer holding
    that payload could not convert one into the other. The net was not merely
    un-reported at the sink, it was un-derivable there."""
    wrapper = PromptCacheWrapper(_FakeClient([(PREFIX, 0), (0, PREFIX)]), MODEL)
    for _ in range(2):
        wrapper.create(system="s", messages=[{"role": "user", "content": "q"}])

    payload = wrapper.aggregate.to_dict()
    assert payload["net_dollars_saved"] == pytest.approx(
        payload["dollars_saved"] - payload["dollars_write_premium"]
    )

    out = tmp_path / "agg.json"
    wrapper.dump_aggregate_json(out)
    on_disk = json.loads(out.read_text())
    assert on_disk == payload
