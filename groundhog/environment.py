#!/usr/bin/env python3
"""Work out how to install a repository and run its tests, without being told.

This is the difference between Groundhog taking five minutes and taking an
afternoon, which is the difference between people using it and not.

The approach is deliberately heuristic rather than LLM-driven: Groundhog has no
runtime dependencies and needs no API key to mine and validate tasks, and that
property is worth more than the last 20% of coverage. When detection fails it
says exactly what it tried and why, rather than guessing and silently producing
zero tasks -- a silent wrong answer is the worst outcome a benchmark can have.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import venv
from dataclasses import dataclass, field
from pathlib import Path

# requirements files, most-likely-to-be-test-deps first
REQUIREMENT_GLOBS = [
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "test-requirements.txt",
    "requirements/*.txt",
]

# Narrowest first: a "dev" group usually pulls in lint, docs and tox as well,
# which is slow to install and more likely to fail. Only fall back to it.
TEST_EXTRA_NAMES = ("test", "tests", "testing", "dev", "develop")


@dataclass
class Plan:
    """How to build an environment for one repository."""

    language: str
    install: list[str]
    test_cmd: str
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fingerprint(self, repo: Path) -> str:
        """Changes when the dependency declarations OR the plan change.

        Hashing only the dependency files was a bug: improving detection then
        left every existing user on a stale, wrongly-built environment, with no
        signal that anything was wrong. The install commands are part of the
        identity of the environment.
        """
        h = hashlib.sha256()
        h.update(sys.version.split()[0].encode())
        for command in self.install:
            h.update(command.encode())
        for name in sorted(self.evidence):
            path = repo / name
            if path.is_file():
                h.update(name.encode())
                h.update(path.read_bytes())
        return h.hexdigest()[:16]


class DetectionFailed(RuntimeError):
    """Raised with everything we looked for, so the user can fix it."""


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _toml(text: str) -> dict:
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - py3.10
        return {}
    try:
        return tomllib.loads(text)
    except Exception:
        return {}


def find_requirement_files(repo: Path) -> list[str]:
    found: list[str] = []
    for pattern in REQUIREMENT_GLOBS:
        for path in sorted(repo.glob(pattern)):
            if path.is_file():
                rel = str(path.relative_to(repo))
                if rel not in found:
                    found.append(rel)
    return found


def test_extras(pyproject: dict) -> list[str]:
    """Names under [project.optional-dependencies] that hold test deps.

    Only extras belong here. PEP 735 dependency groups look identical in the
    TOML but CANNOT be installed with `.[name]` syntax -- pip accepts the
    command, installs nothing, and exits 0. See resolve_groups().
    """
    optional = pyproject.get("project", {}).get("optional-dependencies", {})
    lowered = {k.lower(): k for k in optional}
    for candidate in TEST_EXTRA_NAMES:
        if candidate in lowered:
            return [lowered[candidate]]
    return []


def resolve_groups(pyproject: dict) -> list[str]:
    """Flatten PEP 735 [dependency-groups] test groups into requirement strings.

    Groups may include other groups, so this recurses. We resolve them ourselves
    rather than relying on `pip install --group`, which needs pip 25.1+; doing it
    here works on any pip and makes the installed set explicit.
    """
    groups = pyproject.get("dependency-groups", {})
    if not groups:
        return []

    def flatten(name: str, seen: set[str]) -> list[str]:
        if name in seen or name not in groups:
            return []
        seen.add(name)
        out: list[str] = []
        for entry in groups[name]:
            if isinstance(entry, str):
                out.append(entry)
            elif isinstance(entry, dict) and "include-group" in entry:
                out.extend(flatten(entry["include-group"], seen))
        return out

    lowered = {name.lower(): name for name in groups}
    for candidate in TEST_EXTRA_NAMES:
        if candidate in lowered:
            return flatten(lowered[candidate], set())
    return []


def uses_pytest(repo: Path, pyproject: dict, requirement_text: str) -> bool:
    if "pytest" in requirement_text:
        return True
    if "[tool.pytest" in _read(repo / "pyproject.toml"):
        return True
    if (repo / "pytest.ini").is_file() or (repo / "conftest.py").is_file():
        return True
    if "pytest" in json.dumps(pyproject):
        return True
    tox = _read(repo / "tox.ini") + _read(repo / "setup.cfg")
    return "pytest" in tox


def installer() -> list[str]:
    """Prefer uv when it is available -- it is dramatically faster."""
    if shutil.which("uv"):
        return ["uv", "pip", "install"]
    return ["pip", "install"]


def detect(repo: Path) -> Plan:
    """Figure out how to install this repo and run its tests."""
    looked_for: list[str] = []
    pyproject_text = _read(repo / "pyproject.toml")
    pyproject = _toml(pyproject_text)
    has_python_manifest = bool(pyproject_text) or (repo / "setup.py").is_file()
    requirement_files = find_requirement_files(repo)
    looked_for += ["pyproject.toml", "setup.py", *REQUIREMENT_GLOBS]

    if not has_python_manifest and not requirement_files:
        raise DetectionFailed(
            f"No Python project detected in {repo}.\n"
            f"Looked for: {', '.join(looked_for)}\n"
            "Only Python is supported so far -- pass --venv and --test-cmd "
            "explicitly, or see issues labelled 'language'."
        )

    pip = installer()
    install: list[str] = []
    evidence: list[str] = []
    notes: list[str] = []

    if has_python_manifest:
        extras = test_extras(pyproject)
        target = f".[{','.join(extras)}]" if extras else "."
        # NOTE: must run from the repo root. A requirements file containing
        # "-e ." resolves against the working directory, not the file's
        # location, which silently installs the wrong project.
        install.append(" ".join([*pip, "-e", target]))
        if pyproject_text:
            evidence.append("pyproject.toml")
        if (repo / "setup.py").is_file():
            evidence.append("setup.py")
        if extras:
            notes.append(f"test extras: {', '.join(extras)}")

        group_reqs = resolve_groups(pyproject)
        if group_reqs:
            quoted = " ".join(shlex.quote(r) for r in group_reqs)
            install.append(" ".join([*pip, quoted]))
            notes.append(f"resolved {len(group_reqs)} deps from [dependency-groups]")

    requirement_text = ""
    for rel in requirement_files:
        requirement_text += _read(repo / rel)
        install.append(" ".join([*pip, "-r", rel]))
        evidence.append(rel)

    if not uses_pytest(repo, pyproject, requirement_text):
        notes.append("no pytest signal found; installing it anyway and using it")
    install.append(" ".join([*pip, "pytest"]))

    return Plan(
        language="python",
        install=install,
        test_cmd="python -m pytest {tests} -x -q",
        evidence=evidence,
        notes=notes,
    )


def cache_root() -> Path:
    base = os.environ.get("GROUNDHOG_CACHE") or (Path.home() / ".cache" / "groundhog")
    return Path(base)


def ensure(repo: Path, plan: Plan | None = None, rebuild: bool = False, quiet: bool = False) -> Path:
    """Return a virtualenv for this repo, building it if necessary.

    Cached on the content of the dependency files, so a repo is built once and
    reused across runs -- and rebuilt automatically when its deps change.
    """
    plan = plan or detect(repo)
    target = cache_root() / f"{repo.name}-{plan.fingerprint(repo)}"
    marker = target / ".groundhog-ready"

    if marker.exists() and not rebuild:
        return target
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    def say(msg: str) -> None:
        if not quiet:
            print(msg, file=sys.stderr)

    say(f"building environment for {repo.name} -> {target}")
    for note in plan.notes:
        say(f"  note: {note}")

    target.parent.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True).create(target)

    env = dict(os.environ)
    env["PATH"] = f"{target / 'bin'}:{env['PATH']}"
    env["VIRTUAL_ENV"] = str(target)

    for command in plan.install:
        say(f"  $ {command}")
        proc = subprocess.run(
            command, shell=True, cwd=str(repo), env=env, capture_output=True, text=True
        )
        if proc.returncode != 0:
            tail = (proc.stdout + proc.stderr).strip().splitlines()[-12:]
            shutil.rmtree(target, ignore_errors=True)
            raise DetectionFailed(
                f"Environment setup failed for {repo.name}.\n\n"
                f"Command: {command}\n"
                f"Run from: {repo}\n\n"
                + "\n".join(tail)
                + "\n\nFix the command and pass --venv explicitly, or open an issue "
                "with this output."
            )

    marker.write_text(json.dumps({"repo": str(repo), "install": plan.install}, indent=2))
    say(f"  ready: {target}")
    return target


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Detect and build a repo's test environment.")
    ap.add_argument("repo", type=Path)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--plan-only", action="store_true", help="show the plan, build nothing")
    cfg = ap.parse_args()

    try:
        plan = detect(cfg.repo)
    except DetectionFailed as exc:
        print(exc, file=sys.stderr)
        return 1

    print(f"language:  {plan.language}")
    print(f"evidence:  {', '.join(plan.evidence) or 'none'}")
    for note in plan.notes:
        print(f"note:      {note}")
    print("install:")
    for command in plan.install:
        print(f"  $ {command}")
    print(f"test:      {plan.test_cmd}")

    if cfg.plan_only:
        return 0
    try:
        print(f"\nvenv:      {ensure(cfg.repo, plan, rebuild=cfg.rebuild)}")
    except DetectionFailed as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
