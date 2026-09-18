#!/usr/bin/env python3
"""Validate mined candidates by actually running the tests.

A candidate is only a real task if the tests fail without the source change and
pass with it. That single check is what separates a genuine bug fix from a
refactor or a formatting commit -- no amount of diff analysis can tell them
apart, but two test runs can.

For each candidate we build a worktree at the parent commit, copy in the new
tests, and run them twice:

    parent + new tests              -> must FAIL   (the task is solvable)
    parent + new tests + real fix   -> must PASS   (the task is fair)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    passed: bool
    duration: float
    output: str


def run(cmd: str, cwd: Path, timeout: int, env_path: Path | None = None) -> RunResult:
    started = time.monotonic()
    shell_env = None
    if env_path is not None:
        import os

        shell_env = dict(os.environ)
        shell_env["PATH"] = f"{env_path / 'bin'}:{shell_env['PATH']}"
        shell_env["VIRTUAL_ENV"] = str(env_path)
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=shell_env,
        )
        output = proc.stdout + proc.stderr
        passed = proc.returncode == 0
    except subprocess.TimeoutExpired:
        output = f"TIMEOUT after {timeout}s"
        passed = False
    return RunResult(passed=passed, duration=time.monotonic() - started, output=output)


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def tail(text: str, lines: int = 12) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def validate_one(
    repo: Path, task: dict, cfg: argparse.Namespace, env_path: Path | None
) -> dict:
    """Build a worktree and run the fail-to-pass check for one candidate."""
    sha, parent = task["sha"], task["parent"]
    work = Path(tempfile.mkdtemp(prefix=f"groundhog-{sha[:8]}-"))
    tree = work / "repo"
    verdict: dict = {"task_id": sha[:12], "subject": task["subject"]}

    try:
        git(repo, "worktree", "add", "--detach", "--quiet", str(tree), parent)

        # Bring in the commit's tests, but none of its source.
        git(tree, "checkout", sha, "--", *task["test_files"])

        test_cmd = cfg.test_cmd.format(tests=" ".join(task["test_files"]))

        before = run(test_cmd, tree, cfg.timeout, env_path)
        if before.passed:
            verdict.update(status="rejected", reason="tests already pass without the fix")
            return verdict

        # Now apply the real source change and confirm the tests go green.
        git(tree, "checkout", sha, "--", *task["source_files"])
        after = run(test_cmd, tree, cfg.timeout, env_path)
        if not after.passed:
            verdict.update(
                status="rejected",
                reason="tests still fail with the real fix (environment problem)",
                detail=tail(after.output),
            )
            return verdict

        verdict.update(
            status="valid",
            test_cmd=test_cmd,
            fail_seconds=round(before.duration, 1),
            pass_seconds=round(after.duration, 1),
            failure_excerpt=tail(before.output, 8),
        )
        return verdict
    except RuntimeError as exc:
        verdict.update(status="error", reason=str(exc))
        return verdict
    finally:
        git(repo, "worktree", "remove", "--force", str(tree), check=False)
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=Path)
    ap.add_argument("--candidates", type=Path, default=Path("tasks/candidates.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("tasks/validated.jsonl"))
    ap.add_argument("--test-cmd", default="python -m pytest {tests} -x -q")
    ap.add_argument("--venv", type=Path, help="virtualenv to run tests inside")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--limit", type=int, default=0, help="stop after N candidates")
    cfg = ap.parse_args()

    tasks = [json.loads(line) for line in cfg.candidates.read_text().splitlines() if line.strip()]
    if cfg.limit:
        tasks = tasks[: cfg.limit]

    print(f"validating {len(tasks)} candidates\n", file=sys.stderr)
    valid, results = [], []
    started = time.monotonic()

    for i, task in enumerate(tasks, 1):
        label = f"[{i}/{len(tasks)}] {task['sha'][:12]} {task['subject'][:48]}"
        print(f"{label:<70}", end="", flush=True, file=sys.stderr)
        verdict = validate_one(cfg.repo, task, cfg, cfg.venv)
        results.append(verdict)
        if verdict["status"] == "valid":
            valid.append({**task, **verdict})
            print(f"  VALID   ({verdict['fail_seconds']}s -> {verdict['pass_seconds']}s)", file=sys.stderr)
        else:
            print(f"  {verdict['status'].upper()}  {verdict.get('reason', '')[:44]}", file=sys.stderr)

    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    with cfg.out.open("w") as fh:
        for task in valid:
            fh.write(json.dumps(task) + "\n")

    elapsed = time.monotonic() - started
    print(f"\n{len(valid)}/{len(tasks)} became real tasks in {elapsed:.0f}s -> {cfg.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
