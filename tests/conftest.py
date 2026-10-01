"""Shared test helpers.

Currently one: `bounded_work`, a guard that fails a block which does not
terminate, instead of letting it hang the suite.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

#: Default bail-out for `bounded_work`, in Python trace events.
#:
#: Calibrated from **both** ends, and the upper one is the load-bearing half.
#:
#: Lower bound (don't fire on correct code): the walks this guards cost 21-69
#: events for the payloads in the suite, and ~72,000 for a 10,000-element
#: nested structure. 50,000 is ~700x the largest case actually wrapped today.
#:
#: Upper bound (fire *before* the runaway kills the runner): the defect this
#: exists for (#203) is a walk whose frames each push a longer `path` string, so
#: it consumes memory roughly linearly in events — measured at **1.0 GB by
#: 200,000 events** and 5.7 GB within four untraced seconds. A generous-looking
#: limit is therefore not the safe choice: at two million events the process is
#: OOM-killed before the guard ever runs, and pytest prints nothing at all. The
#: limit must sit far enough below the memory cliff that the AssertionError wins
#: the race. 50,000 events is ~250 MB.
#:
#: The bound is on *work done*, not wall-clock, deliberately: a timeout would
#: turn a logic bug into a flake on a loaded CI box, and would report a
#: non-terminating loop as "slow" rather than as "wrong".
#:
#: Pass `limit=` if you wrap something legitimately larger — but re-check the
#: memory arithmetic above before raising it much.
DEFAULT_TRACE_EVENT_LIMIT = 50_000


@pytest.fixture
def bounded_work() -> Callable[..., Any]:
    """Return a context manager that fails if the block runs unboundedly.

    Usage::

        with bounded_work():
            with pytest.raises(ValueError):
                _validate_payload(cyclic)

    The `AssertionError` is raised from inside the traced frame, so it
    surfaces as an ordinary test failure naming the guard, rather than as a
    job that CI eventually kills.
    """

    @contextlib.contextmanager
    def _guard(limit: int = DEFAULT_TRACE_EVENT_LIMIT, what: str = "block") -> Iterator[None]:
        state = {"count": 0}

        def _trace(frame: Any, event: str, arg: Any) -> Any:
            state["count"] += 1
            if state["count"] > limit:
                raise AssertionError(
                    f"{what} did not terminate: still running after {limit:,} trace events."
                )
            return _trace

        previous = sys.gettrace()
        sys.settrace(_trace)
        threading.settrace(_trace)
        try:
            yield
        finally:
            sys.settrace(previous)
            threading.settrace(previous)  # type: ignore[arg-type]

    return _guard


# ---------------------------------------------------------------------------
# One canonical bench payload per session (#233)
# ---------------------------------------------------------------------------
#
# D-022 added `--durations 10` to CI and deferred acting on the slow tail until
# it produced data. It did: on main run 36535471170 the ten slowest tests were
# 17.3-17.8 s each on 3.11 and 35.1-35.7 s each on 3.12, and every one of them is
# the same computation -- a full 500-row `run_bench`. Locally, 27 tests over
# 0.5 s were 35.7 s of a ~38 s suite at ~1.4 s per bench: one deterministic
# payload recomputed 27 times. The tail looked flat because it was one thing.
#
# `run_bench` is pure in `(n, seed)` -- the two-call determinism arm in
# `test_bench_savings.py` stays uncached to keep that premise honest, and
# `test_the_cached_payload_is_a_fresh_run` compares the cache with a real call.

_CANONICAL_BENCH_KEY = (500, 0xC057)


@pytest.fixture(scope="session")
def canonical_bench_session() -> dict[str, Any]:
    from scripts.bench_savings import run_bench

    n, seed = _CANONICAL_BENCH_KEY
    return run_bench(n=n, seed=seed)


@pytest.fixture
def canonical_bench_payload(canonical_bench_session: dict[str, Any]) -> dict[str, Any]:
    """The `run_bench(n=500)` payload, deep-copied so a test may mutate it."""
    import copy

    return copy.deepcopy(canonical_bench_session)


class _BenchCalls:
    """What `memoized_bench` served: `cached` from the session payload, `real`
    from a genuine `run_bench` call."""

    def __init__(self) -> None:
        self.cached = 0
        self.real = 0


@pytest.fixture
def memoized_bench(
    monkeypatch: pytest.MonkeyPatch, canonical_bench_session: dict[str, Any]
) -> _BenchCalls:
    """Route `scripts.bench_savings.run_bench` through the session payload.

    For tests that reach the bench through `main`, directly or via
    `capture_demo`. Only the canonical `(n, seed)` is served from the cache;
    anything else runs for real, so a test that varies the workload still
    measures it.

    Two hooks, because `capture_demo._import_bench_main` deletes
    `scripts.bench_savings` from `sys.modules` and re-imports it on every call:
    a patch on the already-imported module object never reaches that fresh
    copy. So `importlib.import_module` is wrapped too, and patches the fresh
    module as it is created. `capture_demo` calls `importlib.import_module`
    through the shared `importlib` module object, which is why this reaches it.
    """
    import copy
    import importlib

    import scripts.bench_savings as bench

    calls = _BenchCalls()

    def _memoize(module: Any) -> None:
        real = module.run_bench

        def run_bench(*, n: int = 500, seed: int = 0xC057) -> dict[str, Any]:
            if (n, seed) == _CANONICAL_BENCH_KEY:
                calls.cached += 1
                return copy.deepcopy(canonical_bench_session)
            calls.real += 1
            return real(n=n, seed=seed)

        monkeypatch.setattr(module, "run_bench", run_bench)

    _memoize(bench)
    real_import = importlib.import_module

    def import_module(name: str, package: str | None = None) -> Any:
        module = real_import(name, package)
        if name == "scripts.bench_savings":
            _memoize(module)
        return module

    monkeypatch.setattr(importlib, "import_module", import_module)
    return calls
