"""Public-surface tests for ``cost_optimizer/__init__.py``.

``cost_optimizer`` re-exports symbols from five submodules (`batch`,
`cache_wrapper`, `pricing`, `router`, `semantic_cache`) and declares
`__all__`. The README quotes four library-use snippets that import
14+ names directly from the top-level package
(`PromptCacheWrapper`, `HashEmbedder`, `InMemoryStorage`,
`SemanticCache`, `EntropySignal`, `JudgeConfidenceSignal`,
`UncertaintyRouter`, `AnthropicBatchBackend`, `BatchRequest`,
`BatchCostQuote`, `compare_realtime_vs_batch`, …).

Coverage is incidentally 100% because existing tests touch enough
re-exports, but no test locks the surface SHAPE — a future submodule
rename or split would silently drop names from `cost_optimizer.*`
and break every downstream importer + every README copy-paste reader.

These four orthogonal axes lock the shape:

1. `__all__` agrees bidirectionally with the AST-parsed
   `from cost_optimizer.X import` block.
2. Every `__all__` entry is bound and non-None.
3. Every README `from cost_optimizer import ...` snippet compiles
   against the live package (extracted via regex so adding a snippet
   auto-includes it without test changes).
4. One anchor per submodule survives at the top level.

Same hygiene as the public-surface snapshot in `llm-eval-harness`
(#24 there). Orthogonal axis to `test_readme_snapshot.py` and
`test_readme_defaults_snapshot.py`, which lock README *text*.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import cost_optimizer

_INIT_PATH = Path(cost_optimizer.__file__)
_REPO_ROOT = _INIT_PATH.parent.parent
_README = _REPO_ROOT / "README.md"

# Matches `from cost_optimizer import X, Y, Z` on a single line, AND
# the parenthesised multi-line form `from cost_optimizer import (\n  X,\n  Y,\n)`.
# Captures the names blob to parse separately.
_README_IMPORT_RE = re.compile(
    r"from\s+cost_optimizer\s+import\s+(?:\(([^)]+)\)|([^\n]+))",
    re.MULTILINE,
)

# Anchor names that prove each submodule's re-exports survived. If
# `__init__.py` ever drops a submodule's whole block, the anchor goes
# missing.
SUBMODULE_ANCHORS = {
    "batch": "AnthropicBatchBackend",
    "cache_wrapper": "PromptCacheWrapper",
    "pricing": "get_pricing",
    "router": "UncertaintyRouter",
    "semantic_cache": "SemanticCache",
}


def _parse_init_from_imports() -> set[str]:
    """Return the set of names imported into ``__init__.py`` via
    top-level ``from cost_optimizer.X import (...)`` blocks."""
    tree = ast.parse(_INIT_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("cost_optimizer.")
        ):
            for alias in node.names:
                # An aliased import (`from X import Y as Z`) adds the alias.
                names.add(alias.asname or alias.name)
    return names


def _readme_cost_optimizer_imports() -> list[tuple[int, set[str]]]:
    """Extract every ``from cost_optimizer import ...`` snippet in the
    README, returning a list of (snippet_index, set_of_names).

    Each README snippet that imports from the top-level package
    becomes one test parametrize case via ``readme_import_snippets``.
    """
    text = _README.read_text(encoding="utf-8")
    snippets: list[tuple[int, set[str]]] = []
    for idx, match in enumerate(_README_IMPORT_RE.finditer(text)):
        names_blob = match.group(1) or match.group(2)
        # Strip comments + whitespace, split on commas.
        cleaned = re.sub(r"#.*", "", names_blob)
        names = {n.strip() for n in cleaned.split(",")}
        names = {n for n in names if n and n.isidentifier()}
        if names:
            snippets.append((idx, names))
    return snippets


def test_all_is_non_empty_and_names_bound() -> None:
    """Every name in ``__all__`` must be importable and non-None."""
    assert cost_optimizer.__all__, "cost_optimizer.__all__ is empty."
    missing: list[str] = []
    none_valued: list[str] = []
    for name in cost_optimizer.__all__:
        if not hasattr(cost_optimizer, name):
            missing.append(name)
            continue
        if getattr(cost_optimizer, name) is None:
            none_valued.append(name)
    assert not missing, (
        f"cost_optimizer.__all__ advertises names that are not bound on "
        f"the package: {missing}. The most likely cause is a re-import "
        f"line was deleted from __init__.py but __all__ wasn't updated."
    )
    assert not none_valued, f"cost_optimizer.__all__ entries bound to None: {none_valued}."


def test_all_matches_actual_top_level_imports() -> None:
    """``__all__`` should equal the set of top-level re-exports.

    Catches both directions of drift: imported-but-not-in-__all__
    (silent ``import *`` miss) and in-__all__-but-not-imported
    (the bound-and-non-None test catches it, but this test names
    the specific entries).
    """
    advertised = set(cost_optimizer.__all__)
    imported = _parse_init_from_imports()
    only_imported = imported - advertised
    only_advertised = advertised - imported
    assert not only_imported, (
        f"Names imported into cost_optimizer/__init__.py but missing "
        f"from __all__: {sorted(only_imported)}. Add to __all__ or stop "
        f"importing at top level."
    )
    assert not only_advertised, (
        f"Names in cost_optimizer.__all__ but not imported at the top "
        f"of __init__.py: {sorted(only_advertised)}. Add the import or "
        f"remove the __all__ entry."
    )


# Build the parametrize cases at import time. The test ID is "snippet-N"
# matching the README's order of appearance, so a CI failure pinpoints
# which snippet broke.
_README_SNIPPETS = _readme_cost_optimizer_imports()


@pytest.mark.parametrize(
    ("snippet_idx", "names"),
    _README_SNIPPETS,
    ids=[f"snippet-{idx}" for idx, _ in _README_SNIPPETS],
)
def test_readme_library_use_snippet_imports_resolve(snippet_idx: int, names: set[str]) -> None:
    """Each README ``from cost_optimizer import ...`` snippet must
    resolve against the live package.

    Extracted by regex so adding a fifth snippet to README's library
    examples auto-becomes a fifth test case without any code change.
    """
    missing = sorted(n for n in names if not hasattr(cost_optimizer, n))
    assert not missing, (
        f"README library-use snippet #{snippet_idx} imports names that "
        f"are no longer on the top-level surface: {missing}. Either "
        f"restore the exports or update the README example."
    )


def test_readme_has_at_least_one_library_use_snippet() -> None:
    """Guard against the parametrize source going empty silently —
    if the regex stops matching (README rewrite, syntax change), the
    parametrize gives zero cases and the snippet tests don't fail,
    they just don't run. This test asserts the source is non-empty
    so the regression mode is loud."""
    assert _README_SNIPPETS, (
        "README contains zero `from cost_optimizer import ...` snippets. "
        "Either the README dropped its library-use examples (regression) "
        "or the regex in this test stopped matching (test bug)."
    )


@pytest.mark.parametrize(
    ("submodule", "anchor"),
    sorted(SUBMODULE_ANCHORS.items()),
    ids=sorted(SUBMODULE_ANCHORS.keys()),
)
def test_submodule_anchor_re_exported(submodule: str, anchor: str) -> None:
    """One anchor per re-exported submodule survives at the top level.

    If a submodule moves (``batch.py`` → ``batch/__init__.py``,
    ``router.py`` → ``routing.py``) and ``__init__.py`` isn't updated,
    the anchor goes missing and this test names which submodule broke.
    """
    assert hasattr(cost_optimizer, anchor), (
        f"`{anchor}` from `cost_optimizer.{submodule}` is no longer "
        f"re-exported at the top level. Did `{submodule}` move or get "
        f"renamed? Update `cost_optimizer/__init__.py`."
    )
