"""One file must be one module (#201).

`scripts/tune_threshold.py` was imported by this suite under two different
names — `tune_threshold` (after a `sys.path` insert of `scripts/`) and
`scripts.tune_threshold`. Python treats those as unrelated modules: the file
executes twice, gets two `__dict__`s, and a `monkeypatch.setattr` on one is
invisible to the other.

Measured before the fix, in one interpreter:

    tune_threshold is scripts.tune_threshold            False
    tune_threshold.main is scripts.tune_threshold.main  False
    same __file__                                       True
    sys.modules keys  ['tune_threshold', 'scripts.tune_threshold']
    marker set on one visible via the other             False

Nothing was failing, because `test_main_plot_write_oserror_exits_two`
monkeypatched the copy it had itself imported, and `tests/test_atomic_write.py`
— which imports the *other* copy's `main` — never patches. That is a property
of which tests exist, not one anyone enforced.

It is also exactly what mypy's "Source file found twice under different module
names: `_io` and `scripts._io`" was reporting, which is why `mypy
cost_optimizer scripts` stopped before checking anything ("errors prevented
further checking") and `scripts/` sat outside the gate. The error was a true
finding, not a layout quirk to configure away — the same conclusion
chunking-strategies-lab reached in its D-014, which this repo follows rather
than solving the identical collision a second way.

Structure mirrors csl's `tests/test_run_matrix_single_module_identity.py`, generalized
to every module in `scripts/` rather than the one that happened to be duplicated.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS = _REPO_ROOT / "tests"
_SCRIPTS = _REPO_ROOT / "scripts"

#: Every module under `scripts/`, discovered rather than listed — a hand-written
#: list would go stale the first time a script is added, which is the failure
#: mode this whole issue is an instance of.
_SCRIPT_MODULES = sorted(p.stem for p in _SCRIPTS.glob("*.py"))

#: The one spelling: package-qualified. It needs no `sys.path` mutation, it is
#: what the majority of the suite already used, and it is what
#: `scripts/capture_demo.py`'s own `_import_bench_main()` uses.
_CANONICAL_PREFIX = "scripts."


def _is_cli(path: Path) -> bool:
    """True when *path* is runnable, i.e. it has an ``if __name__ == "__main__"`` guard.

    Discovered, not listed: `scripts/_io.py` is a shared helper with no CLI
    (it owns `resolve_out_stem` and re-exports `atomic_write_text` from
    `cost_optimizer/io_utils.py`), so `--help` prints nothing and asserting a
    usage line on it would be asserting the wrong contract.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "__name__"
        ):
            return True
    return False


#: The runnable subset of `_SCRIPT_MODULES`.
_CLI_MODULES = sorted(p.stem for p in _SCRIPTS.glob("*.py") if _is_cli(p))


def _names_reaching_scripts(path: Path) -> set[str]:
    """Every module name *path* reaches a `scripts/` module by.

    Returns the raw spelling, so `tune_threshold` and `scripts.tune_threshold`
    are distinguishable — that distinction is the whole point.
    """
    found: set[str] = set()
    candidates = {m for m in _SCRIPT_MODULES} | {_CANONICAL_PREFIX + m for m in _SCRIPT_MODULES}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # `from scripts import tune_threshold` reaches it by the qualified
            # name even though `node.module` is just "scripts".
            if node.module == "scripts":
                found.update(
                    _CANONICAL_PREFIX + a.name for a in node.names if a.name in _SCRIPT_MODULES
                )
            elif node.module in candidates:
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in candidates:
                    found.add(alias.name)
    return found


def test_the_suite_imports_each_script_by_exactly_one_name() -> None:
    by_module: dict[str, set[str]] = {}
    for path in sorted(_TESTS.glob("test_*.py")):
        for name in _names_reaching_scripts(path):
            stem = name.rsplit(".", 1)[-1]
            by_module.setdefault(stem, set()).add(name)

    # Anti-vacuous: an AST walk that matched nothing would satisfy every
    # assertion below. Several test files really do import these.
    assert by_module, "no test file imports any scripts/ module — this guard found nothing"
    assert set(by_module) >= {"bench_savings", "tune_threshold"}, (
        f"expected the two artifact-producing scripts to be under test; saw {sorted(by_module)}"
    )

    offenders = {stem: sorted(names) for stem, names in by_module.items() if len(names) > 1}
    assert offenders == {}, (
        f"these scripts are imported under more than one module name: {offenders}. "
        "Python makes each name a separate module object, so a monkeypatch on "
        "one cannot reach the other."
    )
    bare = {stem: sorted(names) for stem, names in by_module.items() if stem in by_module[stem]}
    assert bare == {}, f"these scripts are imported bare rather than as scripts.<name>: {bare}"


def _mutates_sys_path_with_scripts(path: Path) -> bool:
    """True when *path* **calls** `sys.path.insert/append` mentioning "scripts".

    Matched structurally, not by substring: a substring search finds its own
    needle in this file's source and reports the guard as the offender. Walking
    the AST looks for a real call, so the equivalent code inside the string
    literal handed to a subprocess below is correctly not a match.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"insert", "append"}:
            continue
        target = node.func.value
        is_sys_path = (
            isinstance(target, ast.Attribute)
            and target.attr == "path"
            and isinstance(target.value, ast.Name)
            and target.value.id == "sys"
        )
        if is_sys_path and "scripts" in ast.dump(node):
            return True
    return False


def test_no_test_file_puts_the_scripts_dir_on_sys_path() -> None:
    """The mechanism that made a bare `tune_threshold` importable at all.

    Leaving it behind would let the duplicate return without any import
    statement changing: `scripts/` stays on `sys.path` for the rest of the
    session, so a later bare `import tune_threshold` anywhere would resolve.

    `tests/test_capture_demo_smoke.py` was the last holdout, and its stated
    reason was wrong — it claimed to mirror `capture_demo._import_bench_main()`,
    which inserts the **repo root** and imports `scripts.bench_savings`.
    """
    offenders = [
        path.name
        for path in sorted(_TESTS.glob("test_*.py"))
        if _mutates_sys_path_with_scripts(path)
    ]
    assert offenders == [], f"these files re-add scripts/ to sys.path: {offenders}"


def test_importing_it_both_ways_really_does_produce_two_modules() -> None:
    """Pins *why* the rules above exist, rather than asserting them twice.

    Run in a subprocess so this test cannot leave a second copy of the module
    in this session's `sys.modules` — which would be the very hazard it
    documents.
    """
    code = (
        "import sys;"
        f"sys.path.insert(0, r'{_REPO_ROOT}');"
        f"sys.path.insert(0, r'{_SCRIPTS}');"
        "import tune_threshold, scripts.tune_threshold as s;"
        "print(tune_threshold is s, tune_threshold.main is s.main)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=_REPO_ROOT
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False False", (
        "the two import spellings resolved to the same module object; if Python "
        "ever unified them, the guards above are no longer load-bearing and "
        "this test's rationale needs rewriting"
    )


@pytest.mark.parametrize("module", _CLI_MODULES)
@pytest.mark.parametrize("style", ["direct-path", "dash-m"])
def test_both_invocation_styles_still_work(module: str, style: str) -> None:
    """#201 asks for this explicitly: the resolution must not break either one."""
    argv = [f"scripts/{module}.py"] if style == "direct-path" else ["-m", f"scripts.{module}"]
    proc = subprocess.run(
        [sys.executable, *argv, "--help"], capture_output=True, text=True, cwd=_REPO_ROOT
    )
    assert proc.returncode == 0, f"{style} {module} failed:\n{proc.stdout}{proc.stderr}"
    assert "usage:" in proc.stdout, f"{style} {module} did not print a real usage line"


def test_the_cli_discovery_found_the_artifact_scripts() -> None:
    """Anti-vacuous arm for `_CLI_MODULES`.

    An `_is_cli` that matched nothing would make the parametrized invocation
    test above collect zero cases and pass silently — the "`Test Files` is not
    `Tests`" failure mode, one level down.
    """
    assert set(_CLI_MODULES) >= {"bench_savings", "tune_threshold"}, _CLI_MODULES
    assert "_io" not in _CLI_MODULES, "the shared helper has no CLI; discovery is too loose"


def test_scripts_has_no_init_py() -> None:
    """The resolution deliberately taken, recorded as an assertion.

    An `scripts/__init__.py` is the other option mypy's own error message
    suggests. It would not have removed the duplicate — only the import
    normalization does that — and it changes how `python scripts/bench_savings.py`
    resolves. If a later change wants it, this is where that argument gets re-made.
    """
    assert not (_SCRIPTS / "__init__.py").exists()
    assert (_SCRIPTS / "bench_savings.py").is_file()
