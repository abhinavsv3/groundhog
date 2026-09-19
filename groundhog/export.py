#!/usr/bin/env python3
"""Export validated tasks in SWE-bench's instance format.

Groundhog's output is the same shape SWE-bench consumes, so exporting makes
these tasks usable by SWE-agent, existing evaluation harnesses and training
pipelines instead of being a dead end. This plugs into that ecosystem rather
than competing with it.

One honest difference to know before mixing the two. SWE-bench's
`problem_statement` is a human-written GitHub issue -- vague, and part of what
it tests is understanding it. Groundhog has no issue text; a task is defined by
its failing tests. The exported `problem_statement` says so explicitly rather
than dressing a commit subject up as a bug report, because an agent given an
exact executable spec is doing a different job from one given a complaint.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

STATEMENT = """{subject}

The following tests fail in this repository and must pass:

{tests}

Note: this task was generated from a commit by Groundhog, not from a GitHub
issue. There is no human bug report -- the failing tests are the specification.
"""


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args],
                            capture_output=True, text=True)
    return result.stdout


def diff_for(repo: Path, sha: str, paths: list[str]) -> str:
    if not paths:
        return ""
    return git(repo, "diff", f"{sha}~1", sha, "--", *paths)


def to_instance(repo: Path, repo_name: str, task: dict) -> dict:
    tests = task.get("fail_to_pass") or task["test_files"]
    return {
        "instance_id": f"{repo_name.replace('/', '__')}-{task['sha'][:12]}",
        "repo": repo_name,
        "base_commit": task["parent"],
        "patch": diff_for(repo, task["sha"], task["source_files"]),
        "test_patch": diff_for(repo, task["sha"], task["test_files"]),
        "problem_statement": STATEMENT.format(
            subject=task["subject"],
            tests="\n".join(f"  - {t}" for t in tests),
        ),
        "hints_text": "",
        "created_at": task.get("date", ""),
        "version": "groundhog",
        "FAIL_TO_PASS": json.dumps(task.get("fail_to_pass") or []),
        "PASS_TO_PASS": json.dumps(task.get("pass_to_pass") or []),
        "environment_setup_commit": task["parent"],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=Path, help="the repository the tasks were mined from")
    ap.add_argument("--tasks", type=Path, required=True, help="validated tasks")
    ap.add_argument("--out", type=Path, default=Path("swebench-instances.json"))
    ap.add_argument("--repo-name", help="owner/name as SWE-bench records it (default: directory name)")
    ap.add_argument("--jsonl", action="store_true", help="write JSON Lines instead of a JSON array")
    cfg = ap.parse_args()

    if not cfg.tasks.exists():
        print(f"no tasks at {cfg.tasks}", file=sys.stderr)
        return 1

    tasks = [json.loads(l) for l in cfg.tasks.read_text().splitlines() if l.strip()]
    repo_name = cfg.repo_name or cfg.repo.name
    instances, skipped = [], 0
    for task in tasks:
        instance = to_instance(cfg.repo, repo_name, task)
        if not instance["patch"]:
            # No gold patch means nothing to evaluate against; better to drop it
            # loudly than to export a task nobody can score.
            skipped += 1
            continue
        instances.append(instance)

    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    if cfg.jsonl:
        cfg.out.write_text("".join(json.dumps(i) + "\n" for i in instances))
    else:
        cfg.out.write_text(json.dumps(instances, indent=2))

    missing_p2p = sum(1 for i in instances if i["PASS_TO_PASS"] == "[]")
    print(f"{len(instances)} instances -> {cfg.out}", file=sys.stderr)
    if skipped:
        print(f"skipped {skipped} task(s) with no recoverable gold patch", file=sys.stderr)
    if missing_p2p:
        print(f"WARNING: {missing_p2p} instance(s) have an empty PASS_TO_PASS. "
              f"Re-validate with --p2p-scope full for meaningful regression "
              f"checking downstream.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
