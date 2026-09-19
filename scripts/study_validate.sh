#!/usr/bin/env bash
# Validate the study corpus. Logs everything; never retries silently into success.
set -uo pipefail
REPOS_DIR="${REPOS_DIR:?}"
LIMIT="${LIMIT:-25}"
mkdir -p study/logs tasks

REPOS=(httpx click rich attrs typer flask jinja werkzeug starlette
       marshmallow structlog tenacity cattrs gotoolbox)

for n in "${REPOS[@]}"; do
  echo "=== $n ($(date -u '+%H:%M:%S')) ==="
  python3 -m groundhog validate "$REPOS_DIR/$n" \
      --candidates "tasks/$n-candidates.jsonl" \
      --out "tasks/$n-validated.jsonl" \
      --limit "$LIMIT" --timeout 300 \
      > "study/logs/validate-$n.log" 2>&1
  echo "  exit=$? $(tail -2 "study/logs/validate-$n.log" | head -1)"
done
echo "TOTAL VALIDATED: $(cat tasks/*-validated.jsonl 2>/dev/null | wc -l | tr -d ' ')"
