#!/usr/bin/env bash
# The main sweep. Five local models against a pinned, multi-language task set.
#
# Sampled rather than exhaustive: the full corpus across five models is ~22
# hours on this machine, which is not a study, it is a weekend. The sample
# keeps three languages and both difficulty ends, and the departure is recorded
# in the report.
#
# The 14b model costs ~4.7x the 7b per attempt, so it runs only on the easy
# subset -- enough for the within-family size comparison, not the whole sweep.
set -uo pipefail
REPOS_DIR="${REPOS_DIR:?}"
export OPENAI_API_KEY=ollama
export GROUNDHOG_OPENAI_BASE_URL=http://localhost:11434/v1
mkdir -p results study/logs

FAST=(openai:qwen2.5-coder:7b openai:qwen3:8b openai:llama3.1:latest openai:mistral:latest)
REPOS=(toolbox gotoolbox jstoolbox httpx click)

run_one() {  # model, repo, tasks, tag
  local model="$1" repo="$2" tasks="$3" tag="$4"
  local safe="${model//[:\/]/-}"
  printf "  %-34s %-11s " "$model" "$repo"
  python3 -m groundhog run "$REPOS_DIR/$repo" \
      --models "$model" --tasks "$tasks" \
      --out "results/$tag-$safe-$repo.jsonl" \
      --max-turns 14 --timeout 150 --resume \
      >> "study/logs/run-$safe-$repo.log" 2>&1
  local code=$?
  echo "exit=$code  $(python3 - "results/$tag-$safe-$repo.jsonl" <<'PY'
import json, sys
try:
    rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
except OSError:
    print("no output"); raise SystemExit
solved = sum(r["solved"] for r in rows)
f2p = sum(r["fail_to_pass_passed"] for r in rows)
print(f"solved={solved}/{len(rows)} f2p_met={f2p}")
PY
)"
}

# Split the pinned sample back out per repo, since a run targets one repo.
python3 - <<'PY'
import json, collections, pathlib
rows = [json.loads(l) for l in open("tasks/study-sample.jsonl") if l.strip()]
groups = collections.defaultdict(list)
for r in rows:
    groups[r["_repo"]].append(r)
for name, items in groups.items():
    pathlib.Path(f"tasks/sample-{name}.jsonl").write_text(
        "".join(json.dumps(i) + "\n" for i in items))
    print(f"  sample-{name}.jsonl: {len(items)}")
PY

echo
echo "### fast models, all repos ($(date -u '+%H:%M:%S'))"
for model in "${FAST[@]}"; do
  for repo in "${REPOS[@]}"; do
    run_one "$model" "$repo" "tasks/sample-$repo.jsonl" main
  done
done

echo
echo "### qwen2.5-coder:14b, easy subset only ($(date -u '+%H:%M:%S'))"
for repo in toolbox gotoolbox jstoolbox; do
  run_one openai:qwen2.5-coder:14b "$repo" "tasks/sample-$repo.jsonl" main
done

cat results/main-*.jsonl > results/study-all.jsonl
echo
echo "TOTAL ATTEMPTS: $(wc -l < results/study-all.jsonl)  ($(date -u '+%H:%M:%S'))"
