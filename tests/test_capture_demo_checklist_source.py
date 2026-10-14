"""Checklist step 3a names the string the dashboard caption shows (#304).

The caption is ``source: `{args.json}``` (#241), so it shows the ``--json`` it
was given verbatim. #277 made that argument absolute in step 1's command and
in the ``--launch-streamlit`` argv, and left step 3a formatting the path as
typed. Measured on ``main`` from another directory with ``--output-dir demo``::

    1.  streamlit run dashboard/app.py -- --json /tmp/elsewhere/demo/savings_demo.json
    3a. confirm it reads demo/savings_demo.json.

So a correct take failed the one check meant to catch a wrong dashboard.
"""

from __future__ import annotations

import importlib.util
import re
import shlex
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import capture_demo  # noqa: E402

_STEP_3A = re.compile(r"confirm it reads (.+)\.$", re.MULTILINE)


def _step_3a(sheet: str) -> str:
    found = _STEP_3A.findall(sheet)
    assert len(found) == 1, sheet
    return found[0]


def _step_1_json(sheet: str) -> str:
    command = next(
        line.strip("# ").strip() for line in sheet.splitlines() if "streamlit run" in line
    )
    argv = shlex.split(command)
    assert argv[-2] == "--json", argv
    return argv[-1]


_RELATIVE = ["demo/savings_demo.json", "savings_demo.json", "a/../b/savings_demo.json"]


@pytest.mark.parametrize("launched_pid", [None, 4242], ids=["cheatsheet", "launched"])
@pytest.mark.parametrize("given", _RELATIVE)
def test_step_3a_names_the_json_step_1_passes(
    given: str, launched_pid: int | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    sheet = capture_demo._dashboard_cheatsheet(Path(given), launched_pid=launched_pid)
    assert _step_3a(sheet) == _step_1_json(sheet)
    assert _step_3a(sheet) == str(tmp_path / given)


def test_an_absolute_path_is_named_as_given(tmp_path: Path) -> None:
    """Control: the default `--output-dir` is absolute and always agreed."""
    path = tmp_path / "demo" / "savings_demo.json"
    sheet = capture_demo._dashboard_cheatsheet(path)
    assert _step_3a(sheet) == str(path) == _step_1_json(sheet)


def test_step_3a_is_what_the_dashboard_caption_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caption is `args.json`; parse the argv the way the dashboard does."""
    if importlib.util.find_spec("streamlit") is None or importlib.util.find_spec("pandas") is None:
        pytest.skip("streamlit/pandas not installed (the [dashboard] extra)")
    from dashboard.app import _parse_args

    monkeypatch.chdir(tmp_path)
    path = Path("demo") / "savings_demo.json"
    argv = capture_demo._streamlit_argv(path)
    caption_source = _parse_args(argv[argv.index("--") + 1 :]).json
    assert _step_3a(capture_demo._dashboard_cheatsheet(path)) == caption_source


def test_main_with_a_relative_output_dir_prints_the_absolute_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    rc = capture_demo.main(["--pause-seconds", "0", "--no-open", "--output-dir", "demo"])
    out = capsys.readouterr().out
    assert rc == 0
    stable = tmp_path / "demo" / capture_demo.STABLE_SAVINGS_JSON
    assert stable.exists(), "the run wrote no JSON -- the arm below would be vacuous"
    assert _step_3a(out) == str(stable)
