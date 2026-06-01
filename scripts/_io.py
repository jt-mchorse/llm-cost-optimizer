"""Backwards-compat re-export of the package-level atomic-write helper.

Historically lived here so ``bench_savings.py`` and ``tune_threshold.py``
could write atomically. With #50 the runtime layer also needs the same
helper (``PromptCacheWrapper.dump_aggregate_json``), so the canonical
home moved to ``cost_optimizer/io_utils.py``. This module re-exports the
function under its existing name to keep existing call sites stable.
"""

from __future__ import annotations

from cost_optimizer.io_utils import atomic_write_text

__all__ = ["atomic_write_text"]
