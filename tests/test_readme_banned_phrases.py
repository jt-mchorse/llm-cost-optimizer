"""README banned-phrase lock — sibling to test_architecture_doc.py's
BANNED_PHRASES guard, applied to `README.md` instead of
`docs/architecture.md`.

The portfolio-wide pattern (first authored in prompt-regression-suite#43) catches pre-shipping framing leaking (see `tests/test_architecture_doc.py` line 63).
The README had the same drift class for four section headers
("Semantic cache (#2 · this PR)", "Model routing (#3 · this PR)",
"Batch API integration (#4 · this PR)", "Savings dashboard (#5 · this PR)")
— all four are shipped surface. This file locks the README against
that exact drift returning.

Issue #46.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
README = REPO_ROOT / "README.md"

# Substrings, lowercase. Substring match (case-insensitive). Pinned in a
# tuple so a future loose edit can't silently drop one.
#
# The pattern uses U+00B7 middle dot + space + "this pr" instead of
# bare "this pr" so we tie the match to the exact section-header
# drift shape ("## Foo (#N · this PR)") and don't false-positive
# on legitimate prose substrings like "this producer", "this
# practice", "this print", etc. Tightened in python-async-llm-
# pipelines#40 (where "backpressure to this producer" forced the
# fix); propagated here for portfolio uniformity.
BANNED_PHRASES = ("· this pr",)


@pytest.fixture(scope="module")
def readme_text_lower() -> str:
    return README.read_text(encoding="utf-8").lower()


def test_readme_exists() -> None:
    assert README.is_file(), f"README.md missing at {README}"


@pytest.mark.parametrize("phrase", BANNED_PHRASES)
def test_banned_phrase_absent(readme_text_lower: str, phrase: str) -> None:
    assert phrase not in readme_text_lower, (
        f"README contains banned phrase {phrase!r}. "
        "This is pre-shipping framing for surface that has already shipped; "
        "rewrite the section to its steady-state form."
    )


def test_banned_phrases_tuple_locked() -> None:
    # Hard-pin so a future loose edit of this test can't silently drop
    # one of the guards. Same shape as test_architecture_doc.py's
    # `test_banned_phrases_locked`.
    assert BANNED_PHRASES == ("· this pr",)
