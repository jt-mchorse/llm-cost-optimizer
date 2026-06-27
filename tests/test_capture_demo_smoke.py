"""Smoke test for ``scripts/capture_demo.py``.

Same hermetic contract as ``tests/test_bench_savings.py`` — runs
end-to-end under the stub client; no API key, no live network,
no streamlit spawn. Asserts that STAGE 1 runs the bench under
``--dry`` and renders the per-strategy savings rows, that the
stable artifact copies land under ``--output-dir``, and that
STAGE 2's cheat-sheet prints (or is suppressed, under the flag).
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


def _load_capture_module():
    """Load ``scripts/capture_demo.py`` as a fresh module.

    The repo's existing ``scripts/`` imports run from repo-root via
    ``sys.path.insert``; this loader uses the same mechanism so the
    smoke test exercises the same import path the script's own
    ``_import_bench_main()`` helper does.
    """
    repo_root = Path(__file__).resolve().parent.parent
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if "capture_demo" in sys.modules:
        del sys.modules["capture_demo"]
    import capture_demo  # noqa: WPS433 — dynamic import is the point here.

    return capture_demo


def test_capture_demo_runs_bench_and_writes_stable_artifacts(tmp_path: Path) -> None:
    capture_demo = _load_capture_module()

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = capture_demo.main(
            [
                "--pause-seconds",
                "0",
                "--no-open",
                "--output-dir",
                str(tmp_path),
                "--skip-dashboard-cheatsheet",
            ]
        )
    out = buf.getvalue()

    assert rc == 0, f"capture_demo exited {rc}; stdout:\n{out}"

    # Stage 1 banner + bench output markers.
    assert "STAGE 1" in out, f"expected STAGE 1 banner; got:\n{out}"
    # The bench prints `bench wrote <path>.json` and `<path>.md` lines.
    assert "bench wrote" in out
    # Per-strategy summary lines include `$<amount>` and `saved $<amount>`.
    # We only need to confirm the row format actually printed.
    assert "saved $" in out, f"expected per-strategy `saved $...` rows; got:\n{out}"

    # Stable artifact copies materialized.
    stable_md = tmp_path / "savings_demo.md"
    stable_json = tmp_path / "savings_demo.json"
    assert stable_md.exists(), f"expected {stable_md} to be copied from the bench output"
    assert stable_json.exists(), f"expected {stable_json} to be copied"
    assert str(stable_md) in out, "stable .md path should appear in stdout"
    assert str(stable_json) in out, "stable .json path should appear in stdout"

    # Stage 2 banner present even when cheat-sheet is skipped (the banner
    # is what the recording cuts on; the cheat-sheet body is suppressed).
    assert "STAGE 2" in out


def test_capture_demo_prints_dashboard_cheatsheet_by_default(tmp_path: Path) -> None:
    capture_demo = _load_capture_module()

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = capture_demo.main(
            [
                "--pause-seconds",
                "0",
                "--no-open",
                "--output-dir",
                str(tmp_path),
            ]
        )
    assert rc == 0
    out = buf.getvalue()

    assert "STAGE 2" in out
    assert "Streamlit dashboard tour" in out
    # The cheat-sheet must reference the exact streamlit command + URL +
    # the three checklist anchors so the operator's recording path is
    # frame-for-frame reproducible.
    assert "streamlit run dashboard/app.py" in out
    assert "http://localhost:8501" in out
    assert "Strategy summary table" in out
    assert "Cumulative-savings chart" in out
    assert "Strategy comparison view" in out


def test_capture_demo_skip_dashboard_suppresses_cheatsheet(tmp_path: Path) -> None:
    capture_demo = _load_capture_module()

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = capture_demo.main(
            [
                "--pause-seconds",
                "0",
                "--no-open",
                "--output-dir",
                str(tmp_path),
                "--skip-dashboard-cheatsheet",
            ]
        )
    assert rc == 0
    out = buf.getvalue()

    # The banner stays (it's the recording's cue point) but the
    # checklist body is gone.
    assert "STAGE 2" in out
    assert "Strategy summary table" not in out
    assert "streamlit run dashboard/app.py" not in out


def test_capture_demo_exposes_main_callable() -> None:
    """Same uniform contract as the other scripts in this repo: a
    ``main(argv) -> int`` callable so the script is importable +
    driveable from tests."""
    capture_demo = _load_capture_module()
    assert hasattr(capture_demo, "main"), "scripts/capture_demo.py must expose main()"
    import inspect

    sig = inspect.signature(capture_demo.main)
    assert "argv" in sig.parameters, f"main() must accept argv; got: {sig}"


def test_capture_demo_opens_dashboard_url_by_default(tmp_path: Path, monkeypatch) -> None:
    # #100: the --no-open help documents "default is to open the URL once STAGE 2
    # begins", but the open was nested inside the --launch-streamlit branch, so on
    # the default path (no --launch-streamlit) it never fired and --no-open
    # controlled nothing. A default run must open DASHBOARD_URL.
    capture_demo = _load_capture_module()
    opened: list[str] = []
    monkeypatch.setattr(capture_demo.webbrowser, "open", lambda url: opened.append(url))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = capture_demo.main(
            ["--pause-seconds", "0", "--output-dir", str(tmp_path), "--skip-dashboard-cheatsheet"]
        )
    assert rc == 0, buf.getvalue()
    assert opened == [capture_demo.DASHBOARD_URL], (
        "a default capture run must open the dashboard URL (the documented --no-open default); "
        f"webbrowser.open calls: {opened}"
    )


def test_capture_demo_no_open_suppresses_browser(tmp_path: Path, monkeypatch) -> None:
    # The flip side: --no-open must actually suppress the open (and not double-open).
    capture_demo = _load_capture_module()
    opened: list[str] = []
    monkeypatch.setattr(capture_demo.webbrowser, "open", lambda url: opened.append(url))

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = capture_demo.main(
            [
                "--pause-seconds",
                "0",
                "--no-open",
                "--output-dir",
                str(tmp_path),
                "--skip-dashboard-cheatsheet",
            ]
        )
    assert rc == 0, buf.getvalue()
    assert opened == [], f"--no-open must not open a browser; got: {opened}"
