"""Smoke test for `scripts/capture_demo.sh` (issue #18).

The capture script is the deterministic driver for the 60-second README demo.
JT records the GIF/video while it runs; CI runs it with
`CAPTURE_PACE_SECONDS=0` and `CAPTURE_LAUNCH_DASHBOARD=0` so the bench part is
exercised without spinning up streamlit.

Contract this test pins:

1. The script exits 0 on a fresh clone with no API key.
2. The bench surface actually runs and emits the five expected strategies.
3. The rendered savings.md is `cat`-ed to stdout so the recording captures
   the rendered five-strategy table directly in the terminal.
4. The committed `docs/savings.json` is *not* mutated by the script — the
   capture writes to a tempdir.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "capture_demo.sh"
COMMITTED_SAVINGS = REPO_ROOT / "docs" / "savings.json"

EXPECTED_STRATEGIES = (
    "baseline",
    "prompt caching",
    "semantic cache",
    "uncertainty router",
    "batch API",
)


@pytest.fixture(scope="module")
def capture_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Run the capture script once and reuse its stdout + artifact paths.

    `CAPTURE_LAUNCH_DASHBOARD=0` skips the streamlit launch. `CAPTURE_PACE_SECONDS=0`
    removes the recording pauses. `CAPTURE_OUTPUT_DIR` pins the artifact
    location so the test can assert on the generated savings.json.
    """
    if not SCRIPT.exists():
        pytest.fail(f"missing {SCRIPT}")
    if shutil.which("bash") is None:
        pytest.skip("bash not available")

    output_dir = tmp_path_factory.mktemp("capture-out")
    env = dict(os.environ)
    env["CAPTURE_PACE_SECONDS"] = "0"
    env["CAPTURE_LAUNCH_DASHBOARD"] = "0"
    env["CAPTURE_OUTPUT_DIR"] = str(output_dir)
    venv_bin = Path(sys.executable).parent
    env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"

    committed_before = COMMITTED_SAVINGS.read_bytes() if COMMITTED_SAVINGS.exists() else None

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"capture_demo.sh exited {result.returncode}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )

    return {
        "stdout": result.stdout,
        "output_dir": output_dir,
        "committed_before": committed_before,
    }


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.exists(), f"missing {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"{SCRIPT} should be executable"


def test_bench_surface_runs_and_prints_five_strategies(
    capture_run: dict[str, object],
) -> None:
    stdout = capture_run["stdout"]
    assert isinstance(stdout, str)
    assert "1/2 · scripts/bench_savings.py" in stdout
    for strategy in EXPECTED_STRATEGIES:
        assert strategy in stdout, (
            f"expected strategy {strategy!r} in capture output; got first 800 chars:\n"
            f"{stdout[:800]}"
        )


def test_rendered_savings_md_emitted_to_stdout(capture_run: dict[str, object]) -> None:
    """The capture cats savings.md so the rendered table lands in the recording."""
    stdout = capture_run["stdout"]
    assert isinstance(stdout, str)
    assert "rendered savings table" in stdout
    # The rendered markdown has a distinctive table header.
    assert "| Strategy | Rows | $ spent | $ saved | % saved | Mean quality | Extra |" in stdout
    # And the committed cheap/strong model lines.
    assert "Cheap model:" in stdout
    assert "Strong model:" in stdout


def test_dashboard_section_describes_launch_path(capture_run: dict[str, object]) -> None:
    stdout = capture_run["stdout"]
    assert isinstance(stdout, str)
    assert "2/2 · streamlit dashboard" in stdout
    # With LAUNCH=0, the script either prints the skip notice (streamlit
    # absent OR explicitly skipped). Both paths surface the dashboard module
    # path and a pip-install hint for the dashboard extra.
    assert "cost_optimizer/dashboard/app.py" in stdout
    assert "[dashboard]" in stdout


def test_bench_writes_to_tempdir_not_committed_artifact(
    capture_run: dict[str, object],
) -> None:
    """The capture must never mutate `docs/savings.json` — it writes to a tempdir."""
    output_dir = capture_run["output_dir"]
    assert isinstance(output_dir, Path)

    generated = output_dir / "savings.json"
    assert generated.exists(), f"capture should have produced {generated}"
    payload = json.loads(generated.read_text())
    assert payload["n_rows"] == 500
    assert len(payload["strategies"]) == 5

    committed_before = capture_run["committed_before"]
    if committed_before is not None:
        committed_after = COMMITTED_SAVINGS.read_bytes()
        assert committed_after == committed_before, (
            "scripts/capture_demo.sh must not mutate docs/savings.json — "
            "the recording reads the freshly-generated tempdir artifact."
        )
