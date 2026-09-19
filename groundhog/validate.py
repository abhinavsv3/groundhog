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
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .environment import DetectionFailed, detect, ensure


@dataclass
class RunResult:
    passed: bool
    duration: float
    output: str


def run(
    cmd: str,
    cwd: Path,
    timeout: int,
    env_path: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> RunResult:
    started = time.monotonic()
    shell_env = None
    if env_path is not None or extra_env:
        import os

        shell_env = dict(os.environ)
        if env_path is not None:
            shell_env["PATH"] = f"{env_path / 'bin'}:{shell_env['PATH']}"
            shell_env["VIRTUAL_ENV"] = str(env_path)
        shell_env.update(extra_env or {})
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


# pytest -v prints "path/to/test.py::test_name PASSED" (sometimes with params).
TEST_LINE = re.compile(r"^(\S+::\S+?)\s+(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)", re.M)


def test_outcomes(output: str) -> dict[str, str]:
    """Parse per-test results out of a pytest run."""
    return {name: status for name, status in TEST_LINE.findall(output)}


def passing(output: str) -> set[str]:
    return {n for n, st in test_outcomes(output).items() if st in ("PASSED", "XFAIL")}


def source_roots(tree: Path) -> list[Path]:
    """Directories that must shadow any installed copy of the package.

    An editable install points at the original clone, so a worktree using a
    src/ layout would import the *fixed* code no matter what the model wrote --
    every test passes and the task looks like a harmless refactor. Putting the
    worktree's own roots on PYTHONPATH is what stops that.
    """
    roots = [tree]
    if (tree / "src").is_dir():
        roots.insert(0, tree / "src")
    return roots


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
        # PASS_TO_PASS scope. "file" watches only the task's own test files,
        # which is cheap and one file wide -- an agent that breaks a different
        # module goes unnoticed. "full" watches the whole suite.
        scope = getattr(cfg, "p2p_scope", "file")
        p2p_cmd = cfg.test_cmd.format(tests="") if scope == "full" else test_cmd
        # -v without -x: we need every test's outcome, not an early exit
        verbose_cmd = test_cmd.replace(" -x ", " ").replace(" -q", "") + " -v --tb=no"
        pypath = {"PYTHONPATH": ":".join(str(r) for r in source_roots(tree))}

        before = run(verbose_cmd, tree, cfg.timeout, env_path, pypath)
        if before.passed:
            verdict.update(status="rejected", reason="tests already pass without the fix")
            return verdict

        # Now apply the real source change and confirm the tests go green.
        git(tree, "checkout", sha, "--", *task["source_files"])
        after = run(verbose_cmd, tree, cfg.timeout, env_path, pypath)
        if not after.passed:
            verdict.update(
                status="rejected",
                reason="tests still fail with the real fix (environment problem)",
                detail=tail(after.output),
            )
            return verdict

        # FAIL_TO_PASS: the tests the fix is *for*.
        # PASS_TO_PASS: tests already green that must stay green, which is what
        # stops an agent from "solving" a task by breaking everything around it.
        was, now = passing(before.output), passing(after.output)
        fail_to_pass = sorted(now - was)

        if scope == "full":
            # Re-run the whole suite in the fixed state: everything green here
            # must stay green, wherever in the repo it lives.
            wide = run(
                p2p_cmd.replace(" -x ", " ").replace(" -q", "") + " -v --tb=no",
                tree, cfg.timeout * 3, env_path, pypath,
            )
            pass_to_pass = sorted(passing(wide.output) - set(fail_to_pass))
        else:
            pass_to_pass = sorted(now & was)

        if not fail_to_pass:
            verdict.update(
                status="rejected",
                reason="no test flipped from failing to passing",
            )
            return verdict

        verdict.update(
            status="valid",
            test_cmd=test_cmd,
            p2p_scope=scope,
            p2p_cmd=p2p_cmd,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
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
    ap.add_argument("--venv", type=Path, help="virtualenv to run tests inside (auto-built if omitted)")
    ap.add_argument("--no-auto-env", action="store_true", help="do not build an environment automatically")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--p2p-scope", choices=("file", "full"), default="file",
                    help="which tests must stay green: the task's own files, or the whole suite")
    ap.add_argument("--limit", type=int, default=0, help="stop after N candidates")
    cfg = ap.parse_args()

    if cfg.venv is None and not cfg.no_auto_env:
        try:
            plan = detect(cfg.repo)
            if cfg.test_cmd == ap.get_default("test_cmd"):
                cfg.test_cmd = plan.test_cmd
            cfg.venv = ensure(cfg.repo, plan)
        except DetectionFailed as exc:
            print(f"\n{exc}\n", file=sys.stderr)
            return 1

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
            print(f"  VALID   F2P={len(verdict['fail_to_pass']):<3} P2P={len(verdict['pass_to_pass']):<4} "
                  f"({verdict['fail_seconds']}s -> {verdict['pass_seconds']}s)", file=sys.stderr)
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
