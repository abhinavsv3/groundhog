#!/usr/bin/env python3
"""One command from a repository to a table.

    groundhog bench pallets/click --agent claude-code
    groundhog bench ~/src/myrepo --models ollama:qwen3:8b --limit 10

mine -> validate -> run -> report, with the paths threaded for you, under
.groundhog/<repo>/. The second run finds the task set already there and
skips straight to the race, resuming any attempts that were interrupted.
Pass --fresh to mine again.

Each step is printed as the command it runs, so this doubles as a worked
example of the four-command form once you want to change something.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .repos import repo_arg

BOLD, DIM, CYAN, RED, RESET = "\033[1m", "\033[2m", "\033[36m", "\033[31m", "\033[0m"


def say(msg: str = "") -> None:
    print(msg, file=sys.stderr)


def step(title: str, command: list[str], colour: bool) -> int:
    b, d, c, r = (BOLD, DIM, CYAN, RESET) if colour else ("", "", "", "")
    say(f"\n{b}{title}{r}")
    shown = " ".join(("groundhog" if i == 0 else part) for i, part in enumerate(command[2:], 0)) \
        if command[:2] == [sys.executable, "-m"] else " ".join(command)
    say(f"{c}$ {shown.replace('groundhog groundhog', 'groundhog')}{r}")
    return subprocess.run(command, stdin=subprocess.DEVNULL).returncode


def env_flags(cfg: argparse.Namespace, for_validate: bool = False) -> list[str]:
    """Environment options forwarded verbatim.

    --env-cmd goes to validate only: it is recorded in each task and run
    reads it from there, so the two can never disagree.
    """
    flags: list[str] = []
    if cfg.env_cmd and for_validate:
        flags += ["--env-cmd", cfg.env_cmd]
    if cfg.test_cmd:
        flags += ["--test-cmd", cfg.test_cmd]
    if cfg.venv:
        flags += ["--venv", str(cfg.venv)]
    if cfg.no_auto_env:
        flags.append("--no-auto-env")
    return flags


def count(path: Path) -> int:
    return sum(1 for l in path.read_text().splitlines() if l.strip()) if path.exists() else 0


def suggest_models() -> str:
    """Something that will work on this machine, for the no-model error."""
    from .models import HOSTS, base_url_for
    from .doctor import ollama_models
    lines = []
    models = ollama_models(base_url_for(HOSTS["ollama"]))
    if models:
        coder = next((m for m in models if "coder" in m or "qwen3" in m), models[0])
        lines.append(f"  --models ollama:{coder:<28} a model you already have pulled")
    import os
    if os.environ.get("ANTHROPIC_API_KEY"):
        lines.append(f"  --models anthropic:claude-sonnet-5{'':<12} ANTHROPIC_API_KEY is set")
    from .agents import PRESETS
    import shutil
    for name, p in PRESETS.items():
        if shutil.which(p.binary):
            lines.append(f"  --agent {name:<32} {p.binary} is on PATH")
    if not lines:
        lines.append("  --models ollama:qwen3:8b          after `ollama pull qwen3:8b`")
        lines.append("  --agent claude-code               after installing Claude Code")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=repo_arg, help="path or URL")
    ap.add_argument("--models", help="comma separated provider:model list")
    ap.add_argument("--agent", help="a preset external agent (see `groundhog run --list-agents`)")
    ap.add_argument("--agent-cmd", help="any external agent command")
    ap.add_argument("--since", default="12 months ago", help="how far back to mine")
    ap.add_argument("--limit", type=int, default=20, help="tasks to run (0 for all)")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--p2p-scope", choices=("file", "full"))
    ap.add_argument("--stability", type=int, default=2,
                    help="re-run the fixed state N times to catch flaky tests (default 2)")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--max-spend", type=float)
    ap.add_argument("--env-cmd")
    ap.add_argument("--test-cmd")
    ap.add_argument("--venv", type=Path, help="use this environment instead of building one")
    ap.add_argument("--no-auto-env", action="store_true",
                    help="do not detect or build an environment (with --test-cmd)")
    ap.add_argument("--out-dir", type=Path, help="default .groundhog/<repo name>/")
    ap.add_argument("--fresh", action="store_true", help="mine and validate again, discard results")
    ap.add_argument("--no-color", action="store_true")
    cfg = ap.parse_args()

    colour = not cfg.no_color and sys.stderr.isatty()
    b, d, r = (BOLD, DIM, RESET) if colour else ("", "", "")
    if not (cfg.models or cfg.agent or cfg.agent_cmd):
        say(f"{RED if colour else ''}bench needs something to race.{r} On this machine you could use:\n"
            + suggest_models())
        return 2

    out = cfg.out_dir or Path(".groundhog") / cfg.repo.name
    out.mkdir(parents=True, exist_ok=True)
    candidates, tasks, results = out / "candidates.jsonl", out / "tasks.jsonl", out / "results.jsonl"
    py = [sys.executable, "-m", "groundhog"]

    if cfg.fresh:
        for path in (candidates, tasks, results):
            path.unlink(missing_ok=True)

    if tasks.exists() and count(tasks):
        say(f"{d}task set {tasks} already exists ({count(tasks)} tasks); --fresh to rebuild{r}")
    else:
        code = step("1. Mine commits that changed source and tests together",
                    [*py, "mine", str(cfg.repo), "--since", cfg.since, "--out", str(candidates)], colour)
        if code or not count(candidates):
            say(f"\nnothing to validate. Try a wider window: --since \"3 years ago\"")
            return code or 1
        extra = env_flags(cfg, for_validate=True)
        if cfg.p2p_scope:
            extra += ["--p2p-scope", cfg.p2p_scope]
        code = step("2. Validate: tests must fail without the fix and pass with it",
                    [*py, "validate", str(cfg.repo), "--candidates", str(candidates), "--out", str(tasks),
                     "--stability", str(cfg.stability), "--timeout", str(cfg.timeout), *extra], colour)
        if code or not count(tasks):
            say("\nno task survived validation; see the reasons above")
            return code or 1

    run_cmd = [*py, "run", str(cfg.repo), "--tasks", str(tasks), "--out", str(results),
               "--repeats", str(cfg.repeats), "--jobs", str(cfg.jobs), "--timeout", str(cfg.timeout)]
    if cfg.limit:
        run_cmd += ["--limit", str(cfg.limit)]
    if cfg.models:
        run_cmd += ["--models", cfg.models]
    if cfg.agent:
        run_cmd += ["--agent", cfg.agent]
    if cfg.agent_cmd:
        run_cmd += ["--agent-cmd", cfg.agent_cmd]
    if cfg.max_spend is not None:
        run_cmd += ["--max-spend", str(cfg.max_spend)]
    run_cmd += env_flags(cfg)
    if results.exists() and count(results):
        run_cmd.append("--resume")
    code = step("3. Race", run_cmd, colour)
    if code:
        return code

    code = step("4. Report",
                [*py, "report", "--results", str(results), "--tasks", str(tasks),
                 "--repo", cfg.repo.name, "--markdown", str(out / "report.md"),
                 "--site", str(out / "index.html"), "--title", f"Groundhog: {cfg.repo.name}"], colour)
    subprocess.run([*py, "badge", "--results", str(results), "--out", str(out / "badge.json")],
                   capture_output=True)
    say(f"\n{d}everything is under {out}/: tasks.jsonl (pin this), results.jsonl, "
        f"patches/, report.md, index.html, badge.json{r}")
    say(f"{d}inspect one attempt:  groundhog show <task-id> --tasks {tasks} --results {results}{r}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
