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


def _toml_parser():
    """tomllib on 3.11+, tomli if someone installed it, else None."""
    try:
        import tomllib
        return tomllib
    except ModuleNotFoundError:
        try:
            import tomli
            return tomli
        except ModuleNotFoundError:
            return None


NO_TOML_NOTE = ("pyproject.toml present but NOT parsed: this Python has no tomllib, so test "
                "extras and [dependency-groups] were missed. `pip install tomli`, or run "
                "Groundhog under Python 3.11+. Tasks rejected as \"environment problem\" "
                "are probably this.")


def _toml(text: str) -> dict:
    parser = _toml_parser()
    if parser is None:
        return {}
    try:
        return parser.loads(text)
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


# Lockfile -> package manager. Order matters: a repo may carry more than one,
# and the most specific lockfile wins.
JS_LOCKFILES = [
    ("pnpm-lock.yaml", "pnpm", "pnpm install --frozen-lockfile"),
    ("yarn.lock", "yarn", "yarn install --frozen-lockfile"),
    ("bun.lockb", "bun", "bun install --frozen-lockfile"),
    ("package-lock.json", "npm", "npm ci"),
]


def detect_js(repo: Path) -> Plan | None:
    """JavaScript and TypeScript, via whichever runner the project declares.

    vitest and jest both emit the same JSON result shape, so one parser covers
    the large majority of modern repos. mocha and node:test do not, and are
    left to --test-cmd rather than guessed at.
    """
    manifest = repo / "package.json"
    if not manifest.is_file():
        return None
    try:
        package = json.loads(manifest.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    deps = {**package.get("devDependencies", {}), **package.get("dependencies", {})}
    notes: list[str] = []

    install, manager = "npm install", "npm"
    for lockfile, name, command in JS_LOCKFILES:
        if (repo / lockfile).is_file():
            install, manager = command, name
            notes.append(f"{name} ({lockfile})")
            break
    else:
        notes.append("no lockfile; falling back to npm install")

    if "vitest" in deps:
        runner = "npx vitest run --reporter=json --passWithNoTests {tests}"
        notes.append("vitest")
    elif "jest" in deps:
        runner = "npx jest --json --passWithNoTests {tests}"
        notes.append("jest")
    else:
        return None  # mocha, node:test, ava -- let the user pass --test-cmd

    return Plan(
        language="javascript",
        install=[install],
        test_cmd=runner,
        evidence=["package.json"],
        notes=notes,
    )


def detect_go(repo: Path) -> Plan | None:
    """Go is the easy language: uniform tooling and no shadowing problem.

    `go build` always resolves packages from the directory tree it is run in,
    so the editable-install trap that cost this project twenty click tasks
    cannot occur. Dependencies come from go.mod; tests run per package.
    """
    if not (repo / "go.mod").is_file():
        return None
    return Plan(
        language="go",
        install=["go mod download"],
        # -json gives unambiguous per-test results with package names, which
        # bare -v output does not: two packages may each define TestParse.
        test_cmd="go test -json -count=1 {tests}",
        evidence=["go.mod"],
    )


def detect(repo: Path) -> Plan:
    """Figure out how to install this repo and run its tests."""
    for probe in (detect_go, detect_js):
        plan = probe(repo)
        if plan is not None:
            return plan

    looked_for: list[str] = []
    pyproject_text = _read(repo / "pyproject.toml")
    pyproject = _toml(pyproject_text)
    has_python_manifest = bool(pyproject_text) or (repo / "setup.py").is_file()
    requirement_files = find_requirement_files(repo)
    looked_for += ["pyproject.toml", "setup.py", *REQUIREMENT_GLOBS]

    if not has_python_manifest and not requirement_files:
        raise DetectionFailed(
            f"No supported project detected in {repo}.\n"
            f"Looked for: go.mod, {', '.join(looked_for)}\n"
            "Python and Go are supported. For anything else, pass --test-cmd "
            "and --env-cmd explicitly, or see issues labelled 'language'."
        )

    pip = installer()
    install: list[str] = []
    evidence: list[str] = []
    notes: list[str] = []

    if pyproject_text and _toml_parser() is None:
        # Silently installing bare "." here is the worst outcome: every task
        # fails at validation with a message that blames the repository.
        notes.append(NO_TOML_NOTE)

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
    if plan.language == "javascript":
        # Install once in the source clone; worktrees borrow node_modules by
        # symlink in run/validate, which is far cheaper than installing per
        # task. See link_node_modules() for the shadowing hazard that creates.
        for command in plan.install:
            subprocess.run(command, shell=True, cwd=str(repo),
                           capture_output=True, text=True)
        return None

    if plan.language == "go":
        # Go has no per-project interpreter to build; the module cache is global
        # and `go mod download` is idempotent.
        subprocess.run("go mod download", shell=True, cwd=str(repo),
                       capture_output=True, text=True)
        return None

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
    from .repos import repo_arg
    ap.add_argument("repo", type=repo_arg)
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
