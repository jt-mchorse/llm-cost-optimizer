"""`--thresholds` was guarded for parse failure and not for value (#186).

`scripts/tune_threshold.py` validates `--cheap-dollars`, `--strong-dollars`
and `--out` against their value domains and exits 2 on each. `--thresholds` —
the flag the script is named after — was checked only for tokens `float()`
*refuses*. The comment introducing the dollars guard, four lines above, states
the rule it was missing:

    argparse only enforces `float`, which happily parses `nan`/`inf`/negative

Measured on `main` @ 46dcccd, exit codes captured before any pipe:

    --thresholds 'abc'      -> exit 2, clean ::error:: line        (correct)
    --thresholds 'nan,0.5'  -> exit 1, raw ValueError traceback
    --thresholds 'inf'      -> exit 1, raw ValueError traceback
    --thresholds '-1.0,0.5' -> exit 1, raw ValueError traceback
    --thresholds '1e400'    -> exit 1, raw ValueError traceback
    --thresholds ''         -> exit 0, wrote {"rows": []}
    --thresholds ','        -> exit 0, wrote {"rows": []}

Every test below asserts the **exit code and the absence of the artifact**,
not just that something was raised. For the empty-list case the artifact is
the whole defect: the pre-fix run replaced the committed 8-row
`docs/threshold_demo.json` with a zero-row one and printed
`sweep wrote docs/threshold_demo.json` on the way out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# One spelling, repo-root-relative (#201) — see the note in
# tests/test_tune_threshold.py.
from scripts.tune_threshold import main

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _assert_nothing_written(out_stem: Path) -> None:
    """Both write seams stayed shut.

    The guards are placed ahead of `sweep()` for this reason: a rejected input
    must not leave a half-produced pair behind. The `.png` matters as much as
    the `.json` — `_try_save_plot` shares the same `--out` stem and, on an
    empty row list, `ax.plot([], [])` succeeds and writes a blank chart.
    """
    assert not out_stem.with_suffix(".json").exists(), "a rejected run wrote a JSON artifact"
    assert not out_stem.with_suffix(".png").exists(), "a rejected run wrote a PNG artifact"


# ----------------------------------------------------------------------
# Value domain: tokens float() ACCEPTS and the sweep cannot use
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("thresholds", "offender"),
    [
        ("nan", "nan"),
        ("nan,0.5", "nan"),
        ("inf", "inf"),
        ("-inf", "-inf"),
        ("-1.0,0.5", "-1.0"),
        ("0.5,-0.001", "-0.001"),
    ],
)
def test_non_finite_or_negative_threshold_exits_two(
    tmp_path: Path, capsys, thresholds: str, offender: str
) -> None:
    # Pre-fix each of these reached `EntropySignal.__post_init__` from inside
    # `sweep()` and surfaced as a raw multi-frame traceback at exit 1 — a
    # script-level *usage* error escaping as a library-level exception at the
    # wrong code, which is precisely the contract the `abc` guard directly
    # above it exists to honor.
    out_stem = tmp_path / "should-not-be-written"
    rc = main(["--dry", "--out", str(out_stem), f"--thresholds={thresholds}"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "--thresholds values must be finite numbers >= 0" in captured.err
    # The message names the offending value, not just the rule — with a
    # comma-separated list the operator otherwise has to guess which token.
    assert offender in captured.err
    _assert_nothing_written(out_stem)


def test_1e400_is_rejected_although_it_looks_like_a_finite_literal(tmp_path: Path, capsys) -> None:
    # The case an operator cannot see coming: `1e400` is a perfectly ordinary
    # decimal literal on the command line, and `float("1e400")` is `inf`. This
    # is the cross-repo sibling of the `int()`/`float()` overflow class already
    # documented in llm-eval-harness#190 and llm-cost-optimizer#166.
    out_stem = tmp_path / "should-not-be-written"
    rc = main(["--dry", "--out", str(out_stem), "--thresholds=1e400"])
    captured = capsys.readouterr()
    assert rc == 2
    # It is reported as the value the sweep would actually have seen.
    assert "got inf" in captured.err
    assert "1e400" in captured.err, "the raw operator input is echoed back"
    _assert_nothing_written(out_stem)


# ----------------------------------------------------------------------
# An empty sweep is not a result
# ----------------------------------------------------------------------


@pytest.mark.parametrize("thresholds", ["", ",", ",,", "  ", " , "])
def test_empty_threshold_list_exits_two_instead_of_writing_zero_rows(
    tmp_path: Path, capsys, thresholds: str
) -> None:
    # Pre-fix: exit 0, `sweep wrote ...` on stdout, and a JSON artifact of
    # `{"cheap_dollars_per_request": ..., "mode": "dry", "rows": [],
    #   "strong_dollars_per_request": ...}`.
    #
    # `--thresholds "$THRESHOLDS"` with an unset variable is the ordinary way
    # to reach this from CI or a wrapper script, and the success exit code is
    # exactly what lets it through review.
    out_stem = tmp_path / "should-not-be-written"
    rc = main(["--dry", "--out", str(out_stem), f"--thresholds={thresholds}"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "--thresholds must name at least one threshold" in captured.err
    _assert_nothing_written(out_stem)


def test_the_committed_artifact_is_what_an_empty_sweep_would_have_destroyed() -> None:
    # Anchors the severity claim rather than asserting it in prose. `--out`
    # defaults to `docs/threshold_demo`, which is the stem the README's
    # documented command uses, so the pre-fix empty run overwrote *this* file.
    committed = _REPO_ROOT / "docs" / "threshold_demo.json"
    payload = json.loads(committed.read_text(encoding="utf-8"))
    assert len(payload["rows"]) == 8
    assert [r["threshold"] for r in payload["rows"]] == [0.0, 0.5, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0]


# ----------------------------------------------------------------------
# What the guards must NOT change
# ----------------------------------------------------------------------


def test_zero_is_still_a_valid_threshold(tmp_path: Path) -> None:
    # `0.0` is the first row of the committed default sweep. The guard rejects
    # values `< 0`, not `<= 0`; getting that boundary wrong would silently drop
    # the escalate-everything end of the curve.
    out_stem = tmp_path / "sweep"
    rc = main(["--dry", "--out", str(out_stem), "--thresholds=0.0"])
    assert rc == 0
    payload = json.loads(out_stem.with_suffix(".json").read_text(encoding="utf-8"))
    assert [r["threshold"] for r in payload["rows"]] == [0.0]


def test_empty_tokens_between_real_ones_are_still_skipped(tmp_path: Path) -> None:
    # `if t.strip()` already tolerated a trailing or doubled comma, and that
    # behaviour is deliberate. The new guard fires only when *nothing* survives
    # the filter, so a sloppy-but-meaningful list keeps working.
    out_stem = tmp_path / "sweep"
    rc = main(["--dry", "--out", str(out_stem), "--thresholds=0.0,,0.5,"])
    assert rc == 0
    payload = json.loads(out_stem.with_suffix(".json").read_text(encoding="utf-8"))
    assert [r["threshold"] for r in payload["rows"]] == [0.0, 0.5]


def test_the_parse_guard_is_not_replaced(tmp_path: Path, capsys) -> None:
    # The new checks sit *after* the existing `except ValueError`, so a token
    # `float()` refuses still gets its own, more specific message.
    out_stem = tmp_path / "should-not-be-written"
    rc = main(["--dry", "--out", str(out_stem), "--thresholds=0.5,notanumber"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "--thresholds must be comma-separated numbers" in captured.err
    assert "must be finite numbers" not in captured.err
    _assert_nothing_written(out_stem)


def test_an_ordinary_multi_value_sweep_is_unaffected(tmp_path: Path) -> None:
    out_stem = tmp_path / "sweep"
    rc = main(["--dry", "--out", str(out_stem), "--thresholds=0.5,1.0,2.0"])
    assert rc == 0
    payload = json.loads(out_stem.with_suffix(".json").read_text(encoding="utf-8"))
    assert [r["threshold"] for r in payload["rows"]] == [0.5, 1.0, 2.0]
    assert all(r["n"] == 5 for r in payload["rows"])
