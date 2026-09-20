#!/usr/bin/env bash
# Wait for the post-hoc cripple test to release the GPU, then run the sweep.
# Two models never hold memory at the same time; the cripple test and the
# qwen3 click remainder overlap only because they share one set of weights.
set -uo pipefail
cd "$(dirname "$0")/.."
echo "waiting for cripple2 (started $(date -u '+%H:%M:%S'))"
while [ ! -f study/cripple2.json ]; do sleep 60; done
echo "cripple2 done at $(date -u '+%H:%M:%S'); waiting for the click remainder"
while pgrep -f "groundhog run.*qwen3-8b-click" >/dev/null; do sleep 30; done
ollama stop qwen3:8b >/dev/null 2>&1 || true
echo "starting sweep at $(date -u '+%H:%M:%S')"
exec bash scripts/study_run_parallel.sh
