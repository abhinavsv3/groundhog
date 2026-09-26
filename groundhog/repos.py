#!/usr/bin/env python3
"""Accept a URL wherever a repository path is accepted.

    groundhog mine https://github.com/pallets/click
    groundhog mine pallets/click
    groundhog mine git@github.com:pallets/click.git

The first person to try Groundhog on a real repo has usually not cloned it yet,
and "clone it first" is one more step between them and a number. So every
command that takes <repo> resolves URLs to a cached full clone under
$GROUNDHOG_CACHE/repos/<host>/<owner>/<name>, fetching on reuse.

Full history, not a shallow clone: mining reads commits, and a --depth clone
would silently produce zero candidates.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from .environment import cache_root

URL = re.compile(
    r"^(?:https?://)?(?P<host>github\.com|gitlab\.com|bitbucket\.org|codeberg\.org|[\w.-]+\.[a-z]{2,})"
    r"/(?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?/?(?:[#?].*)?$"
)
SSH = re.compile(r"^(?:ssh://)?git@(?P<host>[\w.-]+)[:/](?P<owner>[\w.-]+)/(?P<name>[\w.-]+?)(?:\.git)?$")
SHORT = re.compile(r"^(?P<owner>[\w.-]+)/(?P<name>[\w.-]+)$")


def parse(spec: str) -> tuple[str, str, str, str] | None:
    """(clone url, host, owner, name), or None if spec is a local path."""
    spec = spec.strip()
    if Path(spec).exists():
        return None
    m = SSH.match(spec)
    if m:
        return spec if spec.startswith(("git@", "ssh://")) else spec, m["host"], m["owner"], m["name"]
    m = URL.match(spec)
    if m and ("/" in spec) and not spec.startswith((".", "/", "~")):
        host, owner, name = m["host"], m["owner"], m["name"]
        return f"https://{host}/{owner}/{name}.git", host, owner, name
    m = SHORT.match(spec)
    if m and not spec.startswith((".", "~")):
        # pallets/click: GitHub is the only sensible default.
        return f"https://github.com/{m['owner']}/{m['name']}.git", "github.com", m["owner"], m["name"]
    return None


def is_remote(spec: str) -> bool:
    return parse(spec) is not None


def resolve(spec: str, quiet: bool = False) -> Path:
    """A local path for spec, cloning or fetching as needed."""
    parsed = parse(spec)
    if parsed is None:
        return Path(spec)
    url, host, owner, name = parsed
    target = cache_root() / "repos" / host / owner / name

    def say(msg: str) -> None:
        if not quiet:
            print(msg, file=sys.stderr)

    if (target / ".git").exists():
        say(f"using cached clone {target} (fetching)")
        # Offline is fine: the clone we have is still a repository with history.
        subprocess.run(["git", "-C", str(target), "fetch", "--quiet", "--all", "--tags"],
                       capture_output=True, text=True, timeout=120)
        return target

    say(f"cloning {url} -> {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "clone", "--quiet", url, str(target)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise argparse.ArgumentTypeError(
            f"could not clone {url}:\n{proc.stderr.strip()[-600:]}\n"
            f"(there is no local path called {spec!r} either)"
        )
    return target


def remote_name(repo: Path) -> str | None:
    """owner/name from the origin remote, for citations and SWE-bench ids."""
    proc = subprocess.run(["git", "-C", str(repo), "remote", "get-url", "origin"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    parsed = parse(proc.stdout.strip()) or parse(proc.stdout.strip().removesuffix(".git"))
    if parsed is None:
        return None
    _, _, owner, name = parsed
    return f"{owner}/{name}"


def repo_arg(spec: str) -> Path:
    """argparse type: a path or a URL, always yielding a local repository."""
    try:
        path = resolve(spec)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise argparse.ArgumentTypeError(f"could not fetch {spec}: {exc}") from None
    if not (path / ".git").exists():
        raise argparse.ArgumentTypeError(f"{spec} is not a git repository")
    return path
