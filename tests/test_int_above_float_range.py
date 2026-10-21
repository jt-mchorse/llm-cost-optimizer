"""An int too large for a float gets the field-named answer, not OverflowError (#344).

`math.isfinite(10**400)` and `float(10**400)` both raise `OverflowError`, which is
not a `ValueError`. A JSON decoder produces that int (`json.loads` of `1` and 400
zeros; `1e400` is `inf`, which the guards already handled). Measured on 35cd5f9,
every row below raised `OverflowError: int too large to convert to float`:

    SemanticCache(default_ttl_s=10**400) / put(ttl_s=10**400)
    BatchCostQuote("m", 10**400, 1.0) / ModelPricing("m", 10**400)
    EntropySignal(threshold=10**400) / JudgeConfidenceSignal(threshold=10**400)
    an embedder returning [10**400, 1]           -> put / lookup
    an embedder returning [10**200, 1]           -> lookup (cosine's sqrt)
    request_counts.succeeded = 10**400           -> AnthropicBatchBackend.poll
    first_token_logprobs / top_logprobs [10**400] -> EntropySignal.measure
    a judge verdict with score 10**400           -> JudgeConfidenceSignal.measure

The `10**308` rows are controls: an int a float CAN hold is judged on its value,
exactly as before.
"""

from __future__ import annotations

import json
import types
from typing import Any

import pytest

from cost_optimizer import HashEmbedder, SemanticCache
from cost_optimizer.batch import AnthropicBatchBackend, BatchCostQuote
from cost_optimizer.pricing import ModelPricing
from cost_optimizer.router import EntropySignal, JudgeConfidenceSignal
from cost_optimizer.semantic_cache import InMemoryStorage, cosine
from cost_optimizer.shapes import is_finite

BIG = json.loads("1" + "0" * 400)  # what a config file hands over
FITS = 10**308  # an int a float can hold


def test_the_huge_int_is_what_a_json_decoder_produces() -> None:
    assert type(BIG) is int
    assert BIG == 10**400
    with pytest.raises(OverflowError):
        float(BIG)


def test_is_finite_answers_instead_of_raising() -> None:
    assert is_finite(BIG) is False
    assert is_finite(-BIG) is False
    # Controls: unchanged from `math.isfinite`.
    assert is_finite(FITS) is True
    assert is_finite(1.5) is True
    assert is_finite(float("inf")) is False
    assert is_finite(float("nan")) is False


def _cache(embedder: Any = None, **kw: Any) -> SemanticCache:
    return SemanticCache(embedder=embedder or HashEmbedder(), storage=InMemoryStorage(), **kw)


# --- the seven listed guards ------------------------------------------------

GUARDS = {
    "default_ttl_s": (lambda v: _cache(default_ttl_s=v), "default_ttl_s"),
    "put-ttl_s": (lambda v: _cache().put("q", "a", model="m", ttl_s=v), "ttl_s"),
    "BatchCostQuote": (lambda v: BatchCostQuote("m", v, 1.0), "input_per_mtok"),
    "ModelPricing": (lambda v: ModelPricing("m", v), "input_per_mtok"),
    "EntropySignal": (lambda v: EntropySignal(threshold=v), "EntropySignal.threshold"),
    "JudgeConfidenceSignal": (
        lambda v: JudgeConfidenceSignal(judge=None, rubric="r", threshold=v),
        "JudgeConfidenceSignal.threshold",
    ),
}


@pytest.mark.parametrize("site", sorted(GUARDS))
@pytest.mark.parametrize("value", [BIG, -BIG], ids=["10**400", "-10**400"])
def test_a_huge_int_gets_the_field_named_value_error(site: str, value: int) -> None:
    build, field = GUARDS[site]
    with pytest.raises(ValueError, match=field):
        build(value)


@pytest.mark.parametrize(
    "site", ["default_ttl_s", "put-ttl_s", "BatchCostQuote", "ModelPricing", "EntropySignal"]
)
def test_control_an_int_a_float_can_hold_is_still_accepted(site: str) -> None:
    build, _ = GUARDS[site]
    build(FITS)


class _Fixed:
    def __init__(self, vector: list[Any]) -> None:
        self.vector = vector

    def embed(self, text: str) -> list[Any]:
        return list(self.vector)


@pytest.mark.parametrize("op", ["put", "lookup"])
def test_an_embedding_component_above_float_range_is_refused_at_the_seam(op: str) -> None:
    cache = _cache(_Fixed([BIG, 1]))
    call = getattr(cache, op)
    with pytest.raises(ValueError, match=f"{op} embedder.*index 0"):
        call("q", "a", model="m") if op == "put" else call("q", model="m")


# --- sites the issue did not list --------------------------------------------


@pytest.mark.parametrize("magnitude", [10**200, FITS], ids=["10**200", "10**308"])
def test_a_stored_large_int_vector_misses_like_its_float_twin(magnitude: int) -> None:
    """`put` admits these (they fit a float), and `lookup` then crashed in cosine.

    `sum(x*x)` over ints stays an exact int, so it never overflowed to the `inf`
    cosine's fallback is written for; `math.sqrt` raised converting it instead.
    """
    results = []
    for vector in ([magnitude, 1], [float(magnitude), 1.0]):
        cache = _cache(_Fixed(vector))
        cache.put("q", "a", model="m")
        results.append(cache.lookup("q", model="m"))
    as_int, as_float = results
    assert (as_int.hit, as_int.similarity) == (as_float.hit, as_float.similarity)


def test_cosine_of_a_vector_no_float_can_hold_is_the_zero_fallback() -> None:
    # A record `put` did not write (a hand-edited Redis blob) can carry one.
    assert cosine([BIG, 1], [1, 1]) == 0.0


def test_control_cosine_of_small_int_vectors_is_unchanged() -> None:
    assert cosine([3, 4], [3, 4]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([1, 1], [1, 0]) == pytest.approx(2**-0.5)


def _poll(**counts: Any) -> Any:
    base = {"processing": 0, "succeeded": 0, "errored": 0, "canceled": 0, "expired": 0}
    resp = types.SimpleNamespace(
        id="msgbatch_x",
        processing_status="in_progress",
        cancel_initiated_at=None,
        request_counts=types.SimpleNamespace(**{**base, **counts}),
        created_at="2026-10-21T00:00:00Z",
    )
    batches = types.SimpleNamespace(retrieve=lambda job_id: resp, results=lambda job_id: [])
    backend = AnthropicBatchBackend(
        types.SimpleNamespace(messages=types.SimpleNamespace(batches=batches))
    )
    return backend.poll("msgbatch_x")


def test_a_huge_int_request_count_sums_with_the_others() -> None:
    assert _poll(succeeded=BIG, errored=2).n_requests == BIG + 2


def test_control_ordinary_request_counts_sum() -> None:
    assert _poll(succeeded=3, errored=2).n_requests == 5


def _text(**extra: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "hi", **extra}]}


@pytest.mark.parametrize(
    "response",
    [
        {"first_token_logprobs": [BIG, -1.0]},
        _text(logprobs=[{"top_logprobs": [{"logprob": BIG}, {"logprob": -1.0}]}]),
    ],
    ids=["direct", "nested"],
)
def test_a_huge_int_logprob_abstains(response: dict[str, Any]) -> None:
    reading = EntropySignal().measure(response)
    assert (reading.value, reading.trip) == (None, False)


def test_control_finite_logprobs_still_measure() -> None:
    reading = EntropySignal().measure({"first_token_logprobs": [-0.1, -2.5]})
    assert reading.value is not None
    assert reading.value > 0


class _Judge:
    def __init__(self, score: Any) -> None:
        self._score = score

    def score(self, prompt: str, response_text: str, *, rubric: str) -> Any:
        return types.SimpleNamespace(score=self._score)


def test_a_huge_int_judge_score_abstains() -> None:
    reading = JudgeConfidenceSignal(judge=_Judge(BIG), rubric="r").measure(_text())
    assert (reading.value, reading.trip) == (None, False)


def test_control_an_int_judge_score_still_measures() -> None:
    reading = JudgeConfidenceSignal(judge=_Judge(0), rubric="r").measure(_text())
    assert (reading.value, reading.trip) == (0.0, True)
