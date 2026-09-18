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
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import ProviderError, Usage, connect, price_of
from .validate import git, run, source_roots, tail

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
    task_id: str
    subject: str
    model: str
    solved: bool = False
    turns: int = 0
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
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

    def call(self, name: str, args: dict) -> str:
        if name == "list_files":
            base = self._resolve(args.get("path") or ".")
            if base is None or not base.exists():
                return "error: no such directory"
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


def attempt(repo: Path, task: dict, model: str, cfg: argparse.Namespace) -> Attempt:
    record = Attempt(task_id=task["sha"][:12], subject=task["subject"], model=model)
    started = time.monotonic()
    ws = Workspace(repo, task, cfg.venv, cfg.test_cmd, cfg.timeout)

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
        for turn in range(cfg.max_turns):
            record.turns = turn + 1
            reply = chat.reply(tools)
            if reply.done:
                break
            chat.give_results([(c.id, ws.call(c.name, c.args)) for c in reply.tool_calls])

        record.solved, _ = ws.run_tests()
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
    ap.add_argument("--models", required=True, help="comma separated, e.g. anthropic:claude-opus-5,openai:gpt-5.2")
    ap.add_argument("--tasks", type=Path, default=Path("tasks/validated.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("results/results.jsonl"))
    ap.add_argument("--venv", type=Path)
    ap.add_argument("--test-cmd", default="python -m pytest {tests} -x -q")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--limit", type=int, default=0)
    cfg = ap.parse_args()

    tasks = [json.loads(l) for l in cfg.tasks.read_text().splitlines() if l.strip()]
    if cfg.limit:
        tasks = tasks[: cfg.limit]
    models = [m.strip() for m in cfg.models.split(",") if m.strip()]
    if not tasks:
        print("no tasks -- run mine and validate first", file=sys.stderr)
        return 1

    print(f"{len(models)} models x {len(tasks)} tasks = {len(models) * len(tasks)} attempts\n", file=sys.stderr)
    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    records: list[Attempt] = []

    with cfg.out.open("w") as fh:
        for model in models:
            solved = 0
            for i, task in enumerate(tasks, 1):
                label = f"{model:<32} [{i}/{len(tasks)}] {task['subject'][:38]}"
                print(f"{label:<88}", end="", flush=True, file=sys.stderr)
                rec = attempt(cfg.repo, task, model, cfg)
                records.append(rec)
                fh.write(json.dumps(asdict(rec)) + "\n")
                fh.flush()
                solved += rec.solved
                mark = "PASS" if rec.solved else ("ERROR" if rec.error else "fail")
                print(f"  {mark:<5} {rec.seconds:>6.1f}s  {rec.error[:40]}", file=sys.stderr)
            print(f"{'':<32} -> {solved}/{len(tasks)}\n", file=sys.stderr)

    print(f"{len(records)} attempts -> {cfg.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
