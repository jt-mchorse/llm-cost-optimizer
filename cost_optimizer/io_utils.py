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

# Cap the target basename's contribution to the temp filename. The temp name is
# ``.<base>.<random>.tmp``; the affixes add ~13-20 bytes, so prepending a full
# basename that is itself near NAME_MAX (255 on ext4/APFS) overflows the limit
# and the write fails with ``OSError: [Errno 63] File name too long`` — even
# though a plain ``Path.write_text`` of that same target succeeds (sibling of
# rag-production-kit#128 and mcp-server-cookbook#96). The base in the temp name
# is cosmetic (``ls``-ability); uniqueness comes from ``NamedTemporaryFile``'s
# random component, so truncating it is safe. Budget is in BYTES (NAME_MAX is a
# byte limit) and we trim on a char boundary so multibyte names are never split
# mid-codepoint.
_MAX_TEMP_BASE_BYTES = 200


def _cap_base_for_temp(base: str) -> str:
    if len(base.encode("utf-8")) <= _MAX_TEMP_BASE_BYTES:
        return base
    out = base
    while out and len(out.encode("utf-8")) > _MAX_TEMP_BASE_BYTES:
        out = out[:-1]
    return out


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
            prefix=f".{_cap_base_for_temp(target.name)}.",
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
