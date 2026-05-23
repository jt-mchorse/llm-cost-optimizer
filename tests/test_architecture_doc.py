"""Architecture-doc lock: catch drift between `docs/architecture.md` and
the actual shipped surface of the repo.

Sister to the architecture-doc locks shipped this same week in
``embedding-model-shootout`` (PR #20), ``vector-search-at-scale``
(PR #22), ``llm-eval-harness`` (PR #30), and ``prompt-regression-suite``
(PR #25), plus the JS variants in ``mcp-server-cookbook``,
``nextjs-streaming-ai-patterns``, and ``ai-app-integration-tests``.

This repo's architecture doc annotates surfaces with ``D-NNN`` core-decision
references rather than ``(#NN)`` issue references — so the second invariant
is pivoted from "every shipped issue referenced" to "every active
(non-superseded) core decision referenced from D-002 onward." D-001 is the
scope-baseline and is intentionally not referenced in ``architecture.md``.

Three invariants pinned:

1. **Path-token reachability.** Every backtick-quoted token that starts
   with one of the ``RESOLVABLE_PREFIXES`` resolves on disk. Catches typos
   and renames.

2. **Active-decision coverage.** Every non-superseded ``D-NNN`` in
   ``MEMORY/core_decisions_ai.md`` (excluding ``D-001``) is referenced at
   least once. So a future layer that lands a new decision can't ship
   without the architecture doc updating.

3. **Banned-phrase absence.** Phrases that characterized the pre-fix
   drift in other repos' architecture docs ("this pr", "pending
   downstream", etc.). Pre-empts the same drift here.

Three hard-pin tests lock ``BANNED_PHRASES``, ``RESOLVABLE_PREFIXES``, and
the active-decision set's lower bound so a future loose edit can't silently
weaken the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "architecture.md"
DECISIONS = REPO_ROOT / "MEMORY" / "core_decisions_ai.md"


# Drift shapes specific to the pattern caught across the portfolio's other
# architecture-doc fixes this week. Lowercase substring match. Pinned in a
# tuple so a future loose edit of the test can't silently drop one.
BANNED_PHRASES = (
    "this pr",
    "pending downstream",
    "(unfiled)",
    "to-be-filed",
)


# Path-token prefixes that must resolve on disk if quoted in the doc.
# Backtick-quoted tokens only.
RESOLVABLE_PREFIXES = (
    "cost_optimizer/",
    "scripts/",
    "dashboard/",
    "docs/",
    "tests/",
    ".github/",
)


# Operator-supplied artifacts: paths the doc names as the artifact an
# operator commits *after* running a real workload. These deliberately
# don't exist in-repo (per D-012's "no fabricated benchmarks" posture).
# Hard-pinned so future additions are intentional, not accidents.
OPERATOR_SUPPLIED_PATHS = (
    # D-012: operator runs against real data and commits this file.
    "docs/savings_real.md",
)


# Lower bound for the active-decision coverage check. D-001 (scope baseline)
# is intentionally excluded — it's a portfolio-level baseline, not an
# architectural-shape decision, and architecture.md correctly does not cite
# it. Anything from D-002 onward describes an architectural shape and must
# be cited at least once if active.
MIN_ACTIVE_DECISION_ID = 2


@pytest.fixture(scope="module")
def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def active_decisions() -> tuple[int, ...]:
    """Parse ``MEMORY/core_decisions_ai.md`` for ``id: D-NNN`` entries whose
    ``superseded_by`` is ``null`` and whose numeric id is ``>= MIN_ACTIVE_DECISION_ID``.

    Returns the sorted tuple of active decision numbers (e.g. ``(2, 3, 4, ...)``).
    """
    text = DECISIONS.read_text(encoding="utf-8")
    # Decision blocks are separated by blank lines and start with `- id: D-NNN`.
    # Within each block, capture the id and the superseded_by value.
    blocks = re.split(r"\n(?=- id:)", text)
    active: list[int] = []
    for block in blocks:
        id_match = re.search(r"- id:\s*D-(\d+)", block)
        if not id_match:
            continue
        sup_match = re.search(r"superseded_by:\s*(\S+)", block)
        # Treat a missing superseded_by as active too (defensive).
        is_active = (sup_match is None) or (sup_match.group(1).strip().lower() == "null")
        if is_active:
            n = int(id_match.group(1))
            if n >= MIN_ACTIVE_DECISION_ID:
                active.append(n)
    return tuple(sorted(active))


def _extract_backtick_paths(text: str) -> set[str]:
    """Collect every backtick-quoted token that starts with one of the
    RESOLVABLE_PREFIXES. Placeholder tokens that contain ``<...>`` (variable)
    or ``{...}`` (brace-expansion) are not literal paths a reader would
    copy-paste; they document a *shape* and are excluded from the
    resolvability check.
    """
    found: set[str] = set()
    for match in re.finditer(r"`([^`\n]+)`", text):
        token = match.group(1).strip()
        for prefix in RESOLVABLE_PREFIXES:
            if token.startswith(prefix):
                # Drop trailing punctuation that wouldn't be part of a
                # copy-pasted path.
                while token and token[-1] in ".,;:":
                    token = token[:-1]
                # Drop a trailing `()` from function-style refs.
                token = re.sub(r"\(\)$", "", token)
                # Skip placeholder shapes.
                if "<" in token or "{" in token:
                    break
                if token:
                    found.add(token)
                break
    return found


def _resolves_on_disk(token: str) -> bool:
    return (REPO_ROOT / token).exists()


def test_doc_exists() -> None:
    assert DOC.exists(), f"missing {DOC}"


def test_decisions_file_exists() -> None:
    assert DECISIONS.exists(), f"missing {DECISIONS}"


def test_backtick_paths_resolve_on_disk(doc_text: str) -> None:
    tokens = _extract_backtick_paths(doc_text)
    operator_set = set(OPERATOR_SUPPLIED_PATHS)
    unresolved = sorted(t for t in tokens if not _resolves_on_disk(t) and t not in operator_set)
    assert not unresolved, (
        "docs/architecture.md quotes paths that don't exist on disk:\n"
        + "\n".join(f"  - `{t}`" for t in unresolved)
        + "\n(regenerate the doc to match the current layout, fix the typo, "
        "or — if this is an operator-supplied future artifact — add it to "
        "OPERATOR_SUPPLIED_PATHS in tests/test_architecture_doc.py)"
    )


def test_operator_supplied_paths_actually_absent() -> None:
    """If an operator-supplied artifact ever lands in-repo, it has stopped
    being operator-supplied — drop it from this list so the doc check
    reverts to literal resolvability. Inverse safety net for
    OPERATOR_SUPPLIED_PATHS.
    """
    landed = [p for p in OPERATOR_SUPPLIED_PATHS if (REPO_ROOT / p).exists()]
    assert not landed, (
        "These paths are listed as operator-supplied in "
        "tests/test_architecture_doc.py but exist on disk:\n"
        + "\n".join(f"  - `{p}`" for p in landed)
        + "\n(drop them from OPERATOR_SUPPLIED_PATHS so the resolvability "
        "check covers them as literal paths)"
    )


def test_every_active_decision_referenced(doc_text: str, active_decisions: tuple[int, ...]) -> None:
    referenced = {int(m.group(1)) for m in re.finditer(r"\bD-0*(\d+)\b", doc_text)}
    missing = sorted(set(active_decisions) - referenced)
    assert not missing, (
        "docs/architecture.md doesn't reference these active "
        "(non-superseded) core decisions even once:\n"
        + "\n".join(f"  - D-{n:03d}" for n in missing)
        + "\n(every shipped layer / posture in MEMORY/core_decisions_ai.md "
        "should be annotated in the doc where the relevant code lives; "
        "add a `D-NNN` reference to the relevant bullet)"
    )


def test_no_banned_phrases(doc_text: str) -> None:
    lowered = doc_text.lower()
    hits = [p for p in BANNED_PHRASES if p in lowered]
    assert not hits, (
        "docs/architecture.md contains drift phrases:\n"
        + "\n".join(f"  - {p!r}" for p in hits)
        + "\n(these phrases describe a pre-shipping state; the doc is a "
        "steady-state reference, not a PR description)"
    )


def test_banned_phrases_hard_pin_set() -> None:
    assert BANNED_PHRASES == (
        "this pr",
        "pending downstream",
        "(unfiled)",
        "to-be-filed",
    )


def test_resolvable_prefixes_hard_pin_set() -> None:
    assert RESOLVABLE_PREFIXES == (
        "cost_optimizer/",
        "scripts/",
        "dashboard/",
        "docs/",
        "tests/",
        ".github/",
    )


def test_min_active_decision_id_hard_pin() -> None:
    # D-001 is the scope baseline (portfolio handoff §2) and intentionally
    # not referenced in architecture.md. The active-decision coverage check
    # starts at D-002. If this constant ever drops to 1, the doc would be
    # required to cite the scope baseline, which is a category error.
    assert MIN_ACTIVE_DECISION_ID == 2


def test_operator_supplied_paths_hard_pin_set() -> None:
    assert OPERATOR_SUPPLIED_PATHS == ("docs/savings_real.md",)
