#!/usr/bin/env bash
# POST-HOC validity gate, run after the pre-registered one failed.
#
# The original used qwen2.5-coder:7b, which scores ~8% on this task set. A
# cripple test measures a DECREASE, so there was no headroom to decrease from
# and the test could not have succeeded at any sample size. qwen3:8b scores
# ~52% on the same tasks, which is the headroom the test needed.
#
# This does not replace the failed gate. Both are reported.
set -uo pipefail
REPOS_DIR="${REPOS_DIR:?}"
MODEL="openai:qwen3:8b"
export OPENAI_API_KEY=ollama
export GROUNDHOG_OPENAI_BASE_URL=http://localhost:11434/v1
mkdir -p results study/logs

for turns in 1 14; do
  echo "=== qwen3:8b --max-turns $turns ($(date -u '+%H:%M:%S')) ==="
  python3 -m groundhog run "$REPOS_DIR/toolbox" \
      --models "$MODEL" --tasks tasks/toolbox-validated.jsonl \
      --out "results/cripple2-$turns.jsonl" \
      --max-turns "$turns" --max-nudges 0 --timeout 200 --resume \
      >> study/logs/cripple2.log 2>&1
  echo "  exit=$? $(python3 -c "
import json
rows=[json.loads(l) for l in open('results/cripple2-$turns.jsonl') if l.strip()]
print(f'solved={sum(r[\"solved\"] for r in rows)}/{len(rows)} f2p={sum(r[\"fail_to_pass_passed\"] for r in rows)} calls={sum((r.get(\"tools\") or {}).get(\"calls\",0) for r in rows)}')")"
done

echo
python3 -m groundhog compare results/cripple2-1.jsonl results/cripple2-14.jsonl \
    --label-a "1-turn" --label-b "14-turn" --json study/cripple2.json
