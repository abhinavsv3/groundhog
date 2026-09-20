#!/usr/bin/env bash
# Replication declared in study/nudge-preregistration.md. Run after that file
# was committed; the direction, the task set, the analysis and the power
# calculation were all fixed in advance.
#
# Only the nudges-off arm is new. The nudges-on arm is the main sweep's, run
# before the hypothesis existed.
set -uo pipefail
REPOS_DIR="${REPOS_DIR:?}"
export OPENAI_API_KEY=ollama
export GROUNDHOG_OPENAI_BASE_URL=http://localhost:11434/v1
mkdir -p results study/logs

for repo in gotoolbox jstoolbox; do
  echo "=== nudges off: $repo ($(date -u '+%H:%M:%S')) ==="
  python3 -m groundhog run "$REPOS_DIR/$repo" \
      --models openai:qwen3:8b --tasks "tasks/sample-$repo.jsonl" \
      --out "results/nonudge-$repo.jsonl" \
      --max-turns 14 --max-nudges 0 --timeout 150 --resume \
      >> study/logs/nonudge-$repo.log 2>&1
  echo "  exit=$?"
done

cat results/nonudge-gotoolbox.jsonl results/nonudge-jstoolbox.jsonl > results/nonudge-all.jsonl
cat results/main-openai-qwen3-8b-gotoolbox.jsonl \
    results/main-openai-qwen3-8b-jstoolbox.jsonl > results/nudge-all.jsonl

python3 -m groundhog compare results/nonudge-all.jsonl results/nudge-all.jsonl \
    --label-a "nudges off" --label-b "nudges on" --json study/nudge-replication.json
