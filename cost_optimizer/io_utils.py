"""Package-level atomic-write helper.

Promoted from ``scripts/_io.py`` for use by the runtime layer (the
``cache_wrapper``'s ``dump_aggregate_json`` writes through this) without
the runtime layer needing to import from ``scripts/`` — that package is
operator-facing tooling, not library-public API. Mirrors the layout
decision D-015 took in ``llm-eval-harness`` (atomic-write helpers live
at the package level, not file-private).

``scripts/_io.py`` re-exports from here so existing call sites in
``bench_savings.py`` / ``tune_threshold.py`` keep working unchanged.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    ``Path.write_text`` is not atomic: SIGINT/SIGTERM/disk-full/OOM
    between the implicit ``open(..., "w")`` truncate and ``close()`` flush
    leaves the destination zero-length or partial. The runtime
    ``dump_aggregate_json`` writer hits this same hazard the dashboard
    consumers already dodge, so it routes through the same helper.

    Pattern: write to a sibling temp file in the destination's parent
    directory, fsync, then ``os.replace`` (atomic on POSIX within the
    same filesystem). Same-directory placement is load-bearing —
    guarantees same filesystem so the rename cannot fall back to a
    copy. On any exception between the temp write and the rename,
    the temp is unlinked.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
