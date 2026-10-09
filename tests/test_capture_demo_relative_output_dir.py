"""A relative ``--output-dir`` names the same file for the dashboard (#277).

``--launch-streamlit`` runs the dashboard with ``cwd=REPO_ROOT``, and the
cheat-sheet command only works from the repo root. The ``--json`` it handed
over was ``<output-dir>/savings_demo.json`` as typed, so a relative
``--output-dir`` was resolved against the repo root by the reader and against
the operator's cwd by the writer. Measured at ``ac266f1``, from ``/tmp/elsewhere``
with ``--output-dir docs/demo-artifacts``: the dashboard read the repo's own
``docs/demo-artifacts/savings_demo.json`` from an earlier take, and its caption
showed the same string checklist step 3a tells the operator to confirm.
"""

from __future__ import annotations

import io
import os
import shlex
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tests.test_capture_demo_launch_streamlit import _fake_streamlit, children  # noqa: F401
from tests.test_capture_demo_smoke import _load_capture_module

#: The fake child reports where *it* resolves `--json`, from its own cwd.
_RESOLVING_STREAMLIT = """
import os, sys, time
j = sys.argv[sys.argv.index("--json") + 1]
print("RESOLVED " + os.path.realpath(os.path.join(os.getcwd(), j)), flush=True)
print("  Local URL: http://localhost:8501", flush=True)
time.sleep(60)
"""


def test_the_launched_dashboard_reads_the_json_this_run_wrote(
    children,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture_demo = children
    _fake_streamlit(tmp_path / "bin", _RESOLVING_STREAMLIT)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(capture_demo.webbrowser, "open", lambda url: None)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = capture_demo.main(
            [
                "--pause-seconds",
                "0",
                "--output-dir",
                "demo",
                "--launch-streamlit",
                "--skip-dashboard-cheatsheet",
            ]
        )
    assert rc == 0, err.getvalue()
    written = elsewhere / "demo" / capture_demo.STABLE_SAVINGS_JSON
    assert written.exists(), "the run wrote no JSON -- the arm below would be vacuous"
    log = (elsewhere / "demo" / capture_demo.STREAMLIT_LOG).read_text(encoding="utf-8")
    resolved = next(
        line[len("RESOLVED ") :] for line in log.splitlines() if line.startswith("RESOLVED ")
    )
    assert resolved == os.path.realpath(written)
    assert not resolved.startswith(os.path.realpath(capture_demo.REPO_ROOT) + os.sep)


def test_the_cheatsheet_command_names_an_absolute_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_demo = _load_capture_module()
    monkeypatch.chdir(tmp_path)
    sheet = capture_demo._dashboard_cheatsheet(Path("demo") / "savings_demo.json")
    command = next(
        line.strip("# ").strip() for line in sheet.splitlines() if "streamlit run" in line
    )
    argv = shlex.split(command)
    assert argv[-2] == "--json"
    assert argv[-1] == str(tmp_path / "demo" / "savings_demo.json")
