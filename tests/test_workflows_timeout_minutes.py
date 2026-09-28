"""Lock that every workflow job has a sensible `timeout-minutes` bound.

Propagation of `llm-eval-harness#62` (the canonical first hop) — same
silent-rot prevention arc as `test_workflows_yaml_parseable.py` (#55 in
this repo / portfolio-ops#30 / portfolio-ops#31), different failure mode.

The failure mode this catches: GitHub Actions defaults to 360 minutes
(6 hours) per job when no `timeout-minutes` is set. A hung job — network
stall during `pip install`, infinite test loop, stuck API call against a
flaky upstream — burns the full 6-hour ceiling before the job is killed.
That's quota the operator pays for whether the run produced anything or
not.

This repo's `.github/workflows/integration.yml` already declared
`timeout-minutes: 10` on its single job at write time (good); this lock
extends the invariant to `ci.yml`'s three jobs and makes regression
loud thereafter.

Spec / origin: this repo's #58.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Policy band for this repo. Matches the canonical band from
# llm-eval-harness#62. Bump MAX with a comment naming the workload if a
# legitimately longer-running workflow lands (e.g., dashboard rendering).
MIN_TIMEOUT_MINUTES = 1
MAX_TIMEOUT_MINUTES = 30


def _all_workflow_files() -> list[Path]:
    if not ACTIVE_WORKFLOWS_DIR.is_dir():
        return []
    return sorted(ACTIVE_WORKFLOWS_DIR.glob("*.yml"))


def _all_jobs() -> list[tuple[str, str, dict[str, Any]]]:
    """Return (workflow_filename, job_id, job_body) for every job."""
    rows: list[tuple[str, str, dict[str, Any]]] = []
    for path in _all_workflow_files():
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            continue
        jobs = parsed.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_id, body in jobs.items():
            if isinstance(body, dict):
                rows.append((path.name, str(job_id), body))
    return rows


ALL_JOBS = _all_jobs()


def test_at_least_one_job_discovered() -> None:
    assert ALL_JOBS, (
        f"No jobs discovered under {ACTIVE_WORKFLOWS_DIR}. Either the "
        "workflow files were removed or YAML discovery is broken; this "
        "lock should not silently pass in either case."
    )


@pytest.mark.parametrize(
    ("workflow", "job_id", "body"),
    ALL_JOBS,
    ids=[f"{wf}::{jid}" for (wf, jid, _) in ALL_JOBS],
)
def test_job_has_timeout_minutes(workflow: str, job_id: str, body: dict[str, Any]) -> None:
    timeout = body.get("timeout-minutes")
    assert timeout is not None, (
        f"{workflow}::{job_id} has no `timeout-minutes` set. GitHub "
        f"Actions defaults to 360 min/job when this is missing — a hung "
        f"job (network stall, infinite loop, stuck API call) burns the "
        f"full 6-hour ceiling before the runner kills it. Set "
        f"`timeout-minutes:` on this job. For this repo's workloads, "
        f"15 is the policy default for CI; stay in "
        f"[{MIN_TIMEOUT_MINUTES}, {MAX_TIMEOUT_MINUTES}]."
    )


@pytest.mark.parametrize(
    ("workflow", "job_id", "body"),
    ALL_JOBS,
    ids=[f"{wf}::{jid}" for (wf, jid, _) in ALL_JOBS],
)
def test_job_timeout_is_int(workflow: str, job_id: str, body: dict[str, Any]) -> None:
    timeout = body.get("timeout-minutes")
    if timeout is None:
        pytest.skip("covered by test_job_has_timeout_minutes")
    msg = (
        f"{workflow}::{job_id} has `timeout-minutes: {timeout!r}` "
        f"({type(timeout).__name__}); GitHub Actions requires an integer. "
        "A YAML string like `'15'` is parsed but rejected at workflow-load "
        "time, producing a silent failure shape similar to the YAML "
        "parseability bug propagated from portfolio-ops#27."
    )
    # `bool` is a subclass of `int` in Python; reject it explicitly so a
    # stray `timeout-minutes: true` (parsed as 1) doesn't sneak past.
    assert isinstance(timeout, int), msg
    assert not isinstance(timeout, bool), msg


@pytest.mark.parametrize(
    ("workflow", "job_id", "body"),
    ALL_JOBS,
    ids=[f"{wf}::{jid}" for (wf, jid, _) in ALL_JOBS],
)
def test_job_timeout_in_policy_band(workflow: str, job_id: str, body: dict[str, Any]) -> None:
    timeout = body.get("timeout-minutes")
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        pytest.skip("covered by test_job_timeout_is_int")
    assert MIN_TIMEOUT_MINUTES <= timeout <= MAX_TIMEOUT_MINUTES, (
        f"{workflow}::{job_id} has `timeout-minutes: {timeout}` outside the "
        f"policy band [{MIN_TIMEOUT_MINUTES}, {MAX_TIMEOUT_MINUTES}]. Values "
        f"above the ceiling reintroduce most of the unbounded-job quota burn; "
        f"values at 0 disable the timeout entirely (GitHub Actions semantics). "
        f"If this job genuinely needs a wider bound, bump MAX_TIMEOUT_MINUTES "
        f"with a comment naming the workload that forced the change."
    )


#: The `test` job's cap, pinned by value rather than only by band (#229).
#:
#: `test_job_timeout_in_policy_band` asserts every cap is inside `[1, 30]`, and
#: `15` satisfies that — which is why nothing in this repo noticed `test (3.12)`
#: being cancelled on `main` twice for exceeding it. A band lock cannot see a cap
#: that is legal and too small for the job it governs.
TEST_JOB_MIN_TIMEOUT_MINUTES = 20


def test_the_test_job_keeps_the_headroom_it_was_given() -> None:
    """The `test` job's cap cannot quietly drift back to 15 (#229).

    Measured on `main` before the cap was raised — `test (3.12)` against the old
    15-minute cap, newest ten push runs:

        09-28  15m10s  1.01  CANCELLED
        09-23  10m48s  0.72
        09-22  15m05s  1.00  CANCELLED
        09-21  14m05s  0.94
        09-14  13m12s  0.88
        09-11  10m48s  0.72
        09-10  13m17s  0.89
        09-09  13m10s  0.88
        09-08  14m32s  0.97
        09-07  13m22s  0.89

    At or above 0.88 in eight of ten. The floor here is 20 rather than 30 so the
    cap can be tuned without a test edit, while anything that would put the
    observed 15m10s maximum back above `portfolio-ops`' 0.80 headroom bar goes
    red: 910s / (20 * 60) is 0.76, and 910s / (19 * 60) is 0.80.

    Only `test` is pinned. `lint` and `memory-check` measured 18s and 5s against
    the same cap — ratios of 0.02 and 0.006 — so a floor for them would be a
    number with no measurement behind it.
    """
    ci = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    jobs = yaml.safe_load(ci.read_text(encoding="utf-8"))["jobs"]
    timeout = jobs["test"]["timeout-minutes"]
    assert timeout >= TEST_JOB_MIN_TIMEOUT_MINUTES, (
        f"ci.yml's `test` job is capped at {timeout} minutes. Its own worst "
        f"observed run on main is 15m10s, so anything under "
        f"{TEST_JOB_MIN_TIMEOUT_MINUTES} puts it back over the 0.80 headroom "
        f"bar `portfolio-ops`' timeout-headroom fingerprint applies (#229)."
    )
    assert timeout <= MAX_TIMEOUT_MINUTES, (
        "raising past the policy band needs a band change and a comment naming "
        "the workload, per this module's header."
    )


def test_ci_records_test_durations_so_the_next_investigation_has_data() -> None:
    """Local profiling cannot attribute CI time, so the CI log has to carry it.

    The same suite is ~39s locally and 624-910s on a two-core runner under
    coverage. Nothing in a local `--durations` run explains that gap, and the
    honest response to "which tests are slow in CI?" was previously "no data".
    This arm keeps the instrument from being dropped in a future workflow tweak,
    the same way `portfolio-ops`' `test_pyyaml_installed` keeps an install step.
    """
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    jobs = yaml.safe_load(ci)["jobs"]
    runs = [
        step.get("run", "")
        for step in jobs["test"]["steps"]
        if isinstance(step, dict) and "run" in step
    ]
    pytest_steps = [r for r in runs if "pytest" in r]
    assert pytest_steps, "ci.yml's `test` job no longer runs pytest"
    assert any("--durations" in r for r in pytest_steps), (
        f"the `test` job's pytest invocation dropped `--durations`: {pytest_steps}. "
        f"Without it there is no way to attribute the 16-23x gap between the "
        f"local and CI runtimes (#229)."
    )
