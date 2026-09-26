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

from .agents import AgentMissing, build_prompt, describe_presets, preset, run_external
from .environment import DetectionFailed, detect, ensure
from .models import ProviderError, Usage, connect, price_of
from .repos import repo_arg
from .validate import (git, link_node_modules, passing, run, source_roots,
                       tail, verbose_form, wrap)

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
class ToolStats:
    """How an attempt went, not just whether it succeeded.

    Solve rate on 100 tasks gives 100 observations, mostly zeros for a small
    model. The same runs produce thousands of tool calls, and those differ
    between models even when nobody solves anything -- which is the only way to
    measure a laptop-sized model, or to detect an effect too small to move a
    pass rate.
    """

    calls: int = 0
    errors: int = 0              # a tool returned an error string
    unknown_tool: int = 0        # called something that does not exist
    malformed_args: int = 0      # arguments were not a usable object
    recovered_from_text: int = 0 # tool call arrived as prose, not a tool_call
    first_edit_turn: int | None = None
    by_name: dict[str, int] = field(default_factory=dict)

    def record(self, name: str, result: str, turn: int, known: set[str]) -> None:
        self.calls += 1
        self.by_name[name] = self.by_name.get(name, 0) + 1
        if name not in known:
            self.unknown_tool += 1
        if result.startswith("error:"):
            self.errors += 1
            if "arguments" in result or "no such" in result:
                self.malformed_args += 1
        elif name == "write_file" and self.first_edit_turn is None:
            self.first_edit_turn = turn


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
    fail_to_pass_passed: bool = False  # would a FAIL_TO_PASS-only harness call this solved?
    p2p_scope: str = "file"
    manifest: str = ""
    patch_file: str = ""
    tools: dict = field(default_factory=dict)
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
        from .validate import test_targets
        self.test_cmd = test_cmd.format(
            tests=test_targets(task.get("language", "python"), task["test_files"])
        )
        self.touched: list[str] = []
        self.stats = ToolStats()
        self.turn = 0
        self.known_tools = {"list_files", "read_file", "write_file", "run_tests"}

        git(repo, "worktree", "add", "--detach", "--quiet", str(self.tree), task["parent"])
        git(self.tree, "checkout", task["sha"], "--", *task["test_files"])
        self.env = {"PYTHONPATH": ":".join(str(r) for r in source_roots(self.tree))}
        if task.get("language") == "javascript":
            link_node_modules(repo, self.tree)

        # An external agent has a shell and can rewrite anything, so tool-level
        # restrictions are not enough. Fingerprint the tests now and check them
        # afterwards: editing the test is failing the task, however it happened.
        self.test_fingerprint = self._fingerprint_tests()
        self.last_fail_to_pass_met = False

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
        result = run(wrap(self.test_cmd, self.task.get("env_cmd")),
                     self.tree, self.timeout, self.venv, self.env)
        return result.passed, result.output

    def score(self) -> tuple[bool, int, str]:
        """Grade the attempt: (solved, pass_to_pass broken, output).

        Solved means every FAIL_TO_PASS test now passes *and* every
        PASS_TO_PASS test still does. Without the second half, deleting the
        rest of the suite would read as a fix.
        """
        # Grade against whatever scope the task was validated at. A pass rate
        # measured at "file" scope and one at "full" scope are not comparable.
        language = self.task.get("language", "python")
        command = self.task.get("p2p_cmd") or self.test_cmd
        verbose = wrap(verbose_form(command, language), self.task.get("env_cmd"))
        budget = self.timeout * (3 if self.task.get("p2p_scope") == "full" else 1)
        result = run(verbose, self.tree, budget, self.venv, self.env)
        now = passing(result.output, language)

        f2p = self.task.get("fail_to_pass") or []
        p2p = self.task.get("pass_to_pass") or []
        if not f2p:
            # Task validated before F2P was recorded; fall back to exit status.
            return result.passed, 0, result.output

        broken = [t for t in p2p if t not in now]
        target_met = all(t in now for t in f2p)
        self.last_fail_to_pass_met = target_met
        solved = target_met and not broken
        return solved, len(broken), result.output

    def call(self, name: str, args: dict) -> str:
        """Dispatch a tool call. A bad argument is feedback, never a crash."""
        try:
            result = self._call(name, args)
        except Exception as exc:
            result = f"error: {type(exc).__name__}: {exc}"
        self.stats.record(name, result, self.turn, self.known_tools)
        return result

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


def spent_so_far(path: Path) -> float:
    """Cost already recorded in an output file, for resumed runs."""
    if not path.exists():
        return 0.0
    total = 0.0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            total += json.loads(line).get("cost_usd") or 0.0
        except (json.JSONDecodeError, TypeError):
            continue
    return total


def already_done(path: Path) -> set[tuple[str, str, int]]:
    """(task_id, model, run_index) triples already recorded in an output file.

    Records are written and flushed as each attempt completes, so an
    interrupted run leaves everything finished up to that point. What was
    missing was any way to pick it back up -- which matters when a run is
    hours of paid API calls and a rate limit means starting over.
    """
    if not path.exists():
        return set()
    done: set[tuple[str, str, int]] = set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            done.add((row["task_id"], row["model"], int(row.get("run_index", 0))))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # a truncated final line is expected after a hard kill
    return done


def patch_dir(cfg: argparse.Namespace) -> Path | None:
    if getattr(cfg, "no_patches", False):
        return None
    return Path(getattr(cfg, "out", Path("results/results.jsonl"))).parent / "patches"


def changed_files(ws: "Workspace") -> list[str]:
    """Source files the agent modified, whether or not it committed them.

    Diffing against the task's parent commit rather than HEAD matters for
    external agents: aider auto-commits by default, and `git status` on a
    clean tree would report that such an agent did nothing.
    """
    committed = git(ws.tree, "diff", "--name-only", ws.task["parent"], check=False)
    pending = git(ws.tree, "status", "--porcelain", check=False)
    files: list[str] = []
    for rel in [*committed.splitlines(), *(l[3:] for l in pending.splitlines())]:
        rel = rel.strip()
        if not rel or rel in ws.test_files or rel in files or is_build_noise(rel):
            continue
        files.append(rel)
    return files


NOISE_DIRS = ("__pycache__", ".pytest_cache", "node_modules", ".mypy_cache", ".ruff_cache")


def is_build_noise(rel: str) -> bool:
    """Running the tests writes caches into the tree; the agent did not."""
    parts = rel.strip("/").split("/")
    return (
        any(p in NOISE_DIRS for p in parts)
        or rel.endswith((".pyc", ".egg-info", ".egg-info/"))
    )


def save_patch(ws: "Workspace", record: Attempt, out_dir: Path) -> None:
    """Keep the diff. It is the only record of *how* an attempt failed.

    Where fail_to_pass_passed disagrees with solved, this file is the
    explanation -- and without it that comparison is uninspectable after the
    worktree is destroyed.
    """
    try:
        # -N marks untracked files intent-to-add so they appear in the diff
        # without staging their contents; "diff HEAD" then captures staged and
        # unstaged work alike. Plain "git diff" misses both, which silently
        # hides any agent that stages its edits or creates a new file.
        git(ws.tree, "add", "-A", "-N", check=False)
        # Exclude the task's test files: Groundhog checked those out itself as
        # part of setup, so leaving them in makes the patch look like the agent
        # edited the tests. Real test edits are caught by tests_were_modified().
        excludes = [
            ":(exclude)**/__pycache__/**", ":(exclude)*.pyc",
            ":(exclude).pytest_cache/**", ":(exclude)**/*.egg-info/**",
            *[f":(exclude){rel}" for rel in ws.test_files],
        ]
        # Against the parent commit, not HEAD: an agent that committed its
        # work would otherwise leave an empty diff and look idle.
        diff = git(ws.tree, "diff", ws.task["parent"], "--", ".", *excludes, check=False)
        if not diff.strip():
            return
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_model = record.model.replace("/", "_").replace(":", "-")
        path = out_dir / f"{record.task_id}.{safe_model}.{record.run_index}.diff"
        path.write_text(diff)
        record.patch_file = str(path)

        human = out_dir / f"{record.task_id}.human.diff"
        if not human.exists():
            gold = git(ws.repo, "show", ws.task["sha"], "--", *ws.task["source_files"], check=False)
            if gold.strip():
                human.write_text(gold)
    except Exception:
        pass  # a missing patch must never fail an otherwise good attempt


def finish(record: Attempt, ws: "Workspace", started: float,
           out_dir: Path | None = None, solved_already: bool = False) -> None:
    """Grade an attempt and close its workspace.

    The single scoring path for every agent. It was two for a while -- this one
    and a copy inlined in the built-in loop -- and the copy silently drifted:
    the built-in loop never saved a patch and never recorded p2p_scope, because
    an edit that was supposed to add them did not match and nobody checked.
    One path means that cannot happen again.
    """
    try:
        record.p2p_scope = ws.task.get("p2p_scope", "file")
        record.manifest = ws.task.get("manifest", "")
        record.tools = asdict(ws.stats)
        record.files_touched = record.files_touched or ws.touched
        if out_dir is not None:
            save_patch(ws, record, out_dir)
        solved, broken, _ = ws.score()
        record.solved = solved or solved_already
        record.broke_pass_to_pass = broken
        record.fail_to_pass_passed = ws.last_fail_to_pass_met
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
    solved = False
    ws = Workspace(repo, task, cfg.venv, cfg.test_cmd, cfg.timeout)

    if getattr(cfg, "agent_cmd", None):
        record.agent = getattr(cfg, "agent", None) or "external"
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
            finish(record, ws, started, patch_dir(cfg))
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
        for turn in range(cfg.max_turns):
            record.turns = turn + 1
            ws.turn = turn + 1
            reply = chat.reply(tools)
            ws.stats.recovered_from_text += sum(
                1 for c in reply.tool_calls if c.id.startswith("text_")
            )

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

        record.input_tokens = chat.usage.input_tokens
        record.output_tokens = chat.usage.output_tokens
        record.cost_usd = price_of(model, chat.usage)
        record.files_touched = ws.touched
    except ProviderError as exc:
        record.error = str(exc)[:200]
    except Exception as exc:  # a crashed attempt is a failed attempt, not a crashed run
        record.error = f"{type(exc).__name__}: {exc}"[:200]
    finally:
        finish(record, ws, started, patch_dir(cfg), solved_already=solved)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=repo_arg, help="path or URL")
    ap.add_argument("--models", default="external",
                    help="comma separated, e.g. anthropic:claude-opus-5,ollama:qwen3:8b "
                         "(see --list-providers)")
    ap.add_argument("--list-providers", action="store_true",
                    help="print every provider prefix with its endpoint and key variable")
    ap.add_argument("--tasks", type=Path, default=Path("tasks/validated.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("results/results.jsonl"))
    ap.add_argument("--venv", type=Path, help="virtualenv to run tests inside (auto-built if omitted)")
    ap.add_argument("--no-auto-env", action="store_true")
    ap.add_argument("--test-cmd", default="python -m pytest {tests} -x -q")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--max-turns", type=int, default=25)
    ap.add_argument("--max-nudges", type=int, default=2, help="times to push back on a premature finish")
    which = ap.add_mutually_exclusive_group()
    which.add_argument("--agent", help=(
        "run a known coding agent by name: claude-code, aider, codex, opencode, "
        "gemini, cursor, copilot, goose, amp (see --list-agents)"
    ))
    which.add_argument("--agent-cmd", help=(
        "run any external agent instead of the built-in loop. Placeholders: "
        "{prompt_file} {prompt} {repo} {test_cmd}. "
        "e.g. --agent-cmd \"aider --yes --message-file {prompt_file}\""
    ))
    ap.add_argument("--list-agents", action="store_true", help="print the preset table and exit")
    ap.add_argument("--agent-timeout", type=int, default=900, help="seconds an external agent may run")
    ap.add_argument("--no-patches", action="store_true", help="do not save each attempt's diff")
    ap.add_argument("--resume", action="store_true",
                    help="append to --out, skipping attempts already recorded there")
    ap.add_argument("--max-spend", type=float, default=None,
                    help="stop cleanly once cumulative cost would exceed this (USD)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--repeats", type=int, default=1,
                    help="attempts per task; >1 is required for any claim about a small effect")
    if "--list-agents" in sys.argv:
        print(describe_presets())
        return 0
    if "--list-providers" in sys.argv:
        from .models import describe_providers
        rows = describe_providers()
        width = max(len(r[0]) for r in rows) + 2
        for prefix, url, unlock in rows:
            print(f"{prefix:<{width}}{url:<62}{unlock}")
        return 0
    cfg = ap.parse_args()

    if cfg.agent:
        try:
            cfg.agent_cmd = preset(cfg.agent).command
        except AgentMissing as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if cfg.models == ap.get_default("models"):
            # Label the row by the agent, not the word "external".
            cfg.models = cfg.agent

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
    done = already_done(cfg.out) if cfg.resume else set()
    print(f"{len(models)} models x {len(tasks)} tasks x {cfg.repeats} repeats = {total} attempts",
          file=sys.stderr)
    if cfg.resume:
        planned = {
            (t["sha"][:12], m, r)
            for t in tasks for m in models for r in range(cfg.repeats)
        }
        skipping = len(planned & done)
        print(f"resuming: {skipping} already recorded, {total - skipping} to run",
              file=sys.stderr)
    print(file=sys.stderr)

    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    records: list[Attempt] = []
    spent = spent_so_far(cfg.out) if cfg.resume else 0.0
    skipped_for_budget = 0
    unpriced: set[str] = set()

    if cfg.max_spend is not None and spent:
        print(f"budget: ${spent:.2f} already spent, ceiling ${cfg.max_spend:.2f}\n",
              file=sys.stderr)

    with cfg.out.open("a" if cfg.resume else "w") as fh:
        for model in models:
            solved = attempted = 0
            for run_index in range(cfg.repeats):
                for i, task in enumerate(tasks, 1):
                    if (task["sha"][:12], model, run_index) in done:
                        continue
                    tag = f" r{run_index + 1}" if cfg.repeats > 1 else ""
                    label = f"{model:<32}{tag} [{i}/{len(tasks)}] {task['subject'][:34]}"
                    print(f"{label:<88}", end="", flush=True, file=sys.stderr)
                    if cfg.max_spend is not None and spent >= cfg.max_spend:
                        skipped_for_budget += 1
                        print("  SKIPPED (budget)", file=sys.stderr)
                        continue

                    rec = attempt(cfg.repo, task, model, cfg, run_index)
                    records.append(rec)
                    fh.write(json.dumps(asdict(rec)) + "\n")
                    fh.flush()

                    if rec.cost_usd is None:
                        # A ceiling that silently never triggers is worse than
                        # none, so say so rather than assuming zero.
                        unpriced.add(rec.model)
                    else:
                        spent += rec.cost_usd
                    solved += rec.solved
                    attempted += 1
                    mark = "PASS" if rec.solved else ("ERROR" if rec.error else "fail")
                    print(f"  {mark:<5} {rec.seconds:>6.1f}s  {rec.error[:40]}", file=sys.stderr)
            print(f"{'':<32} -> {solved}/{attempted}\n", file=sys.stderr)

    print(f"{len(records)} attempts -> {cfg.out}", file=sys.stderr)
    if cfg.max_spend is not None:
        print(f"spent ${spent:.2f} of ${cfg.max_spend:.2f}", file=sys.stderr)
    if skipped_for_budget:
        print(f"skipped {skipped_for_budget} attempts at the spend ceiling — "
              f"re-run with --resume and a higher --max-spend to continue",
              file=sys.stderr)
    if unpriced and cfg.max_spend is not None:
        print(f"WARNING: no pricing for {', '.join(sorted(unpriced))} — those "
              f"attempts did not count toward the ceiling. Set GROUNDHOG_PRICING.",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
