"""Every artifact a script writes shares one stem, dots included (#231, D-023).

`resolve_out_stem` (#174) returned a suffixed stem untouched so `with_suffix`
could replace the suffix -- that is what makes `--out docs/savings.json` mean
`docs/savings`. But `with_suffix` replaces *whatever* follows the last dot, and
#176's workload sidecar was built from `.name`, which keeps it. Measured at
`875f242`::

    --out /tmp/d/savings       --n 500
    --out /tmp/d/savings.small --n 25
    -> savings.json / savings.md       the 25-row run (overwrote the 500-row one)
       savings.small_workload.json     the 25-row workload
       savings_workload.json           the 500-row workload, beside a 25-row table

which is #176's harm verbatim, back through the two siblings.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._io import artifact_path, resolve_out_stem  # noqa: E402
from scripts.bench_savings import ARTIFACT_SUFFIXES, WORKLOAD_SUFFIX  # noqa: E402
from scripts.bench_savings import main as bench_main  # noqa: E402
from scripts.tune_threshold import main as tune_main  # noqa: E402

# (--out, the stem every artifact must share)
_STEMS = [
    ("savings", "savings"),
    ("savings.json", "savings"),  # #174's intent: a suffix the script writes is stripped
    ("savings.md", "savings"),
    ("savings.small", "savings.small"),  # any other dot belongs to the stem
    ("savings.2026-09-30", "savings.2026-09-30"),
    ("savings.v1.2", "savings.v1.2"),
    ("savings.small.json", "savings.small"),
    ("savings.png", "savings.png"),  # tune_threshold's suffix is not bench_savings'
]


@pytest.mark.parametrize(("out", "stem"), _STEMS, ids=[o for o, _ in _STEMS])
def test_bench_savings_writes_all_three_names_from_one_stem(
    tmp_path: Path, out: str, stem: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bench_main(["--dry", "--n", "12", "--out", str(tmp_path / out)]) == 0
    _ = capsys.readouterr()
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [f"{stem}.json", f"{stem}.md", f"{stem}_workload.json"]
    )


def test_a_dotted_second_run_no_longer_overwrites_the_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The issue's repro, through `main`, asserting on the files' *contents*."""
    assert bench_main(["--dry", "--n", "40", "--out", str(tmp_path / "savings")]) == 0
    first = (tmp_path / "savings.json").read_bytes()
    assert bench_main(["--dry", "--n", "12", "--out", str(tmp_path / "savings.small")]) == 0
    _ = capsys.readouterr()
    assert (tmp_path / "savings.json").read_bytes() == first
    assert json.loads((tmp_path / "savings.json").read_text())["n_rows"] == 40
    assert json.loads((tmp_path / "savings.small.json").read_text())["n_rows"] == 12
    # Each table sits next to its own workload.
    assert len(json.loads((tmp_path / "savings_workload.json").read_text())["rows"]) == 40
    assert len(json.loads((tmp_path / "savings.small_workload.json").read_text())["rows"]) == 12


@pytest.mark.parametrize(
    ("out", "stem"),
    [("demo", "demo"), ("demo.json", "demo"), ("demo.png", "demo"), ("demo.v2", "demo.v2")],
)
def test_tune_threshold_derives_both_names_from_one_stem(
    tmp_path: Path,
    out: str,
    stem: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """matplotlib is in no extra (see `pyproject.toml`), so the `.png` is never
    written in CI. Spy on the plot seam instead: the path it is *handed* is the
    derivation under test, and it must not depend on whether a plotting library
    happens to be installed."""
    import scripts.tune_threshold as tune

    handed: list[Path] = []

    def spy(rows: object, path: Path) -> bool:
        handed.append(path)
        return False

    monkeypatch.setattr(tune, "_try_save_plot", spy)
    assert tune_main(["--out", str(tmp_path / out)]) == 0
    _ = capsys.readouterr()
    assert [p.name for p in tmp_path.iterdir()] == [f"{stem}.json"]
    assert handed == [tmp_path / f"{stem}.png"]


def test_the_documented_invocation_is_byte_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """README: `python scripts/bench_savings.py --dry --out docs/savings`."""
    assert bench_main(["--dry", "--out", str(tmp_path / "savings")]) == 0
    _ = capsys.readouterr()
    for name in ("savings.json", "savings.md", "savings_workload.json"):
        assert (tmp_path / name).read_bytes() == (_REPO_ROOT / "docs" / name).read_bytes(), name


def test_artifact_path_appends_and_with_suffix_would_not() -> None:
    stem = Path("docs/savings.small")
    assert artifact_path(stem, ".json") == Path("docs/savings.small.json")
    assert stem.with_suffix(".json") == Path("docs/savings.json")  # the defect
    # Where there is no suffix the two agree, which is why the canonical run
    # could not show the difference.
    plain = Path("docs/savings")
    assert artifact_path(plain, ".json") == plain.with_suffix(".json")


def test_resolve_out_stem_strips_only_the_callers_own_suffixes() -> None:
    assert resolve_out_stem("docs/savings.json", artifact_suffixes=ARTIFACT_SUFFIXES) == Path(
        "docs/savings"
    )
    assert resolve_out_stem("docs/savings.png", artifact_suffixes=ARTIFACT_SUFFIXES) == Path(
        "docs/savings.png"
    )
    assert resolve_out_stem("docs/savings.png", artifact_suffixes=(".json", ".png")) == Path(
        "docs/savings"
    )


def test_every_artifact_script_derives_names_through_the_helper() -> None:
    """No `.with_suffix(` left on an output stem in `scripts/`: one derivation."""
    offenders = []
    for path in sorted((_REPO_ROOT / "scripts").glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            # `with_suffix("")` is the strip in `resolve_out_stem` itself.
            if ".with_suffix(" in code and "stem" in code and '.with_suffix("")' not in code:
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, offenders
    assert WORKLOAD_SUFFIX == "_workload.json"
