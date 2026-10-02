"""An `--out` must name a file stem no other stem's files can reach (#239).

D-023 (#231) made one stem's three names agree with each other. Two `--out`
values still wrote somewhere other than where the operator pointed, both at
exit 0 and both over another run's files. Measured at `1dcb7ae`::

    --out /tmp/c/savings          --n 50   savings_workload.json: rows x 50
    --out /tmp/c/savings_workload.json --n 5
        -> savings_workload.json is now a 5-row *results* file, no 'rows' key

    --out /tmp/d/run1/            (run1 is a directory)
        -> /tmp/d/run1.json, run1.md, run1_workload.json; run1/ stays empty

The first is #176's harm through a collision between two stems: `S` +
`_workload.json` is `S_workload` + `.json`. The second is pathlib dropping the
trailing separator, which #174's crash-only guard never looked at.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._io import _colliding_stem_endings, resolve_out_stem  # noqa: E402
from scripts.bench_savings import main as bench_main  # noqa: E402
from scripts.tune_threshold import main as tune_main  # noqa: E402

# ----------------------------------------------------------------------
# The two repros, through `main`
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "second", ["savings_workload.json", "savings_workload", "savings_workload.md"]
)
def test_a_second_run_cannot_overwrite_the_first_runs_workload_record(
    tmp_path: Path, second: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bench_main(["--dry", "--n", "40", "--out", str(tmp_path / "savings")]) == 0
    record = tmp_path / "savings_workload.json"
    before = record.read_bytes()
    _ = capsys.readouterr()
    assert bench_main(["--dry", "--n", "12", "--out", str(tmp_path / second)]) == 2
    captured = capsys.readouterr()
    assert "ends in '_workload'" in captured.err
    # Refused before the work: nothing printed, nothing written.
    assert captured.out == ""
    assert record.read_bytes() == before
    assert len(json.loads(before)["rows"]) == 40
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "savings.json",
        "savings.md",
        "savings_workload.json",
    ]


def test_the_reverse_order_is_refused_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`savings_workload` first, then `savings`: the refusal is on the stem
    that ends in the overlap, so whichever runs it is the one refused, and the
    one that does not end in it is always allowed."""
    assert bench_main(["--dry", "--n", "12", "--out", str(tmp_path / "savings_workload")]) == 2
    assert bench_main(["--dry", "--n", "12", "--out", str(tmp_path / "savings")]) == 0
    _ = capsys.readouterr()


@pytest.mark.parametrize("suffix", ["/", "/.", "//", "/./"])
@pytest.mark.parametrize("script", ["bench_savings", "tune_threshold"])
def test_a_directory_is_refused_rather_than_written_beside(
    tmp_path: Path, suffix: str, script: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run1 = tmp_path / "run1"
    run1.mkdir()
    main = bench_main if script == "bench_savings" else tune_main
    argv = ["--dry", "--n", "12"] if script == "bench_savings" else []
    assert main([*argv, "--out", str(run1) + suffix]) == 2
    captured = capsys.readouterr()
    assert "not a directory" in captured.err
    assert captured.out == ""
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run1"]
    assert list(run1.iterdir()) == []


def test_the_suggested_stem_is_inside_the_directory() -> None:
    with pytest.raises(ValueError, match="not a directory") as excinfo:
        resolve_out_stem("out/run1/", artifact_suffixes=(".json",))
    assert "`--out out/run1/NAME`" in str(excinfo.value)


# ----------------------------------------------------------------------
# The rule is derived from the tails, not written for `_workload`
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tails", "endings"),
    [
        ((".json", ".md", "_workload.json"), ["_workload"]),
        ((".json", ".png"), []),
        ((".json", ".md"), []),
        ((".json", "_a.json", "_b.json"), ["_a", "_b"]),
        ((".json", ".min.json", "_x.min.json"), [".min", "_x", "_x.min"]),
        ((".json",), []),
    ],
)
def test_colliding_endings_are_every_difference_of_two_nested_tails(
    tails: tuple[str, ...], endings: list[str]
) -> None:
    assert _colliding_stem_endings(tails) == endings


def test_a_fourth_tail_inherits_the_rule_without_new_code() -> None:
    with pytest.raises(ValueError, match="ends in '_trace'"):
        resolve_out_stem("x/run_trace", artifact_suffixes=(".json", "_trace.json"))
    assert resolve_out_stem("x/run", artifact_suffixes=(".json", "_trace.json")) == Path("x/run")


def test_a_tail_that_is_not_a_path_suffix_is_never_stripped() -> None:
    """D-023's stripping is unchanged by passing `_workload.json`: it is never a
    `Path.suffix`, so it cannot be stripped -- and a stem that *ends* with it is
    refused by the collision rule instead (after `.json` is stripped)."""
    tails = (".json", ".md", "_workload.json")
    assert resolve_out_stem("d/savings.json", artifact_suffixes=tails) == Path("d/savings")
    assert resolve_out_stem("d/savings.small", artifact_suffixes=tails) == Path("d/savings.small")
    with pytest.raises(ValueError, match="ends in '_workload'"):
        resolve_out_stem("d/savings_workload.json", artifact_suffixes=tails)


@pytest.mark.parametrize(
    "out",
    ["savings", "my_workloads", "workload", "savings_workload2", "..", "x/..", "a/b/c"],
)
def test_ordinary_stems_still_resolve(out: str) -> None:
    """Neighbours of the two refusals that must stay legal: a stem merely
    *containing* `workload`, and #174's deliberately-allowed `..`."""
    resolve_out_stem(out, artifact_suffixes=(".json", ".md", "_workload.json"))
