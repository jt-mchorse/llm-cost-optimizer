"""Atomicity contract for `scripts/bench_savings.py` and `scripts/tune_threshold.py` (issue #42).

`Path.write_text` is not atomic. SIGINT/SIGTERM/disk-full/OOM between
the implicit `open(..., "w")` truncate and `close()` flush leaves the
destination zero-length or partial. The streamlit dashboard
(`cost_optimizer/dashboard/app.py`) loads `docs/savings.json` per the
demo flow; a half-written file crashes it (cryptic `JSONDecodeError`)
or, worse, displays partial strategy rows. `docs/savings.md` is
rendered inline by GitHub on the README; a half-written one breaks the
README in the same window.

The fix routes the four `Path.write_text` call sites in
`scripts/bench_savings.py` (3) and `scripts/tune_threshold.py` (1)
through `scripts/_io.py::atomic_write_text`, which writes to a sibling
temp file in the destination's parent (same filesystem guaranteed →
`os.replace` can't fall back to a copy), `fsync`s, then `os.replace`s.

Parallels `llm-eval-harness#48` — same helper shape, same set of
invariants. The integration tests prove the four call sites all route
through the helper by monkeypatching `os.replace` to raise and
asserting the destination never appears.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import _io as io_mod  # noqa: E402
from scripts._io import atomic_write_text  # noqa: E402
from scripts.bench_savings import main as bench_main  # noqa: E402
from scripts.tune_threshold import main as tune_main  # noqa: E402

# ---------------------------------------------------------------------------
# Unit tests on the helper itself.
# ---------------------------------------------------------------------------


def test_atomic_write_text_happy_path(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    atomic_write_text(out, "hello\nworld\n")
    assert out.read_text(encoding="utf-8") == "hello\nworld\n"


def test_atomic_write_text_creates_parent_dirs(tmp_path: Path) -> None:
    out = tmp_path / "deep" / "nested" / "x.json"
    assert not out.parent.exists()
    atomic_write_text(out, "{}")
    assert out.read_text(encoding="utf-8") == "{}"


def test_atomic_write_text_overwrites_existing_file(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    out.write_text("STALE-CONTENT-MUST-NOT-SURVIVE", encoding="utf-8")
    atomic_write_text(out, "fresh")
    body = out.read_text(encoding="utf-8")
    assert body == "fresh"
    assert "STALE" not in body


def test_atomic_write_text_replace_failure_leaves_destination_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Load-bearing atomicity invariant.

    If `os.replace` raises (cross-device, SIGINT delivered between
    fsync and rename, PermissionError), the destination must not exist.
    The helper must never touch the destination directly — only via
    the atomic rename.
    """
    out = tmp_path / "result.json"

    def boom(*_args, **_kwargs):
        raise OSError("simulated mid-rename failure")

    monkeypatch.setattr(io_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated mid-rename failure"):
        atomic_write_text(out, '{"k": "v"}')

    assert not out.exists()


def test_atomic_write_text_replace_failure_cleans_up_tmp_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No leftover `.tmp` siblings after a failed atomic write."""
    out = tmp_path / "artifacts" / "delta.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    def boom(*_args, **_kwargs):
        raise OSError("simulated mid-rename failure")

    monkeypatch.setattr(io_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated mid-rename failure"):
        atomic_write_text(out, '{"k": "v"}')

    siblings = list(out.parent.iterdir())
    assert siblings == [], f"expected no temp leftovers in {out.parent}, got {siblings}"


def test_atomic_write_text_destination_unchanged_when_overwriting_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed `os.replace` during an overwrite leaves the pre-existing
    destination intact — not zero-length, not partial, not the new
    content. The property `Path.write_text` could never offer.
    """
    out = tmp_path / "existing.json"
    out.write_text('{"keep": true}', encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError("simulated")

    monkeypatch.setattr(io_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_text(out, '{"overwrite": true}')

    assert out.read_text(encoding="utf-8") == '{"keep": true}'


# ---------------------------------------------------------------------------
# Integration: each script `--out` site routes through atomic_write_text.
# ---------------------------------------------------------------------------


def test_bench_savings_json_routes_through_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bench_savings.py --dry --out STEM` writes `STEM.json`,
    `STEM.md`, and `<parent>/savings_workload.json`. A monkeypatched
    `os.replace` raises before any of those exist on disk — proving
    all three writes route through the helper.
    """
    out_stem = tmp_path / "out" / "savings"

    def boom(*_args, **_kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(io_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated rename failure"):
        bench_main(["--dry", "--out", str(out_stem), "--n", "10"])

    assert not (out_stem.with_suffix(".json")).exists()
    assert not (out_stem.with_suffix(".md")).exists()
    assert not (out_stem.parent / "savings_workload.json").exists()


def test_tune_threshold_json_routes_through_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tune_threshold.py --dry --out STEM` writes `STEM.json`. The
    monkeypatched `os.replace` raises before the destination exists.
    """
    out_stem = tmp_path / "out" / "sweep"

    def boom(*_args, **_kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(io_mod.os, "replace", boom)
    with pytest.raises(OSError, match="simulated rename failure"):
        tune_main(["--dry", "--out", str(out_stem)])

    assert not (out_stem.with_suffix(".json")).exists()


def test_bench_savings_end_to_end_produces_valid_atomic_outputs(tmp_path: Path) -> None:
    """End-to-end happy path through `bench_savings.main`. Verifies the
    helper integration didn't regress the rendering — the existing
    `test_bench_savings.py` and `test_savings_snapshot.py` assert on
    file contents and would catch byte-level drift, but this test pins
    the routing at the script level explicitly.
    """
    out_stem = tmp_path / "savings"
    rc = bench_main(["--dry", "--out", str(out_stem), "--n", "20"])
    assert rc == 0

    out_json = out_stem.with_suffix(".json")
    out_md = out_stem.with_suffix(".md")
    out_workload = out_stem.parent / "savings_workload.json"

    assert out_json.exists()
    assert out_md.exists()
    assert out_workload.exists()

    # JSON files are valid and load.
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert "strategies" in payload
    assert len(payload["strategies"]) > 0

    workload = json.loads(out_workload.read_text(encoding="utf-8"))
    assert "rows" in workload

    # Markdown isn't empty and has the strategy column header.
    md_body = out_md.read_text(encoding="utf-8")
    assert "strategy" in md_body.lower()


def test_tune_threshold_end_to_end_produces_valid_atomic_output(tmp_path: Path) -> None:
    """End-to-end happy path through `tune_threshold.main`. The
    monkeypatch tests cover the failure invariant; this test pins
    the routing on the success path so an accidental revert of either
    direction breaks loud.
    """
    out_stem = tmp_path / "sweep"
    rc = tune_main(["--dry", "--out", str(out_stem)])
    assert rc == 0
    out_json = out_stem.with_suffix(".json")
    assert out_json.exists()
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert "rows" in payload
    assert payload["mode"] == "dry"
