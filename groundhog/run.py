#!/usr/bin/env python3
"""Race models against validated tasks.

Each model gets the repo at the parent commit with the new tests already in
place, plus the failing test output. It may read and write source files but not
the tests. When it stops, we run the tests once more and that is the score --
no partial credit, no judge model, no rubric.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .agents import build_prompt, run_external
from .environment import DetectionFailed, detect, ensure
from .models import ProviderError, Usage, connect, price_of
from .validate import git, passing, run, source_roots, tail

SYSTEM = """You are fixing a bug in a real codebase.

A test suite has been added that fails. Your job is to change the source code so
those tests pass. Work directly in the repository using the tools provided.

Rules:
- Do NOT modify any test file. Changing the tests is failing the task.
- Make the smallest change that genuinely fixes the problem.
- Use run_tests to check your work before you finish.
- When the tests pass, say DONE and stop."""

MAX_FILE_BYTES = 120_000


@dataclass
class Attempt:
    repo: str
    task_id: str
    subject: str
    model: str
    run_index: int = 0
    solved: bool = False
    turns: int = 0
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    nudges: int = 0
    agent: str = "builtin"
    broke_pass_to_pass: int = 0
    tampered_with_tests: bool = False
    error: str = ""
    files_touched: list[str] = field(default_factory=list)


def tools_for(test_files: set[str]) -> list[dict]:
    return [
        {
            "name": "list_files",
            "description": "List files under a directory, relative to the repo root.",
            "schema": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "directory, default '.'"}},
            },
        },
        {
            "name": "read_file",
            "description": "Read a file from the repository.",
            "schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Overwrite a source file with new contents. Test files are rejected.",
            "schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
        {
            "name": "run_tests",
            "description": "Run the task's test suite and return the output.",
            "schema": {"type": "object", "properties": {}},
        },
    ]


class Workspace:
    """A checkout the model is allowed to edit, with the tests write-protected."""

    def __init__(self, repo: Path, task: dict, venv: Path | None, test_cmd: str, timeout: int):
        self.repo = repo
        self.task = task
        self.venv = venv
        self.timeout = timeout
        self.tmp = Path(tempfile.mkdtemp(prefix=f"groundhog-run-{task['sha'][:8]}-"))
        self.tree = self.tmp / "repo"
        self.test_files = set(task["test_files"])
        self.test_cmd = test_cmd.format(tests=" ".join(task["test_files"]))
        self.touched: list[str] = []

        git(repo, "worktree", "add", "--detach", "--quiet", str(self.tree), task["parent"])
        git(self.tree, "checkout", task["sha"], "--", *task["test_files"])
        self.env = {"PYTHONPATH": ":".join(str(r) for r in source_roots(self.tree))}

        # An external agent has a shell and can rewrite anything, so tool-level
        # restrictions are not enough. Fingerprint the tests now and check them
        # afterwards: editing the test is failing the task, however it happened.
        self.test_fingerprint = self._fingerprint_tests()

    def _fingerprint_tests(self) -> dict[str, str]:
        out = {}
        for rel in self.test_files:
            path = self.tree / rel
            if path.is_file():
                out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out

    def tests_were_modified(self) -> list[str]:
        """Test files whose contents changed since setup."""
        return [
            rel
            for rel, digest in self.test_fingerprint.items()
            if self._fingerprint_tests().get(rel) != digest
        ]

    def close(self) -> None:
        git(self.repo, "worktree", "remove", "--force", str(self.tree), check=False)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _resolve(self, rel: str) -> Path | None:
        """Keep the model inside the worktree."""
        target = (self.tree / rel).resolve()
        if not str(target).startswith(str(self.tree.resolve())):
            return None
        return target

    def run_tests(self) -> tuple[bool, str]:
        result = run(self.test_cmd, self.tree, self.timeout, self.venv, self.env)
        return result.passed, result.output

    def score(self) -> tuple[bool, int, str]:
        """Grade the attempt: (solved, pass_to_pass broken, output).

        Solved means every FAIL_TO_PASS test now passes *and* every
        PASS_TO_PASS test still does. Without the second half, deleting the
        rest of the suite would read as a fix.
        """
        verbose = self.test_cmd.replace(" -x ", " ").replace(" -q", "") + " -v --tb=no"
        result = run(verbose, self.tree, self.timeout, self.venv, self.env)
        now = passing(result.output)

        f2p = self.task.get("fail_to_pass") or []
        p2p = self.task.get("pass_to_pass") or []
        if not f2p:
            # Task validated before F2P was recorded; fall back to exit status.
            return result.passed, 0, result.output

        broken = [t for t in p2p if t not in now]
        solved = all(t in now for t in f2p) and not broken
        return solved, len(broken), result.output

    def call(self, name: str, args: dict) -> str:
        """Dispatch a tool call. A bad argument is feedback, never a crash."""
        try:
            return self._call(name, args)
        except Exception as exc:
            return f"error: {type(exc).__name__}: {exc}"

    def _call(self, name: str, args: dict) -> str:
        if name == "list_files":
            base = self._resolve(args.get("path") or ".")
            if base is None or not base.exists():
                return "error: no such directory"
            if not base.is_dir():
                return f"error: {args.get('path')} is a file, not a directory"
            names = sorted(p.name + ("/" if p.is_dir() else "") for p in base.iterdir())
            return "\n".join(names[:400]) or "(empty)"

        if name == "read_file":
            target = self._resolve(args.get("path", ""))
            if target is None or not target.is_file():
                return "error: no such file"
            data = target.read_text(errors="replace")
            if len(data) > MAX_FILE_BYTES:
                return data[:MAX_FILE_BYTES] + "\n... (truncated)"
            return data

        if name == "write_file":
            rel = args.get("path", "")
            if rel in self.test_files or "/tests/" in f"/{rel}" or rel.startswith("tests/"):
                return "error: test files are read-only for this task"
            target = self._resolve(rel)
            if target is None:
                return "error: path outside the repository"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args.get("content", ""))
            if rel not in self.touched:
                self.touched.append(rel)
            return f"wrote {rel} ({len(args.get('content', ''))} bytes)"

        if name == "run_tests":
            passed, output = self.run_tests()
            return ("TESTS PASS\n\n" if passed else "TESTS FAIL\n\n") + tail(output, 40)

        return f"error: unknown tool {name}"


def changed_files(ws: "Workspace") -> list[str]:
    """Source files the agent actually modified, from git's point of view."""
    out = git(ws.tree, "status", "--porcelain", check=False)
    files = []
    for line in out.splitlines():
        rel = line[3:].strip()
        if rel and rel not in ws.test_files:
            files.append(rel)
    return files


def finish(record: Attempt, ws: "Workspace", started: float) -> None:
    """Grade an attempt and close its workspace. Applies to any agent."""
    try:
        solved, broken, _ = ws.score()
        record.solved = solved
        record.broke_pass_to_pass = broken
        tampered = ws.tests_were_modified()
        if tampered:
            record.tampered_with_tests = True
            record.solved = False
            record.error = f"modified test files: {', '.join(tampered[:3])}"
    except Exception as exc:
        record.error = record.error or f"scoring failed: {type(exc).__name__}: {exc}"[:200]
    finally:
        ws.close()
        record.seconds = round(time.monotonic() - started, 1)


def attempt(repo: Path, task: dict, model: str, cfg: argparse.Namespace, run_index: int = 0) -> Attempt:
    record = Attempt(repo=repo.name, task_id=task["sha"][:12], subject=task["subject"], model=model, run_index=run_index)
    started = time.monotonic()
    ws = Workspace(repo, task, cfg.venv, cfg.test_cmd, cfg.timeout)

    if getattr(cfg, "agent_cmd", None):
        record.agent = "external"
        try:
            _, failure = ws.run_tests()
            outcome = run_external(
                ws.tree,
                cfg.agent_cmd,
                build_prompt(task, failure, ws.test_cmd),
                ws.test_cmd,
                cfg.agent_timeout,
                cfg.venv,
            )
            record.error = outcome.error
            record.files_touched = changed_files(ws)
        except Exception as exc:
            record.error = f"{type(exc).__name__}: {exc}"[:200]
        finally:
            finish(record, ws, started)
        return record

    try:
        _, failure = ws.run_tests()
        chat = connect(model, SYSTEM)
        chat.say(
            f"Repository: {repo.name}\n"
            f"Tests that must pass: {', '.join(task['test_files'])}\n\n"
            f"Current failure:\n```\n{tail(failure, 60)}\n```\n\n"
            "Fix the source so these tests pass."
        )

        tools = tools_for(ws.test_files)
        solved = False
        for turn in range(cfg.max_turns):
            record.turns = turn + 1
            reply = chat.reply(tools)

            if reply.done:
                # Never take "I'm finished" on trust -- check, and push back once
                # or twice if it isn't true. Without this we would be measuring
                # which model gives up most politely.
                solved, _, output = ws.score()
                if solved or record.nudges >= cfg.max_nudges:
                    break
                record.nudges += 1
                chat.say(
                    "The tests still fail. You have not finished.\n\n"
                    f"```\n{tail(output, 40)}\n```\n\n"
                    + ("Call write_file to change the source -- you have not edited anything yet."
                       if not ws.touched
                       else "Keep going: read the relevant source, then call write_file with a fix.")
                )
                continue

            chat.give_results(
                [(c.id, ws.call(c.name, c.args if isinstance(c.args, dict) else {})) for c in reply.tool_calls]
            )

        final_solved, broken, _ = ws.score()
        record.solved = solved or final_solved
        record.broke_pass_to_pass = broken

        tampered = ws.tests_were_modified()
        if tampered:
            record.tampered_with_tests = True
            record.solved = False
            record.error = f"modified test files: {', '.join(tampered[:3])}"
        record.input_tokens = chat.usage.input_tokens
        record.output_tokens = chat.usage.output_tokens
        record.cost_usd = price_of(model, chat.usage)
        record.files_touched = ws.touched
    except ProviderError as exc:
        record.error = str(exc)[:200]
    except Exception as exc:  # a crashed attempt is a failed attempt, not a crashed run
        record.error = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        ws.close()
        record.seconds = round(time.monotonic() - started, 1)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=Path)
    ap.add_argument("--models", default="external", help="comma separated, e.g. anthropic:claude-opus-5,openai:gpt-5.2")
    ap.add_argument("--tasks", type=Path, default=Path("tasks/validated.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("results/results.jsonl"))
    ap.add_argument("--venv", type=Path, help="virtualenv to run tests inside (auto-built if omitted)")
    ap.add_argument("--no-auto-env", action="store_true")
    ap.add_argument("--test-cmd", default="python -m pytest {tests} -x -q")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--max-nudges", type=int, default=2, help="times to push back on a premature finish")
    ap.add_argument("--agent-cmd", help=(
        "run an external agent instead of the built-in loop. Placeholders: "
        "{prompt_file} {prompt} {repo} {test_cmd}. "
        "e.g. --agent-cmd \"aider --yes --message-file {prompt_file}\""
    ))
    ap.add_argument("--agent-timeout", type=int, default=900, help="seconds an external agent may run")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repeats", type=int, default=1,
                    help="attempts per task; >1 is required for any claim about a small effect")
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

    tasks = [json.loads(l) for l in cfg.tasks.read_text().splitlines() if l.strip()]
    if cfg.limit:
        tasks = tasks[: cfg.limit]
    models = [m.strip() for m in cfg.models.split(",") if m.strip()]
    if not tasks:
        print("no tasks -- run mine and validate first", file=sys.stderr)
        return 1

    total = len(models) * len(tasks) * cfg.repeats
    print(f"{len(models)} models x {len(tasks)} tasks x {cfg.repeats} repeats = {total} attempts\n", file=sys.stderr)
    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    records: list[Attempt] = []

    with cfg.out.open("w") as fh:
        for model in models:
            solved = attempted = 0
            for run_index in range(cfg.repeats):
                for i, task in enumerate(tasks, 1):
                    tag = f" r{run_index + 1}" if cfg.repeats > 1 else ""
                    label = f"{model:<32}{tag} [{i}/{len(tasks)}] {task['subject'][:34]}"
                    print(f"{label:<88}", end="", flush=True, file=sys.stderr)
                    rec = attempt(cfg.repo, task, model, cfg, run_index)
                    records.append(rec)
                    fh.write(json.dumps(asdict(rec)) + "\n")
                    fh.flush()
                    solved += rec.solved
                    attempted += 1
                    mark = "PASS" if rec.solved else ("ERROR" if rec.error else "fail")
                    print(f"  {mark:<5} {rec.seconds:>6.1f}s  {rec.error[:40]}", file=sys.stderr)
            print(f"{'':<32} -> {solved}/{attempted}\n", file=sys.stderr)

    print(f"{len(records)} attempts -> {cfg.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
