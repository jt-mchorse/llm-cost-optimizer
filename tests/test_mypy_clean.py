"""Type-checking lock for ``cost_optimizer`` (#129).

``cost_optimizer`` ships a ``py.typed`` marker (#127), so its annotations
are visible to downstream type-checkers. This test is the in-repo half of
the contract: it runs the configured ``mypy`` gate over the package and
asserts it exits clean, so an annotation that drifts out of shape fails a
test — not just the (separately wired) CI ``mypy`` step.

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
