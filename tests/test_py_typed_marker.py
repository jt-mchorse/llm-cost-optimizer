"""PEP 561 packaging lock for ``cost_optimizer`` (#127).

``cost_optimizer`` is a type-hinted package (6 of 7 modules carry
annotations) and the README markets it as embeddable inside other
portfolio repos. Under PEP 561 those annotations are only visible to a
downstream type-checker (mypy/pyright) if the distribution ships a
``py.typed`` marker file inside the package *and* the wheel actually
contains it. Without the marker, ``import cost_optimizer`` is treated as
untyped and every downstream consumer silently loses the types.

Two orthogonal axes lock the fix so a future refactor can't regress it:

1. ``py.typed`` is discoverable as a package resource of the installed
   ``cost_optimizer`` package (deleting the file fails this).
2. ``pyproject.toml`` declares the ``Typing :: Typed`` trove classifier
   (the metadata half of the PEP 561 contract).

Wheel inclusion itself was verified firsthand at implementation time:
``python -m build --wheel`` emits ``cost_optimizer/py.typed`` into the
wheel because hatchling includes tracked files under a wheel-target
package by default (``[tool.hatch.build.targets.wheel] packages``). That
default needs no per-file config, so it is asserted here via the
resource lookup rather than an in-CI wheel build (which would add a
``build`` dependency and network to every run).
"""

from __future__ import annotations

import tomllib
from importlib.resources import files
from pathlib import Path


def test_py_typed_is_a_packaged_resource() -> None:
    marker = files("cost_optimizer").joinpath("py.typed")
    assert marker.is_file(), (
        "cost_optimizer/py.typed is missing — downstream type-checkers "
        "will treat the package as untyped (PEP 561)."
    )


def test_pyproject_declares_typed_classifier() -> None:
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    classifiers = data["project"]["classifiers"]
    assert "Typing :: Typed" in classifiers, (
        "pyproject.toml must declare the 'Typing :: Typed' classifier "
        "alongside the py.typed marker (PEP 561 metadata half)."
    )
