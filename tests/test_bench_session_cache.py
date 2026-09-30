"""The session bench cache is a cache, not a substitute (#233).

`tests/conftest.py` computes `run_bench(n=500, seed=0xC057)` once per session
because 27 tests were recomputing that one deterministic payload -- 886 s of
the 3.12 CI job. These arms keep the cache from hiding anything: it must equal
a fresh run, hand out independent copies, actually be hit by the capture
pipeline it was built for, and step aside for any other workload.
"""

from __future__ import annotations

import importlib
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.bench_savings import run_bench  # noqa: E402


def test_the_cached_payload_is_a_fresh_run(canonical_bench_session: dict[str, Any]) -> None:
    """Uncached, on purpose: the one extra real bench the suite pays for."""
    assert run_bench(n=500, seed=0xC057) == canonical_bench_session


def test_each_test_gets_its_own_copy(
    canonical_bench_payload: dict[str, Any], canonical_bench_session
) -> None:
    canonical_bench_payload["strategies"].clear()
    canonical_bench_payload["mutated"] = True
    assert canonical_bench_session["strategies"]
    assert "mutated" not in canonical_bench_session


def test_the_capture_pipeline_is_served_from_the_cache(tmp_path: Path, memoized_bench) -> None:
    """Through `capture_demo`, whose `_import_bench_main` re-imports
    `scripts.bench_savings` fresh: the import hook is what reaches it."""
    if "scripts.capture_demo" in sys.modules:
        del sys.modules["scripts.capture_demo"]
    capture_demo = importlib.import_module("scripts.capture_demo")
    with redirect_stdout(io.StringIO()):
        rc = capture_demo.main(
            ["--output-dir", str(tmp_path / "out"), "--no-open", "--skip-dashboard-cheatsheet"]
        )
    assert rc == 0
    assert memoized_bench.cached >= 1
    assert memoized_bench.real == 0


def test_any_other_workload_runs_for_real(memoized_bench) -> None:
    import scripts.bench_savings as bench

    payload = bench.run_bench(n=20, seed=1)
    assert payload["n_rows"] == 20
    assert (memoized_bench.cached, memoized_bench.real) == (0, 1)
