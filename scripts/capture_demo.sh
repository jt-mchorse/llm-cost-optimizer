#!/usr/bin/env bash
# Deterministic driver for the 60-second README demo (issue #18).
#
# Runs the two demo surfaces in sequence on a fresh clone with no API key:
#
#   1. scripts/bench_savings.py --dry  →  five-strategy savings table.
#      The terminal section of the recording captures the rendered
#      savings.md (per-strategy $/% saved across baseline / prompt cache /
#      semantic cache / uncertainty router / batch API).
#   2. (optional) streamlit run cost_optimizer/dashboard/app.py
#      pointing at the freshly-generated savings.json. The browser
#      section of the recording captures the strategy summary, the
#      cumulative-savings chart, and the quality column.
#
# The output is the recording — when JT records the GIF/video, this script's
# stdout is what gets captured. Hermetic: no API key, no network.
#
# Variables:
#   CAPTURE_PACE_SECONDS      pause between sections (default 2 for
#                             recording; test_capture_demo_smoke.py sets 0).
#   CAPTURE_LAUNCH_DASHBOARD  if "1" (default), launch the streamlit
#                             dashboard in the foreground at the end of
#                             the script. Smoke tests set this to "0".
#   CAPTURE_OUTPUT_DIR        directory the bench artifacts are written to
#                             (default: a per-run mktemp dir).
#
# Exit: 0 on full success. Streamlit launch failures (missing dashboard
# extra, port in use, etc.) print a friendly message but do not fail the
# script — the bench has already produced the recordable artifact.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACE="${CAPTURE_PACE_SECONDS:-2}"
LAUNCH="${CAPTURE_LAUNCH_DASHBOARD:-1}"
OUTPUT_DIR="${CAPTURE_OUTPUT_DIR:-}"

banner() {
  printf '\n'
  printf '═══ %s\n' "$1"
  printf '\n'
}

pace() {
  if [ "$PACE" != "0" ]; then
    sleep "$PACE"
  fi
}

cd "$REPO_ROOT"

if [ -z "$OUTPUT_DIR" ]; then
  OUTPUT_DIR="$(mktemp -d -t llm-cost-optimizer-capture-XXXXXX)"
  CLEANUP_OUTPUT=1
else
  mkdir -p "$OUTPUT_DIR"
  CLEANUP_OUTPUT=0
fi

OUT_STEM="$OUTPUT_DIR/savings"

banner "llm-cost-optimizer · 60-second demo"
printf 'two surfaces · 500-row synthetic workload · no API key required\n'
printf 'artifacts: %s\n' "$OUTPUT_DIR"
pace

banner "1/2 · scripts/bench_savings.py --dry"
printf '500 deterministic rows · 60%% redundant · 30%% easy · 10%% hard\n'
printf 'five strategies: baseline → prompt cache → semantic cache → router → batch\n\n'
python -u scripts/bench_savings.py --dry --out "$OUT_STEM"
pace

banner "rendered savings table (cat $OUT_STEM.md)"
cat "$OUT_STEM.md"
pace

banner "2/2 · streamlit dashboard"
printf 'cost_optimizer/dashboard/app.py reads %s.json\n' "$OUT_STEM"
printf '  - workload mix\n'
printf '  - dollars saved vs. baseline (bar chart)\n'
printf '  - cumulative $ saved per row (line chart per strategy)\n'
printf '  - quality maintained? (per-strategy delta vs. baseline)\n\n'

if [ "$LAUNCH" != "1" ]; then
  printf '(CAPTURE_LAUNCH_DASHBOARD=0 → dashboard launch skipped)\n'
  printf 'to record the second segment manually:\n'
  printf '  pip install -e \047.[dashboard]\047\n'
  printf '  streamlit run cost_optimizer/dashboard/app.py -- --json %s.json\n' "$OUT_STEM"
else
  if ! python -c "import streamlit" >/dev/null 2>&1; then
    printf '(streamlit not installed — install the dashboard extra to record this segment:\n'
    printf '  pip install -e \047.[dashboard]\047\n'
    printf 'bench artifact at %s.json is ready to feed the dashboard once installed.)\n' "$OUT_STEM"
  else
    printf 'launching streamlit · Ctrl+C when done recording\n\n'
    # `exec` so signals from the recording session land directly on streamlit
    # and the bash wrapper doesn't intercept Ctrl+C. The cleanup-tempdir hook
    # below won't run after exec — but the OS reaps `mktemp -d` artifacts on
    # reboot and the operator can rm -rf the artifacts dir if they care.
    exec python -m streamlit run cost_optimizer/dashboard/app.py -- --json "$OUT_STEM.json"
  fi
fi

if [ "$CLEANUP_OUTPUT" = "1" ] && [ "$LAUNCH" != "1" ]; then
  # Only when we both created the tempdir AND we won't be handing off to
  # streamlit (which exec's and skips this cleanup) AND the caller didn't
  # pin CAPTURE_OUTPUT_DIR.
  rm -rf "$OUTPUT_DIR"
fi

banner "demo complete"
printf 'bench ran end-to-end with zero API calls.\n'
printf 'recapture: scripts/capture_demo.sh (env: CAPTURE_PACE_SECONDS, CAPTURE_LAUNCH_DASHBOARD, CAPTURE_OUTPUT_DIR).\n'
