"""Usage / I/O contract for ``scripts/capture_demo.py`` (#180).

``tests/test_capture_demo_smoke.py`` drives ``main(argv)`` five times, always
with ``--pause-seconds 0`` and a writable tmp ``--output-dir``, and every
assertion is ``rc == 0``. That happy-path-only coverage is why four hostile
seams sat unguarded, the worst of them a ``RuntimeError`` that converted
``scripts/bench_savings.py``'s documented exit **2** (#156) into a traceback at
exit **1**.

The tests here are anchored to the corruption, not to an exception type:

- the bench-failure test asserts the bench's ``rc`` is **preserved** and that
  the bench's own ``::error::`` line survives on stderr — not merely that
  "something didn't raise";
- the ``nan`` test asserts ``time.sleep`` is called zero times, which is the
  actual harm (a clean exit-0 run whose recording has no cue points);
- the pause-validation tests assert the guard fires *before* STAGE 1's banner,
  because the pre-fix crash happened only after the bench had already written
  artifacts.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from tests.test_capture_demo_smoke import _load_capture_module


def _drive(argv: list[str]) -> tuple[int, str]:
    """Run ``capture_demo.main(argv)``, returning ``(rc, stdout)``."""
    capture_demo = _load_capture_module()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = capture_demo.main(argv)
    return rc, buf.getvalue()


def _base_argv(output_dir: Path) -> list[str]:
    return [
        "--no-open",
        "--skip-dashboard-cheatsheet",
        "--output-dir",
        str(output_dir),
    ]


# ----------------------------------------------------------------------
# The bench's exit code must survive the wrapper
# ----------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX directory permissions; chmod is a no-op on Windows"
)
def test_bench_io_failure_propagates_exit_2_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A read-only ``--output-dir`` makes ``bench_savings`` fail its write and
    return the documented 2. Pre-fix, ``_run_bench_into`` raised an uncaught
    ``RuntimeError``: the operator got a traceback, the code was downgraded to
    1, and the bench's clean ``::error::`` line was buried under the stack.
    """
    target = tmp_path / "readonly"
    target.mkdir()
    target.chmod(0o500)
    try:
        rc, out = _drive(["--pause-seconds=0", *_base_argv(target)])
    finally:
        target.chmod(0o700)

    assert rc == 2, (
        "the bench's own exit code must be propagated verbatim — an I/O error "
        f"is a 2, not the findings code 1; got {rc}. stdout:\n{out}"
    )

    err = capsys.readouterr().err
    assert "Traceback" not in err, f"a bench failure must not surface as a traceback; got:\n{err}"
    # The bench already reported the cause; that line is the useful one and it
    # must still be readable rather than buried.
    assert "::error::" in err, f"bench_savings' own ::error:: diagnostic must survive; got:\n{err}"
    # The wrapper says which stage aborted, without restating the cause.
    assert "bench_savings.py exited 2" in err, (
        f"expected a clean [capture] abort line naming the bench's code; got:\n{err}"
    )


def test_run_bench_into_returns_rc_rather_than_raising(tmp_path: Path) -> None:
    """Pin the helper's contract directly: it reports, it does not raise.

    Anchoring only on ``main``'s exit code would let someone reintroduce the
    raise and "fix" it with a broad ``except Exception`` in ``main``, which
    would lose the code again.
    """
    capture_demo = _load_capture_module()
    target = tmp_path / "readonly"
    target.mkdir()
    target.chmod(0o500)
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc, _stdout = capture_demo._run_bench_into(target / "savings_run")
    finally:
        target.chmod(0o700)

    assert rc == 2, f"_run_bench_into must return the bench's rc; got {rc!r}"


# ----------------------------------------------------------------------
# --pause-seconds: `type=float` is not validation
# ----------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "-1", "-0.5"])
def test_non_finite_or_negative_pause_seconds_exits_2_before_stage_1(
    bad: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pre-fix, ``inf`` raised a raw ``OverflowError`` from ``time.sleep``
    *after* STAGE 1 had run the bench, and ``nan`` / negatives silently took no
    pause and exited 0. Both are usage errors and must cost the operator
    nothing."""
    # `--pause-seconds -1` is eaten by argparse as an unknown flag and never
    # reaches the validator; the `=` form is the only one that does.
    rc, out = _drive([f"--pause-seconds={bad}", *_base_argv(tmp_path)])

    assert rc == 2, f"--pause-seconds {bad} should be a usage error (exit 2); got {rc}"
    assert "STAGE 1" not in out, (
        f"--pause-seconds {bad} must be rejected before STAGE 1 runs the bench; stdout:\n{out}"
    )
    assert not list(tmp_path.iterdir()), (
        f"a rejected --pause-seconds must not leave artifacts behind; found "
        f"{[p.name for p in tmp_path.iterdir()]}"
    )

    err = capsys.readouterr().err
    assert "::error::" in err, f"expected a clean ::error:: line on stderr; got:\n{err}"
    assert "--pause-seconds" in err, f"the error must name the offending flag; got:\n{err}"


def test_nan_pause_seconds_takes_no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Anchor on the corruption itself, independent of the exit code.

    ``_pause`` guards ``if seconds > 0`` and ``nan > 0`` is False, so before
    the fix a ``nan`` produced a clean exit-0 run that never paused. The pause
    is the script's stated reason to exist ("cue points"), so that run yields
    an unusable recording. This pins the mechanism that makes the parse-time
    guard necessary.
    """
    capture_demo = _load_capture_module()

    calls: list[float] = []
    monkeypatch.setattr(capture_demo.time, "sleep", lambda s: calls.append(s))

    capture_demo._pause(float("nan"))
    assert calls == [], "nan silently skips the pause — hence the parse-time guard"

    capture_demo._pause(0.25)
    assert calls == [0.25], "a valid pause must still reach time.sleep"


def test_validate_pause_seconds_rejects_bool() -> None:
    """``True`` is an ``int`` worth ``1.0``, so an in-process caller would
    otherwise get a one-second pause it never asked for."""
    capture_demo = _load_capture_module()
    assert capture_demo._validate_pause_seconds(True) is not None
    assert capture_demo._validate_pause_seconds(False) is not None
    assert capture_demo._validate_pause_seconds(0) is None
    assert capture_demo._validate_pause_seconds(2.0) is None


# ----------------------------------------------------------------------
# --output-dir: the two bare write seams
# ----------------------------------------------------------------------


def test_output_dir_that_is_a_file_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bare ``mkdir`` raised ``FileExistsError`` as a raw traceback at exit 1."""
    target = tmp_path / "not_a_dir"
    target.write_text("", encoding="utf-8")

    rc, out = _drive(["--pause-seconds=0", *_base_argv(target)])

    assert rc == 2, f"an --output-dir that is a file should exit 2; got {rc}"
    assert "STAGE 1" not in out, "the mkdir guard must fire before STAGE 1 runs"
    err = capsys.readouterr().err
    assert "::error::" in err, f"expected a clean ::error:: line on stderr; got:\n{err}"
    assert str(target) in err, f"the error must name the offending path {target}; got:\n{err}"


def test_output_dir_under_a_file_parent_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bare ``mkdir`` raised ``NotADirectoryError`` as a raw traceback at exit 1."""
    parent = tmp_path / "file_parent"
    parent.write_text("", encoding="utf-8")
    target = parent / "sub"

    rc, _ = _drive(["--pause-seconds=0", *_base_argv(target)])

    assert rc == 2, f"an --output-dir under a file parent should exit 2; got {rc}"
    err = capsys.readouterr().err
    assert "::error::" in err, f"expected a clean ::error:: line on stderr; got:\n{err}"
    assert str(target) in err, f"the error must name the offending path {target}; got:\n{err}"


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX file permissions; chmod is a no-op on Windows"
)
def test_unwritable_stable_target_exits_2_at_the_copy_seam(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An existing-but-unwritable stable artifact leaves the *directory* fine,
    so it survives ``mkdir(exist_ok=True)`` and fails at ``shutil.copy2``
    instead — after the bench has already run. A genuinely separate seam,
    reached by an input the mkdir guard accepts.
    """
    stable = tmp_path / "savings_demo.md"
    stable.write_text("", encoding="utf-8")
    stable.chmod(0o400)
    try:
        rc, out = _drive(["--pause-seconds=0", *_base_argv(tmp_path)])
    finally:
        stable.chmod(0o600)

    assert rc == 2, f"an unwritable stable target should exit 2; got {rc}"
    # If STAGE 1 stopped reaching the copy, this test would silently stop
    # exercising the seam it was written for.
    assert "STAGE 1" in out, f"the copy seam is reached only after the bench runs; stdout:\n{out}"
    err = capsys.readouterr().err
    assert "::error::" in err, f"expected a clean ::error:: line on stderr; got:\n{err}"
    assert "Traceback" not in err, f"must not surface as a traceback; got:\n{err}"


def test_valid_invocation_still_exits_0(tmp_path: Path) -> None:
    """The guards must not change any valid run; the smoke tests cover the
    output in detail, this pins that adding them cost nothing."""
    rc, out = _drive(["--pause-seconds=0", *_base_argv(tmp_path)])
    assert rc == 0, f"a valid invocation must still exit 0; stdout:\n{out}"
    assert (tmp_path / "savings_demo.md").exists()
    assert (tmp_path / "savings_demo.json").exists()
