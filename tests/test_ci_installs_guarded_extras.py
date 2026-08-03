"""Lock: CI installs every optional extra the test suite guards on (#170).

The savings dashboard is layer 5 of the README and reads `docs/savings.json`
directly, and eight tests in `tests/test_bench_savings.py` exist to protect
it. None of them had ever run. `ci.yml`'s `test` job installed `.[dev]`,
which carries neither `streamlit` nor `pandas` — those are the `[dashboard]`
extra — so every one of those tests hit its
`find_spec(...) -> pytest.skip(...)` guard and skipped. The job reported
green on every PR while nothing checked the feature.

The guards themselves are right: a contributor working in a minimal venv
should be able to run the suite without installing a dashboard stack. CI is
not that environment, and the bug was treating it as though it were.

So rather than asserting "CI installs `[dashboard]`" — which a future extra
would sail straight past, exactly the way this one did — this derives the
requirement from the suite itself. Every extra a skip guard names must
appear in the `test` job's install. Add a guard mentioning `[foo]` and this
fails until CI installs `[foo]`.

Deliberately parses the workflow rather than shelling out to pytest: the
assertion has to hold in a contributor's environment where the extras are
genuinely absent and the guards are genuinely firing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_TESTS_DIR = _REPO_ROOT / "tests"

# Matches the human-readable half of a skip guard, e.g.
#   pytest.skip("streamlit not installed (the [dashboard] extra)")
_GUARDED_EXTRA_RE = re.compile(r"the \[([a-z0-9_,-]+)\] extra")

# Matches `pip install -e '.[dev,dashboard]'` and friends.
_INSTALL_EXTRAS_RE = re.compile(r"pip install[^\n]*?-e\s+'?\.\[([a-z0-9_,\- ]+)\]'?")


def _extras_guarded_by_the_suite() -> set[str]:
    found: set[str] = set()
    for path in sorted(_TESTS_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        for match in _GUARDED_EXTRA_RE.finditer(path.read_text(encoding="utf-8")):
            found.update(part.strip() for part in match.group(1).split(","))
    return found


def _extras_installed_by_the_test_job() -> set[str]:
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test"]["steps"]
    installed: set[str] = set()
    for step in steps:
        run = step.get("run") or ""
        for match in _INSTALL_EXTRAS_RE.finditer(run):
            installed.update(part.strip() for part in match.group(1).split(","))
    return installed


def test_ci_workflow_is_parseable() -> None:
    assert _CI_WORKFLOW.is_file(), f"ci.yml missing at {_CI_WORKFLOW}"
    assert "test" in yaml.safe_load(_CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def test_suite_actually_guards_on_some_extra() -> None:
    # Anti-vacuous: if the guards are ever reworded, the lock below starts
    # passing trivially and stops protecting anything. Fail here instead,
    # loudly, so the next person updates _GUARDED_EXTRA_RE rather than
    # inheriting a green test that checks nothing.
    guarded = _extras_guarded_by_the_suite()
    assert guarded, (
        "No test skip-guard mentions '(the [<name>] extra)'. Either the guards "
        "were reworded — update _GUARDED_EXTRA_RE — or they were removed, in "
        "which case delete this lock rather than leaving it passing vacuously."
    )


def test_ci_installs_every_guarded_extra() -> None:
    guarded = _extras_guarded_by_the_suite()
    installed = _extras_installed_by_the_test_job()
    missing = guarded - installed
    assert not missing, (
        f"The test suite skips when {sorted(missing)} is missing, but ci.yml's "
        f"`test` job installs only {sorted(installed)}. Those tests will skip "
        "on every CI run and the job will still report green — which is how "
        "the 8 savings-dashboard tests went unrun (#170). Add the extra to the "
        "install line, or drop the tests if they are genuinely not worth "
        "running."
    )


@pytest.mark.parametrize("extra", sorted(_extras_guarded_by_the_suite()))
def test_guarded_extra_is_declared_in_pyproject(extra: str) -> None:
    # A guard naming an extra that doesn't exist can never be satisfied, so
    # the tests behind it would skip forever no matter what CI installs.
    import tomllib

    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"].get("optional-dependencies", {})
    assert extra in declared, (
        f"Tests guard on the [{extra}] extra, but pyproject.toml declares only "
        f"{sorted(declared)}. Those tests can never run."
    )
