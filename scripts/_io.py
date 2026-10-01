"""Shared output-path helpers for the artifact-generating scripts.

``atomic_write_text`` historically lived here so ``bench_savings.py`` and
``tune_threshold.py`` could write atomically. With #50 the runtime layer
also needs the same helper (``PromptCacheWrapper.dump_aggregate_json``),
so the canonical home moved to ``cost_optimizer/io_utils.py``. This
module re-exports the function under its existing name to keep existing
call sites stable, and owns ``resolve_out_stem`` / ``artifact_path``, which
every artifact script uses to turn its ``--out`` flag into filenames.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

from cost_optimizer.io_utils import atomic_write_text

__all__ = ["artifact_path", "atomic_write_text", "resolve_out_stem"]


def resolve_out_stem(raw: str, *, artifact_suffixes: Collection[str]) -> Path:
    """Turn an operator-supplied ``--out`` *stem* into a suffixable ``Path``.

    Both artifact scripts take a stem and derive the real filenames with
    ``Path.with_suffix(".json")`` / ``.md`` / ``.png``. ``with_suffix``
    raises ``ValueError`` when the path has no filename component —
    ``Path("")``, ``Path(".")`` and ``Path("/")`` all qualify — and it does
    so *outside* the write-seam ``try``, so a stemless ``--out`` escaped
    ``main`` as a raw traceback at exit 1 (#174). That is the same
    "success"-range escape the write-seam ``OSError`` guards were added to
    close, for an adjacent kind of unusable ``--out``.

    Raising here, at argument-handling time, also means the failure lands
    *before* the work: ``tune_threshold`` used to run its whole sweep and
    only then crash on the suffix.

    **Only an artifact suffix is stripped (#231, D-023).** This used to
    return any suffixed stem untouched so that ``with_suffix`` could
    replace the suffix -- which is what made ``--out docs/savings.json``
    mean ``docs/savings``, and that intent is kept. But ``with_suffix``
    replaces *whatever* follows the last dot, so ``--out docs/savings.small``
    wrote ``docs/savings.json``/``.md`` -- clobbering the canonical run --
    while #176's workload sidecar, built from ``.name``, kept the dot and
    went to ``savings.small_workload.json``. Two derivations, one stem,
    three names that disagreed on any dotted stem. Now a suffix is stripped
    only when it is one the calling script writes (``artifact_suffixes``),
    every other dot is part of the stem, and every name is built by
    ``artifact_path`` from the one stem that comes back.

    Keyword-only and required: a default would let a new script inherit
    "strip nothing" and quietly bring back the two-derivation split.

    Raises:
        ValueError: when ``raw`` has no filename component to suffix.
    """
    stem = Path(raw)
    if stem.name == "":
        raise ValueError(
            f"--out must be a path stem with a filename component; got {raw!r}. "
            "The script appends the artifact suffixes itself, e.g. "
            "`--out docs/savings` writes docs/savings.json and docs/savings.md."
        )
    if stem.suffix in artifact_suffixes:
        stem = stem.with_suffix("")
    return stem


def artifact_path(stem: Path, suffix: str) -> Path:
    """``stem`` + ``suffix``, appended rather than substituted (#231, D-023).

    ``Path.with_suffix`` would replace a dot that is part of the stem
    (``savings.small`` -> ``savings.json``). For a stem with no suffix the
    two agree, so the documented ``--out docs/savings`` is unchanged.
    ``suffix`` may be a full tail such as ``"_workload.json"``, which is how
    #176's sidecar shares the stem with its two siblings.
    """
    return stem.with_name(stem.name + suffix)
