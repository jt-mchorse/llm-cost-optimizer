"""Snapshot tests locking the committed savings artifacts to the bench output.

`tests/test_bench_savings.py` covers *relative* invariants (deterministic
order, mix proportions, math identities). This module locks the *absolute*
numbers — what's committed to `docs/savings.json`, `docs/savings.md`, and
the README's savings table must match what `run_bench` produces today, so
the public-facing claim cannot silently desync from the implementation.

When one of these tests fails, the regen path is one line:

    python scripts/bench_savings.py --dry --out docs/savings

…followed by `git diff docs/ README.md` to inspect the change before commit.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_savings import _format_markdown, run_bench  # noqa: E402

REGEN_HINT = (
    "Regenerate the committed artifacts:\n"
    "  python scripts/bench_savings.py --dry --out docs/savings\n"
    "Then inspect the change with `git diff docs/ README.md` before committing."
)

DOCS_DIR = _REPO_ROOT / "docs"
SAVINGS_JSON = DOCS_DIR / "savings.json"
SAVINGS_MD = DOCS_DIR / "savings.md"
README = _REPO_ROOT / "README.md"

# Map README strategy-row substrings to the `strategy` field in savings.json.
# Matched by substring (not equality) so the human-readable label can evolve
# cosmetically without breaking the snapshot — what we lock are the numeric
# cells in each row, not the prose.
README_STRATEGY_KEYWORDS = (
    "baseline",
    "prompt caching",
    "semantic cache",
    "uncertainty router",
    "batch API",
)


def test_run_bench_payload_matches_committed_savings_json() -> None:
    """`run_bench(n=500, seed=0xC057)` must equal the committed `docs/savings.json`."""
    payload = run_bench(n=500, seed=0xC057)
    committed = json.loads(SAVINGS_JSON.read_text(encoding="utf-8"))
    # JSON round-trip with sort_keys + indent gives a stable string for both
    # sides, so the assertion message is human-readable on diff.
    payload_s = json.dumps(payload, sort_keys=True, indent=2)
    committed_s = json.dumps(committed, sort_keys=True, indent=2)
    assert payload_s == committed_s, (
        f"docs/savings.json is out of sync with run_bench(n=500, seed=0xC057).\n{REGEN_HINT}"
    )


def test_format_markdown_output_matches_committed_savings_md() -> None:
    """`_format_markdown(run_bench(...))` must equal `docs/savings.md` byte-for-byte."""
    payload = run_bench(n=500, seed=0xC057)
    rendered = _format_markdown(payload)
    committed = SAVINGS_MD.read_text(encoding="utf-8")
    assert rendered == committed, (
        f"docs/savings.md is out of sync with _format_markdown(run_bench(...)).\n{REGEN_HINT}"
    )


# ----------------------------------------------------------------------
# README-table snapshot
# ----------------------------------------------------------------------


_TABLE_HEADER_RE = re.compile(
    r"^\| Strategy \| Rows \| \$ spent \| \$ saved \| % saved \| Mean quality \|"
)


def _extract_readme_savings_table_rows() -> list[list[str]]:
    """Find the README's savings table, return one row of cells per strategy.

    Returns a list of cell-lists (the header and separator rows are
    skipped). Cells are stripped but otherwise untransformed; numeric
    interpretation happens in the test body.

    Raises AssertionError when the table can't be located so the failure
    mode is loud rather than a silent pass-with-empty-list.
    """
    lines = README.read_text(encoding="utf-8").splitlines()
    header_index: int | None = None
    for i, line in enumerate(lines):
        if _TABLE_HEADER_RE.match(line):
            header_index = i
            break
    assert header_index is not None, (
        "Could not locate the savings table header in README.md. The test "
        "expects a row beginning `| Strategy | Rows | $ spent | $ saved | "
        "% saved | Mean quality |`. If the README structure changed "
        "intentionally, update _TABLE_HEADER_RE in this file."
    )
    # The separator row (`| --- | ---: | ...`) follows the header; data rows
    # follow that. Data rows are pipe-bounded; the first non-pipe line ends
    # the table.
    rows: list[list[str]] = []
    for line in lines[header_index + 2 :]:
        if not line.strip().startswith("|"):
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    assert len(rows) >= len(README_STRATEGY_KEYWORDS), (
        f"Expected at least {len(README_STRATEGY_KEYWORDS)} strategy rows in "
        f"the README savings table; found {len(rows)}."
    )
    return rows


def _normalize_dollar(text: str) -> float:
    """Strip a `$` prefix and convert to float; handle `$-0.0892` as well as `$0.0288`."""
    cleaned = text.strip().lstrip("$").replace(",", "")
    return float(cleaned)


def _normalize_percent(text: str) -> float:
    """Convert a percent string like `84.0%` to a fraction (0.84)."""
    cleaned = text.strip().rstrip("%")
    return float(cleaned) / 100.0


def _row_for_keyword(rows: list[list[str]], keyword: str) -> list[str]:
    matches = [r for r in rows if r and keyword in r[0]]
    assert len(matches) == 1, (
        f"Expected exactly one README row matching keyword {keyword!r}; "
        f"found {len(matches)}: {[r[0] for r in matches]}"
    )
    return matches[0]


@pytest.mark.parametrize("keyword", README_STRATEGY_KEYWORDS)
def test_readme_savings_table_matches_savings_json(keyword: str) -> None:
    """Every numeric cell in the README's table matches `docs/savings.json`.

    Strategy-name match is by substring so a cosmetic rename of the
    human-readable label doesn't break the snapshot; what we lock is
    the numbers. Tolerance accounts for the README rounding to 4
    decimals on dollars, 1 decimal on percent, 3 decimals on quality
    (matching what `_format_markdown` produces).
    """
    rows = _extract_readme_savings_table_rows()
    readme_row = _row_for_keyword(rows, keyword)
    # Columns in the README table:
    #   0: Strategy
    #   1: Rows
    #   2: $ spent
    #   3: $ saved
    #   4: % saved
    #   5: Mean quality
    #   6: Extra
    readme_spent = _normalize_dollar(readme_row[2])
    readme_saved = _normalize_dollar(readme_row[3])
    readme_pct = _normalize_percent(readme_row[4])
    readme_quality = float(readme_row[5].strip())

    committed = json.loads(SAVINGS_JSON.read_text(encoding="utf-8"))
    strategy_matches = [s for s in committed["strategies"] if keyword in s["strategy"]]
    assert len(strategy_matches) == 1, (
        f"Expected exactly one strategy in savings.json containing {keyword!r}; "
        f"found {len(strategy_matches)}: {[s['strategy'] for s in strategy_matches]}"
    )
    s = strategy_matches[0]

    # README rounding: 4 decimals on dollar amounts.
    assert readme_spent == pytest.approx(s["total_usd"], abs=5e-5), (
        f"README $ spent for {keyword!r} ({readme_spent}) doesn't match "
        f"savings.json total_usd ({s['total_usd']}).\n{REGEN_HINT}"
    )
    assert readme_saved == pytest.approx(s["saved_usd"], abs=5e-5), (
        f"README $ saved for {keyword!r} ({readme_saved}) doesn't match "
        f"savings.json saved_usd ({s['saved_usd']}).\n{REGEN_HINT}"
    )
    # README percent rounding: 1 decimal (e.g. 84.0%, -154.8%). Worst-case
    # round-half-to-even error is 5e-3 in fraction terms (0.5 percentage
    # points); pick that as the tolerance.
    assert readme_pct == pytest.approx(s["saved_pct"], abs=5e-3), (
        f"README % saved for {keyword!r} ({readme_pct}) doesn't match "
        f"savings.json saved_pct ({s['saved_pct']}).\n{REGEN_HINT}"
    )
    # Mean quality: 3 decimals.
    assert readme_quality == pytest.approx(s["mean_quality"], abs=5e-4), (
        f"README mean quality for {keyword!r} ({readme_quality}) doesn't "
        f"match savings.json mean_quality ({s['mean_quality']}).\n{REGEN_HINT}"
    )


def test_readme_savings_table_row_count_matches_savings_json() -> None:
    """The README must list every strategy in savings.json (no quiet omissions)."""
    rows = _extract_readme_savings_table_rows()
    committed = json.loads(SAVINGS_JSON.read_text(encoding="utf-8"))
    assert len(rows) == len(committed["strategies"]), (
        f"README savings table has {len(rows)} rows but savings.json has "
        f"{len(committed['strategies'])} strategies.\n{REGEN_HINT}"
    )
