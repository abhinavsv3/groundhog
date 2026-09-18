#!/usr/bin/env bash
# Run the whole benchmark against local Ollama models. Costs nothing.
#
#   ollama pull qwen2.5-coder:7b qwen3:8b llama3.1
#   REPOS_DIR=~/src scripts/local_run.sh
#
# Expects each repo to have a sibling virtualenv named <repo>venv with its test
# dependencies installed, and tasks/<repo>-validated.jsonl already mined.
set -uo pipefail

REPOS_DIR="${REPOS_DIR:?set REPOS_DIR to the directory holding the cloned repos}"
MODELS="${MODELS:-openai:qwen2.5-coder:7b,openai:qwen2.5-coder:14b,openai:qwen3:8b,openai:llama3.1:latest}"
MAX_TURNS="${MAX_TURNS:-14}"
OUT_DIR="${OUT_DIR:-results}"

export OPENAI_API_KEY="${OPENAI_API_KEY:-ollama}"
export GROUNDHOG_OPENAI_BASE_URL="${GROUNDHOG_OPENAI_BASE_URL:-http://localhost:11434/v1}"

mkdir -p "$OUT_DIR"

run_repo() {
  local name="$1" tasks="$2" limit="$3"
  echo "=========== $name ==========="
  python3 -m groundhog run "$REPOS_DIR/$name" \
    --models "$MODELS" \
    --tasks "$tasks" \
    --venv "$REPOS_DIR/${name}venv" \
    --out "$OUT_DIR/${name}.jsonl" \
    --limit "$limit" \
    --max-turns "$MAX_TURNS" \
    --timeout 120
}

run_repo httpx tasks/validated.jsonl 8
run_repo click tasks/click-validated.jsonl 10
run_repo rich  tasks/rich-validated.jsonl 10

cat "$OUT_DIR"/httpx.jsonl "$OUT_DIR"/click.jsonl "$OUT_DIR"/rich.jsonl > "$OUT_DIR/all.jsonl"
echo "combined -> $OUT_DIR/all.jsonl ($(wc -l < "$OUT_DIR/all.jsonl") attempts)"
