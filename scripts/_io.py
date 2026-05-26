"""Atomic write helper shared between `bench_savings.py` and `tune_threshold.py`.

`Path.write_text` is not atomic: SIGINT/SIGTERM/disk-full/OOM between
the implicit `open(..., "w")` truncate and `close()` flush leaves the
destination zero-length or partial. Downstream consumers — the
streamlit dashboard reading `docs/savings.json`, GitHub's inline
render of `docs/savings.md` in the README, the operator's plot
regeneration off `tune_threshold`'s JSON — then see corrupt artifacts.

Pattern mirrors `llm-eval-harness/eval_harness/cli.py::_atomic_write_text`
(filed there as #48). Keeping the shape uniform across the portfolio
makes the property easy to recognize when reviewing other repos.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str) -> None:
    # Write to a sibling temp file in the destination's parent
    # directory, fsync, then `os.replace` (atomic on POSIX within the
    # same filesystem). Same-directory placement is load-bearing — it
    # guarantees same filesystem so the rename cannot fall back to a
    # copy. On any exception between the temp write and the rename,
    # the temp is unlinked.
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
