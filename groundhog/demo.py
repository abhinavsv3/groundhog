#!/usr/bin/env python3
"""A self-contained demonstration: `python -m groundhog demo`.

Builds a small repository with real git history, mines tasks from it, validates
them, and runs four scripted agents against them. No API key, no network, about
a minute.

The agents are deliberate: one fixes the bug, one fixes it *and* breaks another
module, one fixes it *and* edits the test, one does nothing. Only the first
should score. That is the whole claim this project makes, and it is checkable
here without trusting a README.

Every command is printed before it runs, so the demo doubles as a tutorial for
the CLI.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BOLD, DIM, GREEN, RED, CYAN, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[36m", "\033[0m",
)

# (name, source file, implementation, tests). Deliberately varied so the
# change-shape breakdown has something to contrast.
FEATURES = [
    ("clamp", "text.py",
     'def clamp(value, low, high):\n    """Constrain value to [low, high]."""\n'
     '    return max(low, min(value, high))\n',
     'def test_clamp():\n    assert text.clamp(5, 0, 10) == 5\n'
     '    assert text.clamp(-3, 0, 10) == 0\n'),
    ("dedupe", "text.py",
     'def dedupe(items):\n    """Remove duplicates, preserving first-seen order."""\n'
     '    seen, out = set(), []\n    for item in items:\n'
     '        if item not in seen:\n            seen.add(item)\n            out.append(item)\n'
     '    return out\n',
     'def test_dedupe():\n    assert text.dedupe([1, 2, 1, 3]) == [1, 2, 3]\n'),
    ("truncate", "text.py",
     'def truncate(value, length, suffix="..."):\n    """Shorten value to length characters."""\n'
     '    if len(value) <= length:\n        return value\n'
     '    return value[: length - len(suffix)] + suffix\n',
     'def test_truncate():\n    assert text.truncate("hello world", 8) == "hello..."\n'
     '    assert text.truncate("short", 10) == "short"\n'),
    ("safe_divide", "text.py",
     'def safe_divide(numerator, denominator, default=0.0):\n'
     '    """Divide, returning default when the denominator is zero."""\n'
     '    if denominator == 0:\n        return default\n'
     '    return numerator / denominator\n',
     'def test_safe_divide():\n    assert text.safe_divide(6, 3) == 2\n'
     '    assert text.safe_divide(1, 0) == 0.0\n'),
]

NUMBERS_MODULE = '''"""Numeric helpers, in a separate module.

This exists so the demo can show an agent passing its own test while breaking
something else -- which a FAIL_TO_PASS-only harness would score as a fix.
"""


def mean(values):
    return sum(values) / len(values) if values else 0.0


def median(values):
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2
'''

NUMBERS_TESTS = '''from demolib import numbers


def test_mean():
    assert numbers.mean([1, 2, 3]) == 2


def test_median():
    assert numbers.median([3, 1, 2]) == 2
    assert numbers.median([4, 1, 3, 2]) == 2.5
'''


def say(message: str = "") -> None:
    print(message, file=sys.stderr)


def shorten(part: str, workspace: Path) -> str:
    """Temp paths are noise; the shape of the command is the point."""
    part = part.replace(str(workspace), "$DEMO")
    if part == sys.executable:
        return "python"
    return part


def run_step(command: list[str], cwd: Path | None = None, show: bool = True,
             workspace: Path | None = None) -> str:
    if show:
        parts = [shorten(c, workspace) for c in command] if workspace else command
        say(f"{CYAN}$ {' '.join(parts)}{RESET}")
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    return result.stdout + result.stderr


def build_repo(root: Path) -> None:
    """A repo whose history contains fixes, a refactor, and a revert cycle."""
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    root.mkdir(parents=True, exist_ok=True)
    (root / "demolib").mkdir()
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demolib"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n\n'
        '[dependency-groups]\ntests = ["pytest"]\n\n'
        '[build-system]\nrequires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"\n\n'
        '[tool.setuptools]\npackages = ["demolib"]\n'
    )
    (root / "demolib" / "__init__.py").write_text("")
    (root / "demolib" / "text.py").write_text('"""Text helpers."""\n')
    (root / "tests" / "test_text.py").write_text("from demolib import text  # noqa: F401\n")

    git("init", "-q")
    git("config", "user.email", "dev@example.com")
    git("config", "user.name", "Demo Developer")
    git("add", "-A")
    git("commit", "-q", "-m", "initial")

    # A second module, so cross-module damage is possible.
    (root / "demolib" / "numbers.py").write_text(NUMBERS_MODULE)
    (root / "tests" / "test_numbers.py").write_text(NUMBERS_TESTS)
    git("add", "-A")
    git("commit", "-q", "-m", "Add numbers module")

    src = root / "demolib" / "text.py"
    tst = root / "tests" / "test_text.py"
    for name, _, impl, test in FEATURES:
        src.write_text(src.read_text() + "\n\n" + impl)
        tst.write_text(tst.read_text() + "\n\n" + test)
        git("add", "-A")
        git("commit", "-q", "-m", f"Add {name}()")

    # A pure refactor: behaviour identical, tests touched. Validation must
    # reject this -- no amount of diff analysis could.
    src.write_text(src.read_text().replace(
        "    return max(low, min(value, high))",
        "    constrained = max(low, min(value, high))\n    return constrained",
    ))
    tst.write_text(tst.read_text() + "\n\ndef test_clamp_exact():\n    assert text.clamp(7, 0, 10) == 7\n")
    git("add", "-A")
    git("commit", "-q", "-m", "Tidy clamp internals")


AGENTS = [
    ("honest", "applies the real fix", GREEN,
     "git checkout {sha} -- {source}"),
    ("saboteur", "fixes it, and breaks another module", RED,
     "git checkout {sha} -- {source} && "
     "printf '\\n\\ndef mean(values):\\n    return 999\\n' >> demolib/numbers.py"),
    ("test-editor", "fixes it, and edits the test", RED,
     "git checkout {sha} -- {source} && echo '# tampered' >> {test}"),
    ("idle", "does nothing at all", DIM, "true"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", type=Path, help="build the demo repo here and leave it behind")
    ap.add_argument("--limit", type=int, default=3, help="tasks to run each agent against")
    cfg = ap.parse_args()

    workspace = cfg.keep or Path(tempfile.mkdtemp(prefix="groundhog-demo-"))
    repo = workspace / "demolib"
    out = workspace / "out"
    out.mkdir(parents=True, exist_ok=True)

    say(f"\n{BOLD}1. A repository with real history{RESET}")
    say(f"{DIM}   Four bug fixes, one pure refactor, two modules.{RESET}")
    build_repo(repo)
    log = run_step(["git", "-C", str(repo), "log", "--oneline"], show=False)
    for line in log.strip().splitlines():
        say(f"   {DIM}{line}{RESET}")

    say(f"\n{BOLD}2. Mine tasks from that history{RESET}")
    say(f"{DIM}   Commits that changed source and tests together.{RESET}")
    candidates = out / "candidates.jsonl"
    say(run_step([sys.executable, "-m", "groundhog", "mine", str(repo),
                  "--since", "10 years ago", "--out", str(candidates)],
                 workspace=workspace).strip())

    say(f"\n{BOLD}3. Validate them{RESET}")
    say(f"{DIM}   Tests must fail without the fix and pass with it. No venv or{RESET}")
    say(f"{DIM}   test command supplied -- it reads pyproject.toml itself.{RESET}")
    tasks = out / "tasks.jsonl"
    output = run_step([sys.executable, "-m", "groundhog", "validate", str(repo),
                       "--candidates", str(candidates), "--out", str(tasks),
                       "--p2p-scope", "full"], workspace=workspace)
    for line in output.strip().splitlines():
        marker = GREEN if "VALID" in line else (DIM if "REJECTED" in line else "")
        say(f"   {marker}{line}{RESET}")
    say(f"\n{DIM}   Note the refactor was rejected: its tests passed without the fix.{RESET}")

    rows = [json.loads(l) for l in tasks.read_text().splitlines() if l.strip()]
    if not rows:
        say(f"{RED}no tasks validated — the demo cannot continue{RESET}")
        return 1
    rows = rows[: cfg.limit]
    subset = out / "subset.jsonl"
    subset.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    say(f"\n{BOLD}4. Run four agents against {len(rows)} task(s){RESET}")
    say(f"{DIM}   Only the honest one should score.{RESET}\n")

    combined = out / "results.jsonl"
    combined.write_text("")
    for name, description, colour, template in AGENTS:
        say(f"   {colour}{BOLD}{name}{RESET} — {description}")
        for index, task in enumerate(rows):
            # Each task gets its own commit, so the scripted agent is doing
            # exactly the thing its name claims and nothing more.
            single = out / f"task-{index}.jsonl"
            single.write_text(json.dumps(task) + "\n")
            results = out / f"{name}-{index}.jsonl"
            command = template.format(
                sha=task["sha"],
                source=" ".join(task["source_files"]),
                test=" ".join(task["test_files"]),
            )
            run_step([sys.executable, "-m", "groundhog", "run", str(repo),
                      "--tasks", str(single), "--out", str(results),
                      "--models", name, "--agent-cmd", command,
                      "--no-patches", "--timeout", "120"], show=False)
            line = results.read_text().strip()
            if not line:
                continue
            record = json.loads(line)
            combined.write_text(combined.read_text() + line + "\n")
            verdict = f"{GREEN}solved{RESET}" if record["solved"] else f"{RED}not solved{RESET}"
            why = ""
            if record["tampered_with_tests"]:
                why = f" {DIM}(edited a test file){RESET}"
            elif record["broke_pass_to_pass"]:
                why = f" {DIM}(broke {record['broke_pass_to_pass']} passing test(s)){RESET}"
            elif record["fail_to_pass_passed"] and not record["solved"]:
                why = f" {DIM}(its own test passed, but…){RESET}"
            say(f"     {record['subject'][:34]:<36}{verdict}{why}")
        say()

    say(f"{BOLD}5. The report{RESET}")
    say(run_step([sys.executable, "-m", "groundhog", "report",
                  "--results", str(combined), "--tasks", str(tasks)], show=False))

    say(f"{BOLD}What this showed{RESET}")
    say("  - tasks mined from ordinary git history, with no hand-labelling")
    say("  - a pure refactor rejected, because its tests passed without the fix")
    say("  - an agent that fixed the bug and broke another module: not solved")
    say("  - an agent that edited the test to pass: not solved")
    say(f"\n{DIM}  A FAIL_TO_PASS-only harness would have scored the middle two as "
        f"fixes.{RESET}")

    if cfg.keep:
        say(f"\n{DIM}kept at {workspace}{RESET}")
    else:
        shutil.rmtree(workspace, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
