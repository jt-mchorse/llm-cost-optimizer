"""The bench workload honours D-012's 60/30/10 mix at every `--n` (#262).

The rows were split by flooring the first two classes and giving every
remainder to `hard`: `--n 3` was 1/0/2 (67% hard) and `--n 1` was 0/0/1 (100%
hard). Hard rows are the ones the router escalates, so the mix rather than the
strategy moved the headline (a -337% router "saving" at `--n 3`).
"""

from __future__ import annotations

import pytest

from scripts.bench_savings import _MIX_PERCENT, _build_workload, _mix_counts


@pytest.mark.parametrize("n", [1, 2, 3, 7, 10, 19, 33, 101, 500])
def test_each_class_is_within_one_row_of_its_share_and_the_total_is_n(n: int) -> None:
    counts = _mix_counts(n)
    assert sum(counts) == n
    for count, pct in zip(counts, _MIX_PERCENT, strict=True):
        assert abs(count - n * pct / 100) < 1, (n, counts)


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, (1, 0, 0)), (3, (2, 1, 0)), (19, (11, 6, 2)), (500, (300, 150, 50))],
)
def test_the_measured_cases(n: int, expected: tuple[int, int, int]) -> None:
    assert _mix_counts(n) == expected


def test_the_workload_rows_follow_the_counts() -> None:
    rows = _build_workload(19)
    kinds = [r.class_ for r in rows]
    assert (kinds.count("redundant"), kinds.count("easy"), kinds.count("hard")) == (11, 6, 2)
