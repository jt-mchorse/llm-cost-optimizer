""" "Quality maintained?" decides a drop on the decimal values it shows (#257).

The table computed `delta = quality - baseline` in floats against a tolerance
of exactly 0.01. Measured on main: 47 of 51 two-decimal drops of exactly 0.01
(baselines 0.50-1.00) were `regression` and 4 were `yes`, all displayed as
`-0.01`; the committed artifact's own baseline 0.886 -> 0.876 was among the
47, although the caption says 0.01 is tolerated. A `None` quality (an empty
population, D-019) raised TypeError.
"""

from __future__ import annotations

import importlib.util
import json
import math
from fractions import Fraction
from pathlib import Path

import pytest

if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
    pytest.skip("dashboard extras not installed", allow_module_level=True)

from dashboard.app import _quality_rows  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent
_COMMITTED = _REPO_ROOT / "docs" / "savings.json"


def _payload(baseline: float | None, *others: float | None) -> dict:
    return {
        "strategies": [
            {"strategy": "baseline", "mean_quality": baseline},
            *({"strategy": f"s{i}", "mean_quality": q} for i, q in enumerate(others)),
        ]
    }


def test_every_drop_of_exactly_the_tolerance_is_tolerated() -> None:
    pairs = [(b / 100, (b - 1) / 100) for b in range(50, 101)]
    # The float rule really does split these, or the arm is vacuous.
    assert {c - b >= -0.01 for b, c in pairs} == {True, False}
    for b, c in pairs:
        (_, row) = _quality_rows(_payload(b, c))
        assert row["verdict"] == "yes", (b, c, row)
        assert Fraction(row["delta_vs_baseline"]) == Fraction(-1, 100)


def test_the_committed_baseline_tolerates_its_own_tolerance() -> None:
    baseline = json.loads(_COMMITTED.read_text())["strategies"][0]["mean_quality"]
    (_, row) = _quality_rows(_payload(baseline, round(baseline - 0.01, 3)))
    assert row["verdict"] == "yes"


@pytest.mark.parametrize(
    ("baseline", "quality", "verdict"),
    [
        (0.9, 0.88996, "regression"),
        (0.9, 0.89004, "yes"),
        (0.8, math.nextafter(0.79, 0.0), "regression"),  # one ULP past
        (0.8, 0.85, "yes"),
    ],
)
def test_the_published_delta_reads_back_as_its_verdict(
    baseline: float, quality: float, verdict: str
) -> None:
    (_, row) = _quality_rows(_payload(baseline, quality))
    assert row["verdict"] == verdict
    tolerated = Fraction(row["delta_vs_baseline"]) >= Fraction(-1, 100)
    assert tolerated is (verdict == "yes"), row


def test_the_two_issue_rows_do_not_print_one_number() -> None:
    rows = _quality_rows(_payload(0.9, 0.88996, 0.89004))
    assert rows[1]["delta_vs_baseline"] != rows[2]["delta_vs_baseline"]


def test_a_missing_quality_is_shown_absent_not_a_crash() -> None:
    rows = _quality_rows(_payload(0.8, None, 0.7))
    assert (rows[1]["delta_vs_baseline"], rows[1]["verdict"]) == ("—", "n/a")
    assert rows[2]["verdict"] == "regression"
    assert {r["verdict"] for r in _quality_rows(_payload(None, 0.7))} == {"n/a"}


def test_the_dashboard_renders_the_table_from_an_edited_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from streamlit.testing.v1 import AppTest

    payload = json.loads(_COMMITTED.read_text())
    baseline = payload["strategies"][0]["mean_quality"]
    tolerated, regressed = payload["strategies"][1], payload["strategies"][2]
    tolerated["mean_quality"] = round(baseline - 0.01, 3)  # exactly the tolerance
    regressed["mean_quality"] = round(baseline - 0.5, 3)
    artifact = tmp_path / "edited.json"
    artifact.write_text(json.dumps(payload))
    monkeypatch.setattr(sys, "argv", ["app.py", "--json", str(artifact)])
    at = AppTest.from_file(str(_REPO_ROOT / "dashboard" / "app.py"), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    # The page really read the edited file, not the committed default.
    assert any(str(artifact) in c.value for c in at.caption)
    quality = next(df.value for df in at.dataframe if "verdict" in df.value.columns)
    assert quality.loc[tolerated["strategy"], "verdict"] == "yes"
    assert quality.loc[regressed["strategy"], "verdict"] == "regression"
