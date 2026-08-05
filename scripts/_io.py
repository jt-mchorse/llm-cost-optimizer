"""Shared output-path helpers for the artifact-generating scripts.

``atomic_write_text`` historically lived here so ``bench_savings.py`` and
``tune_threshold.py`` could write atomically. With #50 the runtime layer
also needs the same helper (``PromptCacheWrapper.dump_aggregate_json``),
so the canonical home moved to ``cost_optimizer/io_utils.py``. This
module re-exports the function under its existing name to keep existing
call sites stable, and owns ``resolve_out_stem``, which both scripts use
to turn their ``--out`` flag into a suffixable path.
"""

from __future__ import annotations

from pathlib import Path

from cost_optimizer.io_utils import atomic_write_text

__all__ = ["atomic_write_text", "resolve_out_stem"]


def resolve_out_stem(raw: str) -> Path:
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

    Nothing else about ``--out`` semantics changes — in particular a stem
    that already carries a suffix is returned untouched, so
    ``with_suffix``'s existing replace-the-suffix behavior (and
    ``bench_1000_doc``-style no-op-suffix handling at the call sites) is
    preserved.

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
    return stem
