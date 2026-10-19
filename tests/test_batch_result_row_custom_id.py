"""A result entry with no readable `custom_id` is an error row, not a silent success (#333).

`custom_id` is the only thing that matches a result row back to its request,
and `submit` refuses an empty or non-pattern one on both backends (#329). The
old `getattr(entry, "custom_id", "")` let an entry with no id, or a non-`str`
one, through as a successful answer. Measured on the unfixed code:

    absent  -> custom_id=''       response_text='hello'  10/5  error=None
    None    -> custom_id=None     response_text='hello'  10/5  error=None
    dict    -> custom_id={'a': 1} response_text='hello'  10/5  error=None

Two absent-id rows collided on `''`, and a dict id made
`compare_realtime_vs_batch` raise "unhashable type". D-017's answer for a row we
cannot read is an error row, not a raise, so one bad entry does not cost the
caller the rest of the batch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from cost_optimizer.batch import (
    AnthropicBatchBackend,
    BatchCostQuote,
    _from_sdk_result_row,
    compare_realtime_vs_batch,
)

_ABSENT = object()


def _succeeded(**id_kw: Any) -> Any:
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="hello")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )
    return SimpleNamespace(result=SimpleNamespace(type="succeeded", message=message), **id_kw)


def _errored(**id_kw: Any) -> Any:
    return SimpleNamespace(result=SimpleNamespace(type="errored", error="boom"), **id_kw)


def _missing_result(**id_kw: Any) -> Any:
    return SimpleNamespace(result=None, **id_kw)


def _id_kw(value: Any) -> dict[str, Any]:
    return {} if value is _ABSENT else {"custom_id": value}


_UNREADABLE_IDS = [
    pytest.param(_ABSENT, id="absent"),
    pytest.param(None, id="None"),
    pytest.param(7, id="int"),
    pytest.param(True, id="bool"),
    pytest.param({"a": 1}, id="dict"),
    pytest.param(b"r-1", id="bytes"),
    pytest.param("", id="empty-str"),
]


@pytest.mark.parametrize("bad_id", _UNREADABLE_IDS)
@pytest.mark.parametrize("make", [_succeeded, _errored, _missing_result])
def test_an_unreadable_custom_id_is_an_error_row(make: Any, bad_id: Any) -> None:
    row = _from_sdk_result_row(make(**_id_kw(bad_id)))
    assert row.error is not None
    assert "custom_id" in row.error
    assert row.custom_id == ""
    assert isinstance(row.custom_id, str)
    assert row.response_text is None
    assert (row.prompt_tokens, row.completion_tokens) == (0, 0)


def test_two_absent_id_answers_no_longer_collide_as_successes() -> None:
    rows = [_from_sdk_result_row(_succeeded()), _from_sdk_result_row(_succeeded())]
    assert [r.error for r in rows] == ["result entry carried no custom_id"] * 2


def test_compare_skips_a_dict_id_row_instead_of_raising_unhashable() -> None:
    rows = [
        _from_sdk_result_row(_succeeded(custom_id={"a": 1})),
        _from_sdk_result_row(_succeeded(custom_id="r-1")),
    ]
    out = compare_realtime_vs_batch(rows, {"m": BatchCostQuote("m", 3.0, 15.0)})
    assert out.n_rows == 1


def test_results_reports_the_bad_entry_and_keeps_the_rest_of_the_batch() -> None:
    resp = SimpleNamespace(
        id="msgbatch_x",
        processing_status="ended",
        cancel_initiated_at=None,
        request_counts=SimpleNamespace(processing=0, succeeded=2, errored=0, canceled=0, expired=0),
        created_at="2026-01-01T00:00:00Z",
    )
    entries = [_succeeded(custom_id="r-1"), _succeeded()]
    batches = SimpleNamespace(retrieve=lambda job_id: resp, results=lambda job_id: entries)
    backend = AnthropicBatchBackend(SimpleNamespace(messages=SimpleNamespace(batches=batches)))
    good, bad = backend.results("msgbatch_x")
    assert (good.custom_id, good.response_text, good.error) == ("r-1", "hello", None)
    assert bad.error == "result entry carried no custom_id"


# --- controls: a valid id moves nothing, on every result type --------------


def test_control_valid_id_on_a_succeeded_row_is_unchanged() -> None:
    row = _from_sdk_result_row(_succeeded(custom_id="r-1"))
    assert (row.custom_id, row.response_text, row.prompt_tokens, row.completion_tokens) == (
        "r-1",
        "hello",
        10,
        5,
    )
    assert row.error is None


def test_control_valid_id_keeps_its_own_failure_reason() -> None:
    assert _from_sdk_result_row(_errored(custom_id="r-2")).error == "boom"
    missing = _from_sdk_result_row(_missing_result(custom_id="r-3"))
    assert (missing.custom_id, missing.error) == ("r-3", "missing result")
