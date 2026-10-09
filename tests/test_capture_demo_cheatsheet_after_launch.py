"""After ``--launch-streamlit``, the cheat-sheet describes the dashboard it launched (#275).

#259 made ``main`` open the port the child reported. The cheat-sheet printed
right after it kept saying "Start the dashboard in a separate terminal" and
"Open the URL the recording captures: http://localhost:8501". On a second take
the child binds 8502, and 8501 is the first take's dashboard, which outlives the
script by design. So the checklist sent the operator to the previous take's
JSON, and told them to start a third server. Every #259 arm passes
``--skip-dashboard-cheatsheet``, so none of them read this text.
"""

from __future__ import annotations

import io
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from tests.test_capture_demo_launch_streamlit import BANNER, _fake_streamlit, children  # noqa: F401


def _launched_run(capture_demo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    _fake_streamlit(
        tmp_path / "bin",
        f"""
        import time
        print({BANNER!r}, flush=True)
        time.sleep(60)
        """,
    )
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(capture_demo.webbrowser, "open", lambda url: None)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = capture_demo.main(
            [
                "--pause-seconds",
                "0",
                "--output-dir",
                str(tmp_path / "out"),
                "--launch-streamlit",
            ]
        )
    assert rc == 0, err.getvalue()
    return out.getvalue()


def _sheet(out: str) -> str:
    head = "# Streamlit dashboard tour (STAGE 2)"
    assert head in out, "no cheat-sheet printed -- the arms below would be vacuous"
    return out[out.index(head) :]


def test_the_cheatsheet_names_the_port_the_child_bound(children, tmp_path, monkeypatch) -> None:  # noqa: F811
    sheet = _sheet(_launched_run(children, tmp_path, monkeypatch))
    assert "http://localhost:8502" in sheet
    assert "http://localhost:8501" not in sheet


def test_the_cheatsheet_does_not_ask_for_a_second_dashboard(
    children,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    out = _launched_run(children, tmp_path, monkeypatch)
    sheet = _sheet(out)
    assert "Start the dashboard" not in sheet
    pid = out.split("spawned streamlit (pid ")[1].split(")")[0]
    assert f"Already running (pid {pid})" in sheet
    # The command it was launched with is still shown, pointing at this run's JSON.
    assert str(tmp_path / "out" / "savings_demo.json") in sheet


def test_the_no_launch_cheatsheet_is_unchanged(tmp_path: Path) -> None:
    """Control: without a launch the text is what it always was."""
    from tests.test_capture_demo_smoke import _load_capture_module

    capture_demo = _load_capture_module()
    json_path = tmp_path / "savings_demo.json"
    sheet = capture_demo._dashboard_cheatsheet(json_path)
    assert "# 1. Start the dashboard in a separate terminal:" in sheet
    assert "#      http://localhost:8501\n" in sheet
    assert "Already running" not in sheet
