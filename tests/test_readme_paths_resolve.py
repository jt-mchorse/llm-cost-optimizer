"""README ↔ filesystem path snapshot (#25).

Every path the README quotes inside ``backticks`` or a ```bash``` /
```text``` fence that points at a real file in this repo must resolve
to an existing path. Before #25 the README's "Today the five runtime
layers ship" bullet and its Demo section both referenced
``cost_optimizer/dashboard/app.py``, but the actual dashboard lives
at ``dashboard/app.py`` — a reader who copy-pasted the literal command
got ``File does not exist`` from Streamlit.

This test is path-only — it does not run the commands. It just
asserts that every README-quoted path resolves on disk, so a future
rename can't drift the README out from under the user.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"

# Project-relative path tokens shaped like `dashboard/app.py`,
# `scripts/foo.py`, `docs/architecture.md`, etc. Tokens with a scheme
# (`http`, `https`) are external; tokens that are pure filenames (no
# slash) are not project paths.
_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"((?:dashboard|cost_optimizer|scripts|docs|tests|fixtures|MEMORY)/[A-Za-z0-9_./-]+"
    r"\.(?:py|sh|json|md|yaml|yml|html|png|svg|ipynb))"
)

# Backtick-delimited token, e.g. `dashboard/app.py`. Triple-backtick
# fence content also extracted — the constraint is "in a code context",
# not "in inline code only".
_BACKTICK_RE = re.compile(r"`([^`]+?)`")
_FENCE_RE = re.compile(r"```(?:[A-Za-z0-9]+)?\n(.*?)\n```", re.DOTALL)

# Paths documented as operator-generated (i.e. produced by a script's
# `--out` flag or by a real-API run, intentionally not committed per
# no-fabricated-benchmarks rule). These are excluded from the
# resolves-on-disk assertion.
_KNOWN_OPERATOR_GENERATED: frozenset[str] = frozenset(
    {
        "docs/threshold_demo.png",
        "docs/threshold_report.md",
        "docs/savings_real.md",
    }
)


@pytest.fixture(scope="module")
def readme_text() -> str:
    return README.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def paths_inside_code(readme_text: str) -> list[str]:
    """Project-relative paths that appear inside ``backticks`` or
    a ```bash``` / ```text``` fence — i.e. paths a reader would
    copy-paste as a command argument or import target.
    """
    code_regions: list[str] = list(_FENCE_RE.findall(readme_text))
    code_regions.extend(_BACKTICK_RE.findall(readme_text))
    seen: dict[str, None] = {}
    for region in code_regions:
        for m in _PATH_RE.findall(region):
            seen.setdefault(m, None)
    return list(seen)


def test_code_quoted_paths_resolve_on_disk(paths_inside_code: list[str]) -> None:
    """Every README-quoted path (inside backticks or a code fence)
    must either exist on disk or be on the operator-generated allow-list.
    """
    missing = [
        p
        for p in paths_inside_code
        if not (REPO_ROOT / p).exists() and p not in _KNOWN_OPERATOR_GENERATED
    ]
    assert not missing, (
        "README references the following paths in code that don't exist: "
        f"{missing}. Either fix the path in the README or, if the file is "
        "intentionally operator-generated (per no-fabricated-benchmarks), "
        "add it to `_KNOWN_OPERATOR_GENERATED` in this test with a brief "
        "comment. (The original instance of this kind of drift was #25's "
        "cost_optimizer/dashboard/app.py → dashboard/app.py rename.)"
    )


def test_dashboard_app_referenced_by_correct_path(readme_text: str) -> None:
    # Hard-pin the original failure mode: even if the file ever moves
    # again, this assertion documents the exact bug shape #25 closed.
    assert "cost_optimizer/dashboard/app.py" not in readme_text, (
        "README must not reference the non-existent `cost_optimizer/dashboard/app.py` path. "
        "The dashboard lives at top-level `dashboard/app.py`; this was #25's fix."
    )
    assert "dashboard/app.py" in readme_text, (
        "README must reference `dashboard/app.py` somewhere — that's where "
        "the dashboard actually lives. If the dashboard was renamed, update "
        "both the README and this assertion."
    )
