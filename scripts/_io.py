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

import os
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

    **A directory is not a stem (#239).** #174 scoped its guard to "exactly
    the set ``with_suffix`` rejects" -- the inputs that *crash*. ``run1/``
    and ``run1/.`` do not crash: pathlib drops the trailing separator and
    the ``.``, so ``Path("run1/").name`` is ``"run1"`` and the artifacts
    landed *beside* the directory the operator named, at exit 0, on top of
    any other script's ``run1.json``. The final component of the string as
    typed is checked, before pathlib normalises it away. ``..`` keeps #174's
    reading: ``...json`` is an odd but coherent filename.

    **Two stems must not share a name (#239).** D-023 made one stem's names
    agree with each other; it never asked whether two *different* stems can
    produce the same name. They can whenever one tail ends with another:
    ``bench_savings`` writes ``.json`` and ``_workload.json``, so stem ``S``
    + ``_workload.json`` *is* stem ``S_workload`` + ``.json``, and the second
    run overwrote the first run's workload record with a results file of
    another schema. So ``artifact_suffixes`` is every tail the script
    appends -- only those that are a ``Path.suffix`` can be stripped, which
    ``_workload.json`` never is -- and a stem ending in the difference of
    two nested tails is refused. Derived from the tails, so a script that
    adds a fourth inherits the rule.

    Raises:
        ValueError: when ``raw`` has no filename component to suffix, names
        a directory, or ends in a part of one tail that another tail
        completes.
    """
    stem = Path(raw)
    if stem.name == "":
        raise ValueError(
            f"--out must be a path stem with a filename component; got {raw!r}. "
            "The script appends the artifact suffixes itself, e.g. "
            "`--out docs/savings` writes docs/savings.json and docs/savings.md."
        )
    if os.path.basename(raw) in ("", "."):
        raise ValueError(
            f"--out must end in a filename stem, not a directory; got {raw!r}, which "
            f"would write {stem.name}.* beside that directory rather than into it. "
            f"Name the stem inside it, e.g. `--out {os.path.normpath(os.path.join(raw, 'NAME'))}`."
        )
    if stem.suffix in artifact_suffixes:
        stem = stem.with_suffix("")
    for prefix in _colliding_stem_endings(artifact_suffixes):
        if stem.name.endswith(prefix):
            raise ValueError(
                f"--out stem {stem.name!r} ends in {prefix!r}, so its files would "
                f"collide with those of stem {stem.name[: -len(prefix)]!r} -- one "
                f"stem's '{prefix}' + tail is the other's whole name. Choose a stem "
                f"that does not end in {prefix!r}."
            )
    return stem


def _colliding_stem_endings(tails: Collection[str]) -> list[str]:
    """Every ``x`` with ``x + short == long`` for two tails the script writes.

    A stem ending in ``x`` produces, through ``short``, the same name another
    stem produces through ``long`` (#239). Sorted so the message is stable.
    """
    return sorted(
        {
            long[: -len(short)]
            for long in tails
            for short in tails
            if long != short and long.endswith(short)
        }
    )


def artifact_path(stem: Path, suffix: str) -> Path:
    """``stem`` + ``suffix``, appended rather than substituted (#231, D-023).

    ``Path.with_suffix`` would replace a dot that is part of the stem
    (``savings.small`` -> ``savings.json``). For a stem with no suffix the
    two agree, so the documented ``--out docs/savings`` is unchanged.
    ``suffix`` may be a full tail such as ``"_workload.json"``, which is how
    #176's sidecar shares the stem with its two siblings.
    """
    return stem.with_name(stem.name + suffix)
