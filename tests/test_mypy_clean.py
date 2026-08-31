"""Type-checking lock for ``cost_optimizer`` and ``scripts`` (#129, #201).

``cost_optimizer`` ships a ``py.typed`` marker (#127), so its annotations
are visible to downstream type-checkers. This test is the in-repo half of
the contract: it runs the configured ``mypy`` gate over the package and
asserts it exits clean, so an annotation that drifts out of shape fails a
test — not just the (separately wired) CI ``mypy`` step.

``scripts`` joined the gate in #201. It was outside it not by preference
but because ``mypy cost_optimizer scripts`` stopped before checking
anything ("Source file found twice under different module names: ``_io``
and ``scripts._io`` … errors prevented further checking"), so this lock
covered half the code that produces the README's published numbers. The
scope is asserted below rather than left implicit in ``pyproject.toml``:
a bare ``mypy`` run is clean whether the gate covers two directories or
one, so a future narrowing of ``files`` would pass this test silently.

The mypy configuration is the project's own ``[tool.mypy]`` block in
``pyproject.toml`` (non-strict baseline, D-014); this test invokes ``mypy``
with no arguments so it reads exactly that config, keeping the local test,
the CI step, and a developer's bare ``mypy`` invocation in lockstep.

Skipped (not failed) when mypy isn't importable, so a minimal environment
without the ``dev`` extra can still run the rest of the suite; CI installs
``.[dev]`` so the gate is always exercised there.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_mypy_reports_no_issues() -> None:
    pytest.importorskip("mypy", reason="mypy not installed (dev extra); CI installs it")
    proc = subprocess.run(
        [sys.executable, "-m", "mypy"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "mypy gate failed — the shipped py.typed annotations drifted from "
        "the code. Output:\n" + proc.stdout + proc.stderr
    )


def test_the_gate_covers_both_the_package_and_the_scripts() -> None:
    """Pin the gate's *scope*, which a clean exit code cannot.

    Parsed with ``tomllib``, not grepped: the prose above quotes
    ``files = [...]`` and a substring scan would match this file's own
    docstring rather than the configuration.
    """
    with (_REPO_ROOT / "pyproject.toml").open("rb") as fh:
        config = tomllib.load(fh)
    mypy_config = config["tool"]["mypy"]
    assert set(mypy_config["files"]) == {"cost_optimizer", "scripts"}, mypy_config["files"]
    # Both halves of the mapping fix; either alone leaves mypy unable to give
    # `scripts/_io.py` one unambiguous module name.
    assert mypy_config["mypy_path"] == "."
    assert mypy_config["explicit_package_bases"] is True
