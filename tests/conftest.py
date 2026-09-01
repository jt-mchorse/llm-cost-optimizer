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
