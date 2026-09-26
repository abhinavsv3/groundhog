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
import hashlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .environment import DetectionFailed, detect, ensure
from .repos import repo_arg


@dataclass
class RunResult:
    passed: bool
    duration: float
    output: str


def wrap(cmd: str, wrapper: str | None) -> str:
    """Apply a user-supplied environment wrapper to a test command.

    Auto-detection handles pyproject, dependency groups and requirements files.
    It does not handle conda, pixi, nix or docker, and it deliberately will not
    call an LLM to infer them -- mining and validation work with no API key, and
    that is worth more than the last slice of coverage. This is the escape
    hatch: Groundhog need not understand your environment, only be told how to
    enter it.

        --env-cmd "conda run -n myenv {cmd}"
    """
    if not wrapper:
        return cmd
    return wrapper.replace("{cmd}", shlex.quote(cmd)) if "{cmd}" in wrapper else f"{wrapper} {cmd}"


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


def js_outcomes(output: str) -> dict[str, str]:
    """Parse vitest/jest JSON output. Both use the same result shape."""
    start = output.find("{")
    while start != -1:
        try:
            report = json.loads(output[start:])
            break
        except json.JSONDecodeError:
            start = output.find("{", start + 1)
    else:
        return {}
    if start == -1 or not isinstance(report, dict):
        return {}

    results: dict[str, str] = {}
    for suite in report.get("testResults", []):
        path = suite.get("name") or suite.get("testFilePath") or "?"
        rel = Path(path).name
        for case in suite.get("assertionResults", []):
            name = case.get("fullName") or case.get("title")
            status = (case.get("status") or "").lower()
            if not name:
                continue
            results[f"{rel}::{name}"] = {
                "passed": "PASSED", "failed": "FAILED",
                "pending": "SKIPPED", "skipped": "SKIPPED",
            }.get(status, status.upper())
    return results


def link_node_modules(source: Path, tree: Path) -> None:
    """Borrow the clone's node_modules, minus any self-reference.

    Installing per worktree would dominate the cost of a run, so the worktree
    symlinks the clone's node_modules. That reintroduces Python's
    editable-install hazard in Node form: a package that imports itself by name
    (`import {x} from "mylib"`) resolves through node_modules/mylib, which
    points back at the ORIGINAL clone -- so the agent's edits are invisible and
    every test passes regardless. Dropping the self-link forces those imports
    to fail loudly instead of silently succeeding against the wrong code.
    """
    modules = source / "node_modules"
    link = tree / "node_modules"
    if not modules.is_dir() or link.exists():
        return
    link.symlink_to(modules, target_is_directory=True)

    try:
        name = json.loads((tree / "package.json").read_text()).get("name")
    except (json.JSONDecodeError, OSError):
        return
    if not name:
        return
    self_ref = modules / name
    if self_ref.exists():
        shadow = tree / ".groundhog-no-self-link"
        shadow.mkdir(exist_ok=True)
        for entry in modules.iterdir():
            if entry.name != name.split("/")[0]:
                (shadow / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
        link.unlink()
        shadow.rename(link)


def go_outcomes(output: str) -> dict[str, str]:
    """Parse `go test -json` events into per-test outcomes.

    Package-qualified, because `TestParse` may exist in several packages and
    FAIL_TO_PASS sets must not collide.
    """
    results: dict[str, str] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        name, action = event.get("Test"), event.get("Action")
        if not name or action not in ("pass", "fail", "skip"):
            continue
        results[f"{event.get('Package', '?')}::{name}"] = action.upper() + (
            "ED" if action == "pass" else ""
        )
    return {k: ("PASSED" if v.startswith("PASS") else v) for k, v in results.items()}


def test_targets(language: str, test_files: list[str]) -> str:
    """How to name the tests to run, for this language's test runner."""
    if language == "go":
        # Go runs packages, not files.
        dirs = sorted({str(Path(f).parent) for f in test_files})
        return " ".join(f"./{d}" if d != "." else "." for d in dirs)
    return " ".join(test_files)


def test_outcomes(output: str) -> dict[str, str]:
    """Parse per-test results out of a pytest run."""
    return {name: status for name, status in TEST_LINE.findall(output)}


def passing(output: str, language: str = "python") -> set[str]:
    parser = {"go": go_outcomes, "javascript": js_outcomes}.get(language, test_outcomes)
    outcomes = parser(output)
    return {n for n, st in outcomes.items() if st in ("PASSED", "XFAIL")}


def verbose_form(cmd: str, language: str) -> str:
    """The same test command, but reporting every individual test.

    pytest needs -v and must lose -x (which stops at the first failure, hiding
    the rest). `go test -json` is already per-test, and rewriting pytest flags
    into it would produce nonsense.
    """
    if language in ("go", "javascript"):
        return cmd  # already per-test via -json / --reporter=json
    return cmd.replace(" -x ", " ").replace(" -q", "") + " -v --tb=no"


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


def manifest(repo: Path, tasks: list[dict], cfg) -> dict:
    """A stable identity for a task set.

    `compare` pairs on task ids and silently intersects whatever it is handed,
    so re-mining with a different window between two runs quietly shrinks the
    comparison rather than failing. The hash makes that detectable, and makes
    `groundhog --version` plus a hash a sufficient citation -- which is what the
    README currently asks people to assemble by hand.
    """
    ids = sorted(t["sha"] for t in tasks)
    digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]
    dates = sorted(t.get("date", "") for t in tasks if t.get("date"))
    return {
        "repo": repo.name,
        "head": git(repo, "rev-parse", "HEAD").strip(),
        "task_count": len(tasks),
        "commit_range": [dates[0][:10], dates[-1][:10]] if dates else [],
        "mined_since": getattr(cfg, "since", None),
        "p2p_scope": getattr(cfg, "p2p_scope", "file"),
        "deduplicated": not getattr(cfg, "keep_duplicates", False),
        "hash": digest,
    }


def deduplicate(tasks: list[dict]) -> tuple[list[dict], list[tuple[dict, dict]]]:
    """Collapse tasks that test the same change.

    A repo with a revert cycle -- deprecate, revert, re-apply -- yields three
    commits that are one change. An agent solving one solves all three, so
    counting them separately triples that change's weight in the pass rate and,
    worse, breaks the independence assumption behind every confidence interval
    and p-value Groundhog prints.

    Three signals, merged transitively:

    1. Identical FAIL_TO_PASS sets -- the same tests flip, so it is the same task.
    2. Identical subject AND overlapping source files. Subject alone would merge
       every commit called "fix tests"; requiring shared source files makes it
       safe.
    3. A revert naming its target's subject. git records this verbatim.

    Signal 1 alone is not enough: in pallets/click's isolated_filesystem cycle
    the same test moved between files, so the two halves had disjoint F2P sets
    despite being one change. Signal 3 links them through the revert.

    The newest task in each group survives, since it reflects the code as it
    stands.
    """
    parent: dict[int, int] = {i: i for i in range(len(tasks))}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_targets: dict[frozenset, int] = {}
    by_subject: dict[str, list[int]] = {}
    for i, task in enumerate(tasks):
        targets = frozenset(task.get("fail_to_pass") or [])
        if targets:
            if targets in by_targets:
                union(i, by_targets[targets])
            else:
                by_targets[targets] = i
        by_subject.setdefault(task["subject"].strip(), []).append(i)

    for indexes in by_subject.values():
        for a, b in zip(indexes, indexes[1:]):
            if set(tasks[a].get("source_files", [])) & set(tasks[b].get("source_files", [])):
                union(a, b)

    for i, task in enumerate(tasks):
        target_subject = (task.get("reverts") or "").strip()
        for j in by_subject.get(target_subject, []):
            union(i, j)

    groups: dict[int, list[dict]] = {}
    for i, task in enumerate(tasks):
        groups.setdefault(find(i), []).append(task)

    kept: list[dict] = []
    dropped: list[tuple[dict, dict]] = []
    for group in groups.values():
        group.sort(key=lambda t: t.get("date", ""), reverse=True)
        kept.append(group[0])
        dropped.extend((duplicate, group[0]) for duplicate in group[1:])

    kept.sort(key=lambda t: t.get("date", ""), reverse=True)
    return kept, dropped


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

        language = getattr(cfg, "language", "python")

        # Bring in the commit's tests, but none of its source.
        git(tree, "checkout", sha, "--", *task["test_files"])
        if language == "javascript":
            link_node_modules(repo, tree)

        test_cmd = cfg.test_cmd.format(tests=test_targets(language, task["test_files"]))
        # PASS_TO_PASS scope. "file" watches only the task's own test files,
        # which is cheap and one file wide -- an agent that breaks a different
        # module goes unnoticed. "full" watches the whole suite.
        scope = getattr(cfg, "p2p_scope", None) or "file"
        env_cmd = getattr(cfg, "env_cmd", None)
        p2p_cmd = cfg.test_cmd.format(tests="./..." if language == "go" else "") \
            if scope == "full" else test_cmd
        # -v without -x: we need every test's outcome, not an early exit
        verbose_cmd = wrap(verbose_form(test_cmd, language), env_cmd)
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
        was, now = passing(before.output, language), passing(after.output, language)
        fail_to_pass = sorted(now - was)

        if scope == "full":
            # Re-run the whole suite in the fixed state: everything green here
            # must stay green, wherever in the repo it lives.
            wide = run(wrap(verbose_form(p2p_cmd, language), env_cmd),
                       tree, cfg.timeout * 3, env_path, pypath)
            pass_to_pass = sorted(passing(wide.output, language) - set(fail_to_pass))
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
            language=language,
            env_cmd=env_cmd or "",
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
    ap.add_argument("repo", type=repo_arg, help="path or URL")
    ap.add_argument("--candidates", type=Path, default=Path("tasks/candidates.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("tasks/validated.jsonl"))
    ap.add_argument("--test-cmd", default="python -m pytest {tests} -x -q")
    ap.add_argument("--venv", type=Path, help="virtualenv to run tests inside (auto-built if omitted)")
    ap.add_argument("--no-auto-env", action="store_true", help="do not build an environment automatically")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--env-cmd", help='wrap every test command, e.g. "conda run -n myenv {cmd}"')
    ap.add_argument("--p2p-scope", choices=("file", "full"), default=None,
                    help="which tests must stay green: the task's own files, or the whole "
                         "suite. Defaults to file for Python, full for Go.")
    ap.add_argument("--limit", type=int, default=0, help="stop after N candidates")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="keep tasks defined by the same tests flipping")
    cfg = ap.parse_args()

    if cfg.venv is None and not cfg.no_auto_env:
        try:
            plan = detect(cfg.repo)
            cfg.language = plan.language
            if cfg.p2p_scope is None:
                # Go needs full scope, not as a preference but for correctness:
                # a missing function fails compilation, so every test in the
                # package lands in FAIL_TO_PASS and PASS_TO_PASS is empty --
                # zero protection against an agent breaking something else.
                cfg.p2p_scope = "full" if plan.language == "go" else "file"
                if plan.language == "go":
                    print("using --p2p-scope full: Go compilation failures leave "
                          "nothing for file scope to protect", file=sys.stderr)
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

    duplicates: list[tuple[dict, dict]] = []
    if not cfg.keep_duplicates:
        valid, duplicates = deduplicate(valid)

    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    stamp = manifest(cfg.repo, valid, cfg)
    with cfg.out.open("w") as fh:
        for task in valid:
            fh.write(json.dumps({**task, "manifest": stamp["hash"]}) + "\n")
    cfg.out.with_suffix(".manifest.json").write_text(json.dumps(stamp, indent=2))

    if duplicates:
        print(f"\ndropped {len(duplicates)} duplicate task(s) -- same tests flip, "
              f"so they are not independent observations:", file=sys.stderr)
        for duplicate, survivor in duplicates[:6]:
            print(f"  {duplicate['sha'][:12]}  {duplicate['subject'][:52]}", file=sys.stderr)
            print(f"      -> same change as {survivor['sha'][:12]} "
                  f"{survivor['subject'][:40]}", file=sys.stderr)
        print("  (--keep-duplicates to retain them)", file=sys.stderr)

    elapsed = time.monotonic() - started
    print(f"\n{len(valid)}/{len(tasks)} became real tasks in {elapsed:.0f}s -> {cfg.out}", file=sys.stderr)
    print(f"task set {stamp['hash']}  ({stamp['commit_range'][0] if stamp['commit_range'] else '?'} .. {stamp['commit_range'][-1] if stamp['commit_range'] else '?'})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
