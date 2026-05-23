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

DASHBOARD_URL = "http://localhost:8501"


def _banner(stage: int, title: str) -> str:
    line = "=" * 72
    return f"\n{line}\n  STAGE {stage}  {title}\n{line}\n"


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


def _run_bench_into(tmp_out_stem: Path) -> str:
    """Run `bench_savings.main` against a tmp `--out` stem; return its stdout.

    The bench script writes three artifacts next to the stem
    (`<stem>.json`, `<stem>.md`, and `<stem>.parent>/savings_workload.json`)
    and prints a per-strategy summary line per row to stdout. The capture
    script forwards that stdout into the recording so the terminal frame
    shows the same per-strategy numbers the README's savings table
    derives from.
    """
    bench_main = _import_bench_main()
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = bench_main(["--dry", "--out", str(tmp_out_stem)])
    if rc != 0:
        raise RuntimeError(
            f"scripts.bench_savings.main exited {rc}; output captured:\n{buf.getvalue()}"
        )
    return buf.getvalue()


def _dashboard_cheatsheet() -> str:
    return (
        "# Streamlit dashboard tour (STAGE 2) — operator steps.\n"
        "# The dashboard reads the committed docs/savings.json that\n"
        "# STAGE 1 just regenerated. Not launched by default because\n"
        "# streamlit spawns a long-running server that can't run\n"
        "# hermetically in CI; pass --launch-streamlit to spawn it from\n"
        "# this script.\n"
        "#\n"
        "# 1. Start the dashboard in a separate terminal:\n"
        "#      streamlit run dashboard/app.py\n"
        "#\n"
        f"# 2. Open the URL the recording captures:\n"
        f"#      {DASHBOARD_URL}\n"
        "#\n"
        "# 3. Recording checklist (in order, so the GIF is reproducible):\n"
        "#      a. Strategy summary table — top of page; show the five\n"
        "#         rows with per-strategy dollars-saved and percent-saved\n"
        "#         that match the terminal output from STAGE 1.\n"
        "#      b. Cumulative-savings chart — scroll to confirm the chart\n"
        "#         is sourced from docs/savings.json (the page footer or\n"
        "#         a `?source=...` URL parameter shows this).\n"
        "#      c. Strategy comparison view — open the comparison panel\n"
        "#         to show two strategies side-by-side with their\n"
        "#         saved_pct trajectories.\n"
        "#\n"
        "# 4. Stop the dashboard with Ctrl-C when the recording is done."
    )


def _maybe_launch_streamlit() -> subprocess.Popen[bytes] | None:
    """Spawn `streamlit run dashboard/app.py` as a child if streamlit is
    installed and on PATH. Returns the child for the operator to terminate
    when the recording is finished. Returns ``None`` if streamlit isn't
    available — the caller falls back to the cheat-sheet.
    """
    if shutil.which("streamlit") is None:
        return None
    return subprocess.Popen(  # noqa: S603 — invoked with absolute resolution of `streamlit`
        ["streamlit", "run", "dashboard/app.py", "--server.headless", "true"],
        cwd=REPO_ROOT,
    )


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

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # STAGE 1 — bench savings (auto, hermetic).
    print(_banner(1, "Savings bench (scripts/bench_savings.py --dry)"))
    tmp_out_stem = output_dir / "savings_run"
    bench_stdout = _run_bench_into(tmp_out_stem)
    print(bench_stdout, end="")

    bench_md = tmp_out_stem.with_suffix(".md")
    bench_json = tmp_out_stem.with_suffix(".json")
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
    shutil.copy2(bench_md, stable_md)
    shutil.copy2(bench_json, stable_json)
    print(f"\n[capture] stable savings table: {stable_md}")
    print(f"[capture] stable savings JSON:  {stable_json}")
    _pause(args.pause_seconds)

    # STAGE 2 — dashboard tour (operator-action, optional auto-launch).
    print(_banner(2, "Streamlit dashboard tour (operator-action)"))

    streamlit_child = None
    if args.launch_streamlit:
        streamlit_child = _maybe_launch_streamlit()
        if streamlit_child is None:
            print(
                "[capture] --launch-streamlit was passed but `streamlit` is "
                "not on PATH; falling back to the cheat-sheet."
            )
        else:
            print(
                f"[capture] spawned streamlit (pid {streamlit_child.pid}); "
                f"Ctrl-C / terminate when the recording is done."
            )
            # Small grace period so the server is up before the browser opens.
            time.sleep(2.0)
            if not args.no_open:
                webbrowser.open(DASHBOARD_URL)

    if not args.skip_dashboard_cheatsheet:
        print(_dashboard_cheatsheet())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
