"""The dashboard cheat-sheet describes the dashboard it launches (#241).

STAGE 2's cheat-sheet is what the operator follows while recording. At
`1dcb7ae` three of its claims were false against `dashboard/app.py`:

* "The dashboard reads the committed docs/savings.json that STAGE 1 just
  regenerated" -- STAGE 1 writes `<output-dir>/savings_run.*`, and the
  dashboard reads `docs/savings.json` unless given `--json`. `--launch-streamlit`
  passed none either.
* "the page footer or a `?source=...` URL parameter shows this" -- the page
  named no source and reads no query parameters.
* "open the comparison panel" -- there is none.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import capture_demo  # noqa: E402

_APP = _REPO_ROOT / "dashboard" / "app.py"


def _dashboard_sections() -> set[str]:
    """Every `st.subheader("...")` title in the dashboard, by AST."""
    tree = ast.parse(_APP.read_text(encoding="utf-8"))
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "subheader"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def test_the_section_walk_finds_the_dashboard_sections() -> None:
    """Control: a walk that found nothing would make the next arm vacuous."""
    sections = _dashboard_sections()
    assert len(sections) >= 5, sections
    assert "Per-strategy details" in sections


def test_every_section_the_checklist_quotes_exists() -> None:
    sheet = capture_demo._dashboard_cheatsheet(Path("out/savings_demo.json"))
    quoted = set(re.findall(r'"([^"]+)"', sheet))
    assert quoted, "the checklist quotes no section — the arm below would be vacuous"
    assert quoted <= _dashboard_sections(), sorted(quoted - _dashboard_sections())


@pytest.mark.parametrize("phrase", ["comparison panel", "?source=", "page footer"])
def test_the_checklist_no_longer_points_at_things_the_page_lacks(phrase: str) -> None:
    assert phrase not in capture_demo._dashboard_cheatsheet(Path("x.json"))


def test_the_cheatsheet_command_points_at_the_runs_own_json(tmp_path: Path) -> None:
    json_path = tmp_path / "out dir" / "savings_demo.json"
    sheet = capture_demo._dashboard_cheatsheet(json_path)
    command = next(
        line.strip("# ").strip() for line in sheet.splitlines() if "streamlit run" in line
    )
    # Quoted for the shell, so a path with a space survives a copy-paste.
    argv = shlex.split(command)
    assert argv == ["streamlit", "run", "dashboard/app.py", "--", "--json", str(json_path)]


def test_launch_streamlit_passes_the_same_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    class _Popen:
        pid = 1

        def __init__(self, argv: list[str], **_: Any) -> None:
            calls.append(argv)

    monkeypatch.setattr(capture_demo.shutil, "which", lambda _: "/usr/bin/streamlit")
    monkeypatch.setattr(capture_demo.subprocess, "Popen", _Popen)
    capture_demo._maybe_launch_streamlit(Path("o/savings_demo.json"), tmp_path / "streamlit.log")
    (argv,) = calls
    # Streamlit's own option before `--`, the script's after it.
    assert argv == [
        "streamlit",
        "run",
        "dashboard/app.py",
        "--server.headless",
        "true",
        "--",
        "--json",
        # Absolute (#277): the child runs with cwd=REPO_ROOT, so a relative
        # path would be resolved against the wrong directory.
        str(Path("o/savings_demo.json").absolute()),
    ]


def test_main_hands_the_stable_json_to_the_cheatsheet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = capture_demo.main(["--pause-seconds", "0", "--no-open", "--output-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    stable = tmp_path / capture_demo.STABLE_SAVINGS_JSON
    assert stable.exists()
    assert shlex.join(["--json", str(stable)]) in out


def test_the_dashboard_parses_the_json_flag_the_command_passes() -> None:
    """The flag the cheat-sheet hands over is the one `_parse_args` reads."""
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    from dashboard.app import _parse_args

    assert _parse_args(["--json", "o/savings_demo.json"]).json == "o/savings_demo.json"


def test_the_dashboard_caption_names_its_source() -> None:
    source = _APP.read_text(encoding="utf-8")
    assert "source: `{args.json}`" in source
