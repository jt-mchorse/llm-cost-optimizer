"""Every flag a script's Usage block shows must be one its parser accepts (#178).

`scripts/tune_threshold.py` documented

    python scripts/tune_threshold.py --dataset path/to.jsonl --out docs/threshold

and there is no `--dataset` flag. Because `argparse` renders the module
docstring as the parser ``description``, `--help` printed a usage example
that exits 2, directly above the flag list contradicting it.

The check is black-box — it reads the script's own ``--help`` — for two
reasons. The parsers here are built inline inside ``main()``, so there's no
factory to call; and a regex sweep over ``add_argument`` calls produced five
false positives across the portfolio that a real parser resolves for free:

- second-position aliases (``add_argument("--out", "--output")``) are real
  flags that ``--help`` lists;
- ``action=argparse.BooleanOptionalAction`` synthesizes ``--no-dry`` from
  ``--dry`` with no second ``add_argument``;
- flags belonging to a *different* command quoted in prose (``vector-bench
  load --run-id``) are excluded by reading the ``Usage:`` block specifically
  rather than the whole docstring.

Two details keep it honest in both directions. Accepted flags come only from
the *option-entry* lines of the help output, never from option help prose —
`--dry`'s own help text mentions "Run `--dry`", and harvesting that would let
a ghost flag mentioned anywhere pass. And nothing here executes a script with
real arguments: `--help` exits before `main()` does any work, so the check
can't write into `docs/`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Long flags only. Short flags are ambiguous to extract from prose (`-3 dB`
# reads as a flag), and every drift of this kind in this repo shows up on the
# long form.
_FLAG_RE = re.compile(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)")

# An argparse option entry: exactly two spaces, then a dash. Continuation and
# help text are indented further.
_OPTION_LINE_RE = re.compile(r"^ {2}(-\S.*)$")


def _script_paths() -> list[Path]:
    return sorted(p for p in SCRIPTS_DIR.glob("*.py") if not p.name.startswith("_"))


def _usage_block(path: Path) -> str:
    """Text following a ``Usage:`` line in the module docstring.

    Scoped deliberately: elsewhere a docstring legitimately names other
    commands' flags, and widening this is what generated false positives.
    """
    text = path.read_text(encoding="utf-8")
    match = re.match(r'\s*(?:#![^\n]*\n)?\s*"""(.*?)"""', text, re.S)
    if not match or "Usage:" not in match.group(1):
        return ""
    return match.group(1).split("Usage:", 1)[1]


def _help_text(path: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(path), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"{path.name} --help exited {proc.returncode}: {proc.stderr}"
    return proc.stdout


def _accepted_flags(help_text: str) -> set[str]:
    """Flags from option-entry lines only — not from option help prose."""
    accepted: set[str] = set()
    for line in help_text.splitlines():
        entry = _OPTION_LINE_RE.match(line)
        if not entry:
            continue
        # Option strings end at the first run of 2+ spaces (the help column).
        option_part = re.split(r"\s{2,}", entry.group(1), maxsplit=1)[0]
        accepted.update(_FLAG_RE.findall(option_part))
    return accepted


@pytest.mark.parametrize("script", _script_paths(), ids=lambda p: p.name)
def test_usage_block_flags_are_accepted_by_the_parser(script: Path) -> None:
    documented = set(_FLAG_RE.findall(_usage_block(script)))
    if not documented:
        pytest.skip(f"{script.name}'s docstring has no Usage block naming flags")

    accepted = _accepted_flags(_help_text(script))
    assert accepted, f"parsed no options out of {script.name} --help; the pattern went stale"

    ghosts = sorted(documented - accepted)
    assert not ghosts, (
        f"{script.name}'s Usage block documents {ghosts}, which its own parser "
        f"rejects. `--help` renders that block as the description, so the help "
        f"text shows a command that exits 2. Accepted: {sorted(accepted)}"
    )


def test_the_check_actually_inspects_something() -> None:
    """Anti-vacuous: the test above is skip-guarded, so it can silently no-op.

    A refactor that drops every Usage block would leave this suite green while
    checking nothing.
    """
    checked = [s.name for s in _script_paths() if _FLAG_RE.findall(_usage_block(s))]
    assert checked, (
        "no script under scripts/ has a Usage block naming flags — the lock "
        "above is inspecting nothing"
    )
