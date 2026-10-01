"""The README's install lines provide every import its code needs (#235).

Following the README on a fresh clone -- `pip install -e '.[dev]'`, then the
documented `ANTHROPIC_API_KEY=... pytest tests/integration -v` three lines
later -- failed at collection with `ModuleNotFoundError: No module named
'anthropic'`. `pyproject.toml` has the `[anthropic]` extra for exactly that, and
the README never named it. The wrapper snippet below it failed the same way.

This lock resolves extras from `pyproject.toml` rather than listing them, so a
new snippet importing a new optional dependency fails here until the README says
how to install it.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_README = (_ROOT / "README.md").read_text(encoding="utf-8")
_PYPROJECT = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

#: The package's own import names.
_OWN = {"cost_optimizer", "dashboard"}

#: Requirement name -> import name, where they differ. Empty today; listed so a
#: dependency whose import name differs is a deliberate entry, not a guess.
_IMPORT_NAME: dict[str, str] = {}


def _requirement_names(reqs: list[str]) -> set[str]:
    return {re.split(r"[<>=!~\[; ]", r, maxsplit=1)[0].strip().lower() for r in reqs}


def _installed_by_readme() -> set[str]:
    """Import names available after the README's `pip install -e '.[...]'` lines."""
    extras: set[str] = set()
    for line in re.findall(r"pip install -e '\.\[([^\]]+)\]'", _README):
        extras |= {e.strip() for e in line.split(",")}
    names = _requirement_names(_PYPROJECT["project"].get("dependencies", []))
    for extra in extras:
        names |= _requirement_names(_PYPROJECT["project"]["optional-dependencies"][extra])
    return {_IMPORT_NAME.get(n, n).replace("-", "_") for n in names}


def _cross_repo_installs() -> set[str]:
    """Portfolio packages the README tells the reader to `pip install git+...`."""
    repos = re.findall(r"pip install git\+https://github\.com/jt-mchorse/([\w-]+)", _README)
    return {r.replace("llm-", "").replace("-", "_") for r in repos}


def _top_level_imports(source: str) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out - set(sys.stdlib_module_names) - {"__future__"}


def _readme_python_blocks() -> list[str]:
    return re.findall(r"```python\n(.*?)```", _README, re.S)


def _available() -> set[str]:
    return _OWN | _installed_by_readme() | _cross_repo_installs()


@pytest.mark.parametrize("index", range(len(_readme_python_blocks())))
def test_every_readme_snippet_import_is_installable_from_the_readme(index: int) -> None:
    block = _readme_python_blocks()[index]
    missing = _top_level_imports(block) - _available()
    assert not missing, (
        f"README python block #{index} imports {sorted(missing)}, which no install "
        f"line in the README provides (extras resolved from pyproject.toml)"
    )


@pytest.mark.parametrize(
    "path", sorted((_ROOT / "tests" / "integration").glob("*.py")), ids=lambda p: p.name
)
def test_the_documented_integration_suite_is_installable_from_the_readme(path: Path) -> None:
    missing = _top_level_imports(path.read_text(encoding="utf-8")) - _available()
    assert not missing, (
        f"{path.name} imports {sorted(missing)}; the README must say how to install it"
    )


def test_the_integration_workflow_installs_through_the_extra() -> None:
    """CI and the documented path install the same thing, bound included."""
    workflow = (_ROOT / ".github" / "workflows" / "integration.yml").read_text(encoding="utf-8")
    assert "pip install -e '.[dev,anthropic]'" in workflow
    assert not re.search(r"pip install[^\n]*\]'\s+anthropic\b", workflow)


def test_the_resolution_is_not_vacuous() -> None:
    """The README has python blocks, the integration suite exists, and the
    resolver really does read extras: `anthropic` comes only from its extra."""
    assert len(_readme_python_blocks()) >= 3
    assert list((_ROOT / "tests" / "integration").glob("*.py"))
    assert "anthropic" in _installed_by_readme()
    assert "anthropic" not in _requirement_names(
        _PYPROJECT["project"]["optional-dependencies"]["dev"]
    )
    assert _top_level_imports("import anthropic\nimport json\nfrom os import path") == {"anthropic"}


def test_the_router_snippet_passes_the_rubric_text_not_a_name() -> None:
    """`eval_harness.Judge.score` formats `rubric` into the prompt as
    `RUBRIC: {rubric}`; a bare word grades against that word."""
    (router,) = [b for b in _readme_python_blocks() if "JudgeConfidenceSignal(" in b]
    assert "rubric=FAITHFULNESS_RUBRIC" in router
    code = "\n".join(line.split("#", 1)[0] for line in router.splitlines())
    assert not re.search(r"rubric=[\"']", code)
