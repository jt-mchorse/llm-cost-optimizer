"""No two points in one sweep chart carry the same annotation (#227).

`scripts/tune_threshold.py` builds its sweep as
``sorted(set(float(t) for t in args.thresholds.split(",")))``, so the thresholds
are **guaranteed distinct**. The chart then annotated them at a fixed two
places, publishing a figure that disagreed with the guarantee the script itself
enforces::

    thresholds: [0.85, 0.851, 1.25, 1.253]
    labels:     ['t=0.85', 't=0.85', 't=1.25', 't=1.25']

Four points, two labels — on a chart whose only purpose is to let an operator
pick a threshold off the quality/cost frontier. The JSON artifact was never
wrong: `payload["rows"]` carries the thresholds at full precision. Same split
`embedding-model-shootout#149` found between a correct aggregate and a collapsed
table.

**The rule is set-wide, and these arms are built to reject the pairwise one.**
Every sibling fix in this class (`prompt-regression-suite#175`,
`llm-eval-harness#252`, `ai-app-integration-tests#125`,
`rag-production-kit#225`, `agent-orchestration-platform#149`,
`vector-search-at-scale#150`) renders two numbers in one sentence, so "widen
while the two render identically" is right there. Here a label is wrong when it
collides with *any other label in the chart*, and the colliding pair need not be
adjacent in the sorted sweep —
`test_a_collision_between_non_adjacent_thresholds_is_caught` is the arm a
pairwise rule fails.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.tune_threshold import _build_parser, _distinct_labels, main


def _labels_for(raw: str) -> list[str]:
    """Render labels for the sweep `--thresholds raw` would actually produce.

    Goes through the same `sorted(set(float(...)))` the script uses, so these
    arms cannot pass on a threshold list the script would have deduplicated or
    reordered out from under them.
    """
    values = sorted({float(t) for t in raw.split(",") if t.strip()})
    return _distinct_labels(values)


def test_the_shipped_default_sweep_is_byte_identical() -> None:
    """GREEN against the unfixed tree, deliberately.

    This is the arm that rejects a fix which widened the labels unconditionally,
    or switched to a shortest-round-trip rendering: `repr` closes the class just
    as well and would republish every one of these as `t=0.0`, `t=0.5`, churning
    the chart everyone actually looks at to fix one almost nobody generates.

    The default sweep is read out of the shipped parser rather than retyped, so
    a change to it fails here instead of leaving a stale literal that quietly
    stops testing the default.
    """
    parser = _build_parser()
    default_sweep = parser.get_default("thresholds")
    assert default_sweep == "0.0,0.5,1.0,1.2,1.4,1.6,1.8,2.0", default_sweep
    assert _labels_for(default_sweep) == [
        "t=0.00",
        "t=0.50",
        "t=1.00",
        "t=1.20",
        "t=1.40",
        "t=1.60",
        "t=1.80",
        "t=2.00",
    ]


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("0.85,0.851", id="adjacent-3dp"),
        pytest.param("0.85,0.851,1.25,1.253", id="two-colliding-pairs"),
        pytest.param("0.1234,0.1235", id="4dp"),
        pytest.param("0.0,0.001,0.002", id="near-zero"),
        pytest.param("1.9999,2.0", id="rounds-up-into-its-neighbour"),
    ],
)
def test_no_two_labels_collide(raw: str) -> None:
    labels = _labels_for(raw)
    assert len(set(labels)) == len(labels), labels


def test_the_rule_does_not_depend_on_the_input_being_sorted() -> None:
    """The set-wide rule vs the adjacent-pairs rule, on the input that separates them.

    Honest accounting, because my first draft of this arm claimed more than it
    could show. For **sorted** values the two rules are *equivalent*: rendering
    is monotonic, so if every adjacent rendered pair differs then every pair
    does. I built the adjacent-pairs neighbour and it passed every other arm in
    this module — 0 red.

    They diverge only on unsorted input, where an adjacent scan can be satisfied
    while two non-neighbours collide:

        [0.85, 1.0, 0.8501] at 2 places -> ['t=0.85', 't=1.00', 't=0.85']
        adjacent pairs all differ; the first and third do not.

    `main` sorts the sweep (`sorted(set(...))`), so this is a robustness
    property rather than a live defect — and that is exactly why it is worth
    pinning. `_distinct_labels` is a module-level function taking a list; it
    cannot see the ordering invariant its one caller happens to maintain, and
    a rule that silently depends on a caller's invariant is one refactor away
    from being wrong.
    """
    labels = _distinct_labels([0.85, 1.0, 0.8501])
    assert len(set(labels)) == 3, labels


def test_a_collision_needing_four_places_is_resolved() -> None:
    """A sweep where two places are not enough and three are not either."""
    labels = _distinct_labels([0.85, 0.8501, 1.0, 2.0])
    assert len(set(labels)) == 4, labels
    # The width is the one the hardest pair needed, shared by every label, so
    # the annotations stay readable as a column.
    assert all(len(lbl.split(".")[1]) == 4 for lbl in labels), labels


def test_every_label_shares_one_width() -> None:
    """A set-wide width, not a per-value one.

    Widening only the labels that collide would produce a chart mixing `t=0.850`
    with `t=1.25`, which reads as two different quantities. This is the
    same-precision half of the sibling rule, applied to a set rather than a
    pair.
    """
    labels = _distinct_labels([0.85, 0.851, 1.25, 1.253])
    widths = {len(lbl.split(".")[1]) for lbl in labels}
    assert widths == {3}, labels


def test_values_no_fixed_width_separates_fall_back_to_repr() -> None:
    """`set()` guarantees the thresholds are distinct *as doubles*, which is a
    weaker promise than "separable at some practical number of decimals".
    """
    labels = _distinct_labels([1e-300, 2e-300])
    assert labels == ["t=1e-300", "t=2e-300"]
    assert len(set(labels)) == 2


def test_degenerate_sweeps() -> None:
    assert _distinct_labels([]) == []
    assert _distinct_labels([0.5]) == ["t=0.50"]


def test_the_json_artifact_keeps_full_precision(tmp_path: Path) -> None:
    """The data was never the defect; pin that the fix did not become one.

    A fix that rounded the *stored* thresholds to match the chart would make the
    artifact less precise to make the labels consistent — the standing
    anti-pattern this portfolio has now declined in six repos.
    """
    out = tmp_path / "sweep"
    rc = main(["--dry", "--thresholds", "0.85,0.851,1.25,1.253", "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    thresholds = [row["threshold"] for row in payload["rows"]]
    assert thresholds == [0.85, 0.851, 1.25, 1.253], thresholds


def test_the_chart_is_the_only_labelled_surface_in_this_repo() -> None:
    """Pins the claim the issue makes, so a second chart cannot ship unruled.

    #227 states that `tune_threshold.py` holds the only annotation site in
    `scripts/` and `cost_optimizer/`. That was a grep at a point in time; this
    makes it a check. A new `labels =` or `.annotate(` in any *other* module
    fails here and has to either adopt `_distinct_labels` or say why it does
    not need to.

    Deliberately pinned to the **file**, not to line numbers. A line-numbered
    exemption goes stale the first time anything above it moves, and then reads
    as a guarantee about code that is no longer there.
    """
    root = Path(__file__).resolve().parents[1]
    files: set[str] = set()
    sources = [*(root / "scripts").rglob("*.py"), *(root / "cost_optimizer").rglob("*.py")]
    for path in sorted(sources):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if re.search(r"\blabels\b\s*=|\.annotate\(", stripped):
                files.add(str(path.relative_to(root)))
    assert files == {"scripts/tune_threshold.py"}, sorted(files)


# ----------------------------------------------------------------------
# The drawn thing, not the computed one
# ----------------------------------------------------------------------


class _RecordingAx:
    """Enough of a matplotlib `Axes` for `_try_save_plot`, recording annotations."""

    def __init__(self) -> None:
        self.annotations: list[str] = []

    def plot(self, *args: object, **kwargs: object) -> None: ...
    def set_xlabel(self, *args: object, **kwargs: object) -> None: ...
    def set_ylabel(self, *args: object, **kwargs: object) -> None: ...
    def set_title(self, *args: object, **kwargs: object) -> None: ...
    def grid(self, *args: object, **kwargs: object) -> None: ...

    def annotate(self, label: str, *args: object, **kwargs: object) -> None:
        self.annotations.append(label)


class _RecordingFig:
    def savefig(self, *args: object, **kwargs: object) -> None: ...


def _annotations_for(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str) -> list[str]:
    """Drive `_try_save_plot` end to end and return the labels it actually drew.

    A **fake matplotlib injected into `sys.modules`**, not the real one:
    matplotlib is an optional extra and is absent from the `dev` extra and from
    CI, so an arm gated on it would skip everywhere that matters — and a
    permanently skipped arm is not an arm. This also keeps the check hermetic.
    """
    import sys
    import types

    ax = _RecordingAx()
    pyplot = types.ModuleType("matplotlib.pyplot")
    pyplot.subplots = lambda **kwargs: (_RecordingFig(), ax)  # type: ignore[attr-defined]
    pyplot.close = lambda fig: None  # type: ignore[attr-defined]
    matplotlib = types.ModuleType("matplotlib")
    matplotlib.use = lambda backend: None  # type: ignore[attr-defined]
    matplotlib.pyplot = pyplot  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "matplotlib", matplotlib)
    monkeypatch.setitem(sys.modules, "matplotlib.pyplot", pyplot)

    from scripts.tune_threshold import _build_sample_items, _try_save_plot, sweep

    thresholds = sorted({float(t) for t in raw.split(",") if t.strip()})
    rows = sweep(_build_sample_items(), thresholds, cheap_dollars=0.0008, strong_dollars=0.012)
    assert _try_save_plot(rows, tmp_path / "sweep.png") is True
    return ax.annotations


def test_the_chart_actually_drawn_has_no_duplicate_annotations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reads what the chart was given, not what the helper returns.

    Every other arm in this module calls `_distinct_labels` directly, and a
    plain revert of the **call site** leaves all of them green — measured, 0
    red. `vector-search-at-scale#148` recorded exactly this: formatter-level
    arms stayed green against a call-site revert because the formatter was
    already correct. This is the arm that fails when `_try_save_plot` stops
    using the helper.
    """
    drawn = _annotations_for(monkeypatch, tmp_path, "0.85,0.851,1.25,1.253")
    assert len(drawn) == 4, drawn
    assert len(set(drawn)) == 4, drawn
    assert drawn == ["t=0.850", "t=0.851", "t=1.250", "t=1.253"], drawn


def test_the_chart_actually_drawn_is_unchanged_for_the_default_sweep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The byte-identity control, also at the call site.

    GREEN against the unfixed tree on purpose: it is what rejects a fix that
    widened every chart rather than only the ones that needed it.
    """
    drawn = _annotations_for(monkeypatch, tmp_path, _build_parser().get_default("thresholds"))
    assert drawn == [
        "t=0.00",
        "t=0.50",
        "t=1.00",
        "t=1.20",
        "t=1.40",
        "t=1.60",
        "t=1.80",
        "t=2.00",
    ], drawn
