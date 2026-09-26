#!/usr/bin/env python3
"""Run an external coding agent against a prepared task.

Groundhog ships a small agent loop, which is fine for exercising the harness and
useless for answering the question most teams actually have: does *our* setup
work on *our* code? Your agent has its own tools, prompt, retrieval and loop, and
measuring ours tells you nothing about it.

So: Groundhog prepares the worktree, hands your agent a prompt, gets out of the
way, and grades whatever it left behind. Anything you can start from a shell
works -- aider, SWE-agent, Claude Code, opencode, a script of your own.

    groundhog run <repo> --agent claude-code
    groundhog run <repo> --agent-cmd "aider --yes --message-file {prompt_file}"

The first form uses a preset from PRESETS below; the second is the escape
hatch for anything else. Scoring is unchanged and deliberately outside your agent's reach: the recorded
FAIL_TO_PASS tests must pass, the PASS_TO_PASS tests must still pass, and the
test files must be byte-identical to what we handed over.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Preset:
    """A known-good non-interactive invocation of one coding agent."""

    name: str
    binary: str
    command: str
    install: str
    verified: bool = False   # someone has run this preset against the demo repo
    notes: str = ""


# Every preset must: read the task without a human, edit files without asking,
# and exit when done. Placeholders are the same ones --agent-cmd accepts.
#
# "verified" means the preset was run against `groundhog demo --keep` and
# produced a graded attempt. Unverified presets are the documented flags for
# that agent; if one is wrong, please fix it and flip the flag.
PRESETS: dict[str, Preset] = {
    "claude-code": Preset(
        "claude-code", "claude",
        "claude -p {prompt} --permission-mode bypassPermissions",
        "npm install -g @anthropic-ai/claude-code",
        verified=True,
        notes="bypassPermissions is safe here: the agent only ever sees a throwaway worktree",
    ),
    "aider": Preset(
        "aider", "aider",
        "aider --yes --no-auto-commits --no-check-update --message-file {prompt_file}",
        "pip install aider-chat",
        notes="--no-auto-commits keeps the diff in the working tree where it is graded",
    ),
    "codex": Preset(
        "codex", "codex",
        "codex exec --full-auto {prompt}",
        "npm install -g @openai/codex",
        notes="--full-auto edits and runs commands inside codex's own sandbox",
    ),
    "opencode": Preset(
        "opencode", "opencode",
        "opencode run {prompt}",
        "curl -fsSL https://opencode.ai/install | bash",
        verified=True,
        notes="pass -m provider/model through --agent-cmd to pin a model",
    ),
    "gemini": Preset(
        "gemini", "gemini",
        "gemini --yolo -p {prompt}",
        "npm install -g @google/gemini-cli",
        notes="--yolo auto-approves tool calls",
    ),
    "cursor": Preset(
        "cursor", "cursor-agent",
        "cursor-agent -p --force {prompt}",
        "curl https://cursor.com/install -fsS | bash",
        notes="--force applies edits without confirmation",
    ),
    "copilot": Preset(
        "copilot", "copilot",
        "copilot -p {prompt} --allow-all-tools",
        "npm install -g @github/copilot",
    ),
    "goose": Preset(
        "goose", "goose",
        "goose run --instructions {prompt_file}",
        "https://block.github.io/goose/docs/getting-started/installation",
    ),
    "amp": Preset(
        "amp", "amp",
        "amp -x {prompt}",
        "npm install -g @sourcegraph/amp",
        notes="-x is execute mode: one prompt, no TUI",
    ),
}


class AgentMissing(RuntimeError):
    """The preset's binary is not on PATH. Raised before any worktree is built."""


def preset(name: str) -> Preset:
    if name not in PRESETS:
        raise AgentMissing(
            f"no preset called {name!r}; choose from {', '.join(sorted(PRESETS))} "
            f"or pass --agent-cmd"
        )
    p = PRESETS[name]
    if shutil.which(p.binary) is None:
        raise AgentMissing(
            f"--agent {name} needs `{p.binary}` on PATH and it is not there.\n"
            f"  install: {p.install}"
        )
    return p


def describe_presets() -> str:
    width = max(len(n) for n in PRESETS) + 2
    lines = [f"{'AGENT':<{width}}{'ON PATH':<9}{'VERIFIED':<10}COMMAND"]
    for name, p in PRESETS.items():
        here = "yes" if shutil.which(p.binary) else "no"
        lines.append(f"{name:<{width}}{here:<9}{'yes' if p.verified else 'no':<10}{p.command}")
        if p.notes:
            lines.append(f"{'':<{width}}{'':<19}  {p.notes}")
    lines.append("")
    lines.append("Unverified presets are the agent's documented flags, not a tested run.")
    lines.append("Anything else: --agent-cmd with {prompt_file} {prompt} {repo} {test_cmd}.")
    return "\n".join(lines)

PROMPT = """The test suite below fails in this repository.

Tests that must pass:
{tests}

Current failure:
{failure}

Change the source code so those tests pass. Do not modify any test file --
editing the tests fails the task. Do not commit; leave your changes in the
working tree. You can run the tests with:

    {test_cmd}
"""


@dataclass
class AgentRun:
    seconds: float = 0.0
    exit_code: int | None = None
    timed_out: bool = False
    output: str = ""
    command: str = ""
    error: str = ""


def build_prompt(task: dict, failure: str, test_cmd: str) -> str:
    return PROMPT.format(
        tests="\n".join(f"  - {t}" for t in (task.get("fail_to_pass") or task["test_files"])),
        failure=failure.strip()[-4000:],
        test_cmd=test_cmd,
    )


def render(command: str, **values: str) -> str:
    """Fill {placeholders} in an agent command, quoting every substitution.

    Values reach a shell, and a task subject can contain anything a commit
    message can, so nothing is interpolated unquoted.
    """
    out = command
    for key, value in values.items():
        out = out.replace("{" + key + "}", shlex.quote(str(value)))
    return out


def run_external(
    tree: Path,
    command: str,
    prompt: str,
    test_cmd: str,
    timeout: int,
    venv: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> AgentRun:
    """Run an agent command inside the prepared worktree."""
    prompt_file = tree / ".groundhog-task.md"
    prompt_file.write_text(prompt)

    rendered = render(
        command,
        prompt_file=str(prompt_file),
        prompt=prompt,
        repo=str(tree),
        test_cmd=test_cmd,
    )

    env = dict(os.environ)
    if venv is not None:
        env["PATH"] = f"{venv / 'bin'}:{env['PATH']}"
        env["VIRTUAL_ENV"] = str(venv)
    env.update(extra_env or {})
    # Agents commonly look for these; make the task discoverable without them
    # having to be told where it is.
    env["GROUNDHOG_PROMPT_FILE"] = str(prompt_file)
    env["GROUNDHOG_TEST_CMD"] = test_cmd

    started = time.monotonic()
    result = AgentRun(command=rendered)
    try:
        proc = subprocess.run(
            rendered,
            shell=True,
            cwd=str(tree),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        result.exit_code = proc.returncode
        result.output = (proc.stdout + proc.stderr)[-20000:]
    except subprocess.TimeoutExpired as exc:
        result.timed_out = True
        result.output = (exc.stdout or b"").decode(errors="replace")[-8000:] if exc.stdout else ""
        result.error = f"agent timed out after {timeout}s"
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.seconds = time.monotonic() - started
        # Don't leave our scratch file in the diff we are about to grade.
        prompt_file.unlink(missing_ok=True)
    return result
