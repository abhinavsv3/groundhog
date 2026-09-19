#!/usr/bin/env bash
# Validity gate: can this setup detect a known-large effect?
# Same model, same tasks, one turn versus many. If this is not significant,
# every null reported afterwards is meaningless.
set -uo pipefail
REPOS_DIR="${REPOS_DIR:?}"
MODEL="${MODEL:-openai:qwen2.5-coder:7b}"
TASKS="${TASKS:-tasks/toolbox-validated.jsonl}"
REPO="${REPO:-toolbox}"
export OPENAI_API_KEY=ollama
export GROUNDHOG_OPENAI_BASE_URL=http://localhost:11434/v1
mkdir -p results study/logs

for turns in 1 14; do
  echo "=== --max-turns $turns ($(date -u '+%H:%M:%S')) ==="
  python3 -m groundhog run "$REPOS_DIR/$REPO" \
      --models "$MODEL" --tasks "$TASKS" \
      --out "results/cripple-$turns.jsonl" \
      --max-turns "$turns" --max-nudges 0 --timeout 120 --resume \
      >> study/logs/cripple.log 2>&1
  echo "  exit=$? solved=$(python3 -c "
import json,sys
rows=[json.loads(l) for l in open('results/cripple-$turns.jsonl') if l.strip()]
print(f'{sum(r[\"solved\"] for r in rows)}/{len(rows)}')")"
done

echo
python3 -m groundhog compare results/cripple-1.jsonl results/cripple-14.jsonl \
    --label-a "1-turn" --label-b "14-turn" --json study/cripple.json
