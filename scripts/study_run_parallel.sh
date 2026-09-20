#!/usr/bin/env bash
# The main sweep, rescheduled for a 16 GB M1 Pro.
#
# Same models, same pinned tasks, same flags as scripts/study_run.sh. The only
# change is the order work is handed to the GPU. Nothing about the measurement
# moves, and --resume means the 73 attempts already recorded are not repeated.
#
# Why this shape:
#
#   One model at a time. Unified memory is 16 GB and macOS holds ~4 GB of it.
#   Two 8B models at q4 are ~11 GB together, and qwen2.5-coder:14b alone is ~9
#   GB. Loading a second model either swaps to disk or makes Ollama evict and
#   reload ~6 GB of weights on every alternating request. Either one is slower
#   than running the models in sequence, so the sweep never holds two.
#
#   Two workers within a model. Decode on Apple Silicon is memory-bandwidth
#   bound, so two concurrent generations against the SAME weights split the
#   bandwidth and win nothing. The win is elsewhere: measured generation
#   throughput is 7-20 tok/s against ~25-30 uncontended, which means roughly
#   half of each attempt's wall clock is not generation at all -- it is venv
#   construction, git worktree setup, and running the test suite. That half is
#   CPU and disk with the GPU idle. A second worker fills it. Expect ~1.4x,
#   not 2x.
#
#   Disjoint repos per worker. The venv cache is keyed
#   {repo.name}-{plan fingerprint}, so two workers on the same repo would race
#   to build the same environment. No repo appears in both lanes.
set -uo pipefail
REPOS_DIR="${REPOS_DIR:?}"
export OPENAI_API_KEY=ollama
export GROUNDHOG_OPENAI_BASE_URL=http://localhost:11434/v1
mkdir -p results study/logs

# Lane A takes the slowest Python repo; lane B takes the other one, so neither
# lane is left holding all the 14-turn timeouts.
LANE_A=(toolbox httpx)
LANE_B=(click gotoolbox jstoolbox)

run_one() {  # model, repo
  local model="$1" repo="$2" safe="${1//[:\/]/-}"
  python3 -m groundhog run "$REPOS_DIR/$repo" \
      --models "$model" --tasks "tasks/sample-$repo.jsonl" \
      --out "results/main-$safe-$repo.jsonl" \
      --max-turns 14 --timeout 150 --resume \
      >> "study/logs/run-$safe-$repo.log" 2>&1
}

lane() {  # model, repo...
  local model="$1"; shift
  for repo in "$@"; do run_one "$model" "$repo"; done
}

tally() {  # model
  local safe="${1//[:\/]/-}"
  python3 - "$safe" <<'PY'
import json, sys, glob
rows = [json.loads(l) for f in glob.glob(f"results/main-{sys.argv[1]}-*.jsonl")
        for l in open(f) if l.strip()]
if not rows:
    print("  no rows"); raise SystemExit
solved = sum(r["solved"] for r in rows)
f2p = sum(r["fail_to_pass_passed"] for r in rows)
collat = sum(bool(r["fail_to_pass_passed"] and not r["solved"]) for r in rows)
calls = sum((r.get("tools") or {}).get("calls", 0) for r in rows)
text = sum((r.get("tools") or {}).get("recovered_from_text", 0) for r in rows)
print(f"  n={len(rows)} solved={solved} f2p={f2p} collateral={collat} "
      f"calls={calls} as_text={100*text//(calls or 1)}%")
PY
}

# Ollama holds the previously-used model resident for five minutes. Between
# models that is 6 GB of memory the incoming model has to displace, so ask for
# it back explicitly rather than paying for the eviction mid-attempt.
evict() { ollama stop "${1#openai:}" >/dev/null 2>&1 || true; }

sweep() {  # model
  local model="$1"
  echo "### $model ($(date -u '+%H:%M:%S'))"
  lane "$model" "${LANE_A[@]}" &
  local a=$!
  lane "$model" "${LANE_B[@]}" &
  local b=$!
  wait $a $b
  tally "$model"
  evict "$model"
}

for model in openai:qwen3:8b openai:llama3.1:latest openai:mistral:latest; do
  sweep "$model"
done

# The 14b is ~9 GB at q4. It runs alone and only on the three toolbox repos --
# the same easy subset scripts/study_run.sh gave it, for the within-family size
# comparison in H3c.
echo "### openai:qwen2.5-coder:14b, easy subset only ($(date -u '+%H:%M:%S'))"
for repo in toolbox gotoolbox jstoolbox; do
  run_one openai:qwen2.5-coder:14b "$repo"
done
tally openai:qwen2.5-coder:14b

cat results/main-*.jsonl > results/study-all.jsonl
echo
echo "TOTAL ATTEMPTS: $(wc -l < results/study-all.jsonl)  ($(date -u '+%H:%M:%S'))"
