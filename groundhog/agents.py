#!/usr/bin/env python3
"""Run an external coding agent against a prepared task.

Groundhog ships a small agent loop, which is fine for exercising the harness and
useless for answering the question most teams actually have: does *our* setup
work on *our* code? Your agent has its own tools, prompt, retrieval and loop, and
measuring ours tells you nothing about it.

So: Groundhog prepares the worktree, hands your agent a prompt, gets out of the
way, and grades whatever it left behind. Anything you can start from a shell
works -- aider, SWE-agent, Claude Code, opencode, a script of your own.

    groundhog run <repo> --agent-cmd "aider --yes --message-file {prompt_file}"

Scoring is unchanged and deliberately outside your agent's reach: the recorded
FAIL_TO_PASS tests must pass, the PASS_TO_PASS tests must still pass, and the
test files must be byte-identical to what we handed over.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

PROMPT = """The test suite below fails in this repository.

Tests that must pass:
{tests}

Current failure:
{failure}

Change the source code so those tests pass. Do not modify any test file --
editing the tests fails the task. You can run the tests with:

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
