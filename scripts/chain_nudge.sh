#!/usr/bin/env bash
# Wait for the main sweep to release the GPU, then run the nudge replication.
set -uo pipefail
cd "$(dirname "$0")/.."
while [ ! -f results/study-all.jsonl ] || pgrep -f "groundhog run" >/dev/null; do sleep 60; done
ollama stop qwen2.5-coder:14b >/dev/null 2>&1 || true
echo "sweep done, starting nudge replication at $(date -u '+%H:%M:%S')"
exec bash scripts/nudge_replication.sh
