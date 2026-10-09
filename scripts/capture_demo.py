#!/usr/bin/env python3
"""Deterministic capture orchestrator for the llm-cost-optimizer 60-second demo.

Sequences the two demo flows from the README's "Demo" section under
explicit stage banners and a configurable inter-stage pause so a screen
recorder can re-capture the demo over and over and land on the same
frames every time.

Stages:

- **STAGE 1 (auto, hermetic).** Calls `scripts.bench_savings.main(["--dry",
  ...])` in-process, prints the rendered five-strategy savings table,
  and copies the freshly-rendered `savings.md` to a stable artifact
  path under `docs/demo-artifacts/` so the recorder's text viewer can
  be pre-positioned.
- **STAGE 2 (operator-action).** Prints a cheat-sheet for the
  Streamlit dashboard tour: the exact launch command, the URL the
  browser opens, and a numbered checklist of what to click (strategy
  summary → cumulative-savings chart → comparison view). Streamlit
  isn't auto-launched by default — it spawns a long-running server
  that can't run hermetically in CI — but `--launch-streamlit`
  subprocess-spawns it for the operator's convenience when running
  the recording.

Usage:

    python scripts/capture_demo.py [--pause-seconds 2.0] [--no-open]
                                   [--output-dir docs/demo-artifacts]
                                   [--launch-streamlit]
                                   [--skip-dashboard-cheatsheet]

Closes the AC3 row on #18 ("Capture script committed under scripts/
so the demo can be re-captured deterministically"). AC1 (committed
GIF/MP4) and AC2 (README embed) remain operator-only.

Locked by `tests/test_capture_demo_smoke.py`. Same hermetic contract
as `tests/test_bench_savings.py` — no API key, no live network, stub
client only.
"""

from __future__ import annotations

import argparse
import importlib
import io
import math
import re
import shlex
import shutil
import subprocess
import sys
import time
import webbrowser
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "demo-artifacts"

# Stable filenames for the artifacts copied out of the bench's `--out`
# tempdir into the gitignored stable destination. The recorder's
# pre-positioned terminal / text viewer opens these paths, so they
# must stay constant across re-captures.
STABLE_SAVINGS_MD = "savings_demo.md"
STABLE_SAVINGS_JSON = "savings_demo.json"
# Where a --launch-streamlit child writes its output (#259).
STREAMLIT_LOG = "streamlit.log"

DASHBOARD_URL = "http://localhost:8501"


def _fail(message: str) -> int:
    """Print a clean ``::error::`` line to stderr and return exit code 2.

    Same shape `scripts/bench_savings.py` and `scripts/tune_threshold.py`
    already use (#156, #160). This script is an entry point too — `main(argv)
    -> int` under `raise SystemExit(main())` — so it owes the operator the same
    `0 = clean / 1 = findings / 2 = I/O or usage error` contract (#180).
    """
    print(f"::error::{message}", file=sys.stderr)
    return 2


def _banner(stage: int, title: str) -> str:
    line = "=" * 72
    return f"\n{line}\n  STAGE {stage}  {title}\n{line}\n"


def _validate_pause_seconds(seconds: float) -> str | None:
    """Return an error message for an unusable ``--pause-seconds``, else ``None``.

    ``type=float`` is not validation, and both directions of the unguarded
    domain were live (#180):

    - ``inf`` reached ``time.sleep`` and raised a raw ``OverflowError``, and it
      fired from ``_pause`` — i.e. *after* STAGE 1 had already run the bench
      and written artifacts, so the operator got a half-finished capture and a
      traceback.
    - ``nan`` and negatives were the quiet half: ``_pause`` guards
      ``if seconds > 0`` and ``nan > 0`` is ``False``, so the run exited 0
      having paused nowhere. The inter-stage pause is the script's stated
      reason to exist ("cue points"), so that clean-looking run produces an
      unusable recording with no diagnostic at all.

    ``bool`` is excluded because ``True`` is an ``int`` worth ``1.0`` and would
    otherwise silently become a one-second pause for an in-process caller.
    """
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return f"--pause-seconds must be a number; got {seconds!r}"
    if math.isnan(seconds) or math.isinf(seconds):
        return f"--pause-seconds must be finite; got {seconds!r}"
    if seconds < 0:
        return f"--pause-seconds must be >= 0; got {seconds!r}"
    return None


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _import_bench_main():
    """Fresh-import `scripts.bench_savings` and return its `main` callable.

    Mirrors the path-bootstrapping pattern in `tests/test_bench_savings.py`
    (which adds the repo root to `sys.path`, then `from scripts.bench_savings
    import ...`). Fresh-imports avoid stale module state between
    in-process invocations.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    if "scripts.bench_savings" in sys.modules:
        del sys.modules["scripts.bench_savings"]
    mod = importlib.import_module("scripts.bench_savings")
    if not hasattr(mod, "main"):
        raise RuntimeError("scripts/bench_savings.py must expose a `main()` callable")
    return mod.main


def _run_bench_into(tmp_out_stem: Path) -> tuple[int, str]:
    """Run `bench_savings.main` against a tmp `--out` stem; return `(rc, stdout)`.

    The bench script writes three artifacts next to the stem
    (`<stem>.json`, `<stem>.md`, `<stem>_workload.json`) and prints a
    per-strategy summary line per row to stdout. The capture
    script forwards that stdout into the recording so the terminal frame
    shows the same per-strategy numbers the README's savings table
    derives from.

    This used to raise ``RuntimeError`` on a non-zero ``rc``, and nothing
    caught it — so a bench that failed correctly got its work undone three
    ways (#180). ``bench_savings`` returns the documented **2** on an I/O
    error and prints its own clean ``::error::`` line (#156); the wrapper
    downgraded that to a traceback at exit **1**, buried the useful stderr
    line underneath it, and did so via a message reading "output captured:"
    followed by nothing — ``redirect_stdout`` captures stdout, and the
    diagnostic went to stderr.

    Returning the code lets `main` propagate it verbatim, which is also what
    the artifact-missing check immediately below already does.
    """
    bench_main = _import_bench_main()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = bench_main(["--dry", "--out", str(tmp_out_stem)])
    return rc, buf.getvalue()


def _streamlit_argv(json_path: Path, *streamlit_options: str) -> list[str]:
    """The dashboard command, pointed at *this run's* JSON (#241).

    `dashboard/app.py` defaults to the committed `docs/savings.json`, which
    STAGE 1 does not write -- so without `--json` the recording showed the
    committed file under a cheat-sheet saying it was the run just made.
    Everything after `--` is forwarded to the script by streamlit, so
    streamlit's own options go before it.

    Absolute, against *this* process's cwd (#277). The path is resolved by
    another process: `--launch-streamlit` runs the child with `cwd=REPO_ROOT`,
    and the cheat-sheet command only works from the repo root
    (`dashboard/app.py` is relative). A relative `--output-dir` given from
    anywhere else therefore named a different file -- with
    `--output-dir docs/demo-artifacts` it named the repo's own copy from an
    earlier take, and the page caption then showed exactly the string step 3a
    tells the operator to confirm.
    """
    return [
        "streamlit",
        "run",
        "dashboard/app.py",
        *streamlit_options,
        "--",
        "--json",
        str(json_path.absolute()),
    ]


def _dashboard_cheatsheet(
    json_path: Path, *, url: str = DASHBOARD_URL, launched_pid: int | None = None
) -> str:
    # Section names are the dashboard's own `st.subheader` titles, quoted
    # exactly; `tests/test_capture_demo_cheatsheet.py` derives those titles
    # from `dashboard/app.py` and fails on a quoted name that is not one (#241).
    # The previous checklist named a "comparison panel" and a `?source=` URL
    # parameter, neither of which the dashboard has.
    #
    # `url` and `launched_pid` describe the dashboard `--launch-streamlit`
    # started, when it did (#275). #259 made `main` open the port that child
    # reported, but this text kept saying "start the dashboard" and
    # `DASHBOARD_URL`: on a second take the child is on 8502 and 8501 is the
    # first take's dashboard, so the checklist sent the operator to the
    # previous take's JSON -- and to start a third server.
    if launched_pid is None:
        step_1 = (
            "# 1. Start the dashboard in a separate terminal:\n"
            f"#      {shlex.join(_streamlit_argv(json_path))}\n"
        )
    else:
        step_1 = (
            f"# 1. Already running (pid {launched_pid}), started by --launch-streamlit\n"
            "#    with the argument below; do not start another:\n"
            f"#      {shlex.join(_streamlit_argv(json_path))}\n"
        )
    return (
        "# Streamlit dashboard tour (STAGE 2) — operator steps.\n"
        "# Point the dashboard at the JSON STAGE 1 just wrote, not at the\n"
        "# committed docs/savings.json it reads by default. Not launched by\n"
        "# default because streamlit spawns a long-running server that\n"
        "# can't run hermetically in CI; pass --launch-streamlit to spawn\n"
        "# it from this script with the same argument.\n"
        "#\n"
        f"{step_1}"
        "#\n"
        f"# 2. Open the URL the recording captures:\n"
        f"#      {url}\n"
        "#\n"
        "# 3. Recording checklist (in order, so the GIF is reproducible):\n"
        "#      a. The caption under the title — it names the source file;\n"
        f"#         confirm it reads {json_path}.\n"
        '#      b. "Dollars saved vs. baseline" — the per-strategy dollars\n'
        "#         and percent saved, matching STAGE 1's terminal output.\n"
        '#      c. "Cumulative $ saved per row" — the per-strategy\n'
        "#         cumulative series across the workload.\n"
        '#      d. "Per-strategy details" — the full table, including the\n'
        "#         extra.* columns.\n"
        "#\n"
        "# 4. Stop the dashboard with Ctrl-C when the recording is done."
    )


def _maybe_launch_streamlit(json_path: Path, log_path: Path) -> subprocess.Popen[bytes] | None:
    """Spawn `streamlit run dashboard/app.py` as a child if streamlit is
    installed and on PATH, its output going to `log_path`. Returns the child,
    which outlives this script for the dashboard tour; returns ``None`` if
    streamlit isn't available -- the caller falls back to the cheat-sheet.

    The output goes to a file, not the terminal (#259): Streamlit's banner
    prints a Network and an External URL, and the External one is the
    operator's public IP, in a terminal that is being recorded. A file rather
    than a pipe because nothing would drain a pipe once this script exits.
    """
    if shutil.which("streamlit") is None:
        return None
    with log_path.open("wb") as log:
        return subprocess.Popen(  # noqa: S603 — invoked with absolute resolution of `streamlit`
            _streamlit_argv(json_path, "--server.headless", "true"),
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )


_LOCAL_URL = re.compile(r"^\s*Local URL: (http://localhost:\d+)\s*$", re.MULTILINE)


def _wait_for_local_url(
    child: subprocess.Popen[bytes], log_path: Path, timeout: float
) -> str | None:
    """The URL the child reports binding, or ``None`` if it exits or times out first.

    Streamlit moves to the next free port when 8501 is taken and the port was
    not set explicitly -- and on a second take it is taken, by the first take's
    dashboard, which outlives the script by design. Opening the hard-coded
    8501 then recorded the previous take's dashboard and JSON (#259).
    """
    deadline = time.monotonic() + timeout
    while True:
        text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        if (m := _LOCAL_URL.search(text)) is not None:
            return m.group(1)
        if child.poll() is not None or time.monotonic() >= deadline:
            return None
        time.sleep(0.1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic 60-second demo capture orchestrator for llm-cost-optimizer."
    )
    parser.add_argument(
        "--pause-seconds",
        type=float,
        default=2.0,
        help=(
            "Pause between stages so the screen recorder has cue points. "
            "Default 2.0; set to 0 for CI and tests."
        ),
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help=(
            "Skip launching the system browser on the dashboard URL. "
            "Required for CI/tests; default is to open the URL once "
            "STAGE 2 begins so the recording captures the rendered page."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=(
            "Where the stable artifact copies land. Default: "
            "docs/demo-artifacts (gitignored). The directory is created "
            "on first run and overwritten on re-runs."
        ),
    )
    parser.add_argument(
        "--launch-streamlit",
        action="store_true",
        help=(
            "Subprocess-spawn `streamlit run dashboard/app.py`. Off by "
            "default — streamlit spawns a long-running server that can't "
            "run hermetically in CI. Operators pass this for one-key "
            "recording sessions."
        ),
    )
    parser.add_argument(
        "--skip-dashboard-cheatsheet",
        action="store_true",
        help="Suppress the STAGE 2 cheat-sheet print. Useful for CI.",
    )
    args = parser.parse_args(argv)

    # Validate before anything runs. The pre-fix `inf` crash fired from
    # `_pause`, i.e. after STAGE 1 had already run the bench and written
    # artifacts — a usage error that cost the operator a partial capture.
    # Checking here makes it free.
    if (msg := _validate_pause_seconds(args.pause_seconds)) is not None:
        return _fail(msg)

    output_dir: Path = args.output_dir
    # `mkdir` was bare: an `--output-dir` that is an existing file raised
    # FileExistsError and one under a file parent raised NotADirectoryError,
    # both as raw tracebacks at exit 1 (#180).
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return _fail(f"failed to create output directory {output_dir}: {e}")

    # STAGE 1 — bench savings (auto, hermetic).
    print(_banner(1, "Savings bench (scripts/bench_savings.py --dry)"))
    tmp_out_stem = output_dir / "savings_run"
    bench_rc, bench_stdout = _run_bench_into(tmp_out_stem)
    print(bench_stdout, end="")
    if bench_rc != 0:
        # Propagate the bench's own code verbatim — a bench I/O error is a 2
        # and must stay a 2. `bench_savings` has already printed its clean
        # `::error::` line to stderr (#156), so don't restate the cause here;
        # just say which stage aborted. Same shape as the artifact-missing
        # handler below.
        print(
            f"[capture] scripts/bench_savings.py exited {bench_rc}; aborting demo capture.",
            file=sys.stderr,
        )
        return bench_rc

    # The same derivation the bench used to write them (#231, D-023), so the
    # two cannot disagree about where the artifacts are.
    from scripts._io import artifact_path

    bench_md = artifact_path(tmp_out_stem, ".md")
    bench_json = artifact_path(tmp_out_stem, ".json")
    if not bench_md.exists() or not bench_json.exists():
        print(
            f"[capture] bench did not produce expected artifacts at "
            f"{bench_md} / {bench_json}; aborting.",
            file=sys.stderr,
        )
        return 1

    # Copy to stable filenames so the recorder's open file path is fixed
    # across re-captures, independent of the `--out` stem.
    stable_md = output_dir / STABLE_SAVINGS_MD
    stable_json = output_dir / STABLE_SAVINGS_JSON
    # Separate seam from the `mkdir` above, reached by an input that `mkdir`
    # accepts: an existing-but-unwritable stable target leaves the *directory*
    # perfectly fine and fails here instead, after the bench has already run
    # (#180).
    try:
        shutil.copy2(bench_md, stable_md)
        shutil.copy2(bench_json, stable_json)
    except OSError as e:
        return _fail(f"failed to copy bench artifacts into {output_dir}: {e}")
    print(f"\n[capture] stable savings table: {stable_md}")
    print(f"[capture] stable savings JSON:  {stable_json}")
    _pause(args.pause_seconds)

    # STAGE 2 — dashboard tour (operator-action, optional auto-launch).
    print(_banner(2, "Streamlit dashboard tour (operator-action)"))

    dashboard_url = DASHBOARD_URL
    launched_pid: int | None = None
    if args.launch_streamlit:
        log_path = output_dir / STREAMLIT_LOG
        streamlit_child = _maybe_launch_streamlit(stable_json, log_path)
        if streamlit_child is None:
            print(
                "[capture] --launch-streamlit was passed but `streamlit` is "
                "not on PATH; falling back to the cheat-sheet."
            )
        else:
            local_url = _wait_for_local_url(streamlit_child, log_path, timeout=30.0)
            if local_url is None:
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
                if streamlit_child.poll() is None:
                    streamlit_child.terminate()
                return _fail(
                    f"streamlit (pid {streamlit_child.pid}) exited or never reported its "
                    f"Local URL; its output ({log_path}):\n{tail}"
                )
            dashboard_url = local_url
            launched_pid = streamlit_child.pid
            print(
                f"[capture] spawned streamlit (pid {streamlit_child.pid}) on {local_url}; "
                f"output in {log_path}. Terminate it when the recording is done."
            )

    # Open the dashboard URL by default so the recording captures the rendered
    # page (suppress with --no-open). The demo flow assumes the dashboard is
    # running — whether auto-launched above or started by the operator per the
    # cheat-sheet. This open used to be nested inside the --launch-streamlit
    # success branch, so on the default path --no-open controlled nothing and
    # the URL was never opened despite the documented default (#100). When this
    # script launched it, the URL is the one the child reported (#259).
    if not args.no_open:
        webbrowser.open(dashboard_url)

    if not args.skip_dashboard_cheatsheet:
        print(_dashboard_cheatsheet(stable_json, url=dashboard_url, launched_pid=launched_pid))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
