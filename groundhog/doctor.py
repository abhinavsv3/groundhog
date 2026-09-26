#!/usr/bin/env python3
"""Check the machine before a run, so the first error is not four worktrees deep.

    $ groundhog doctor

Every first-run failure is environmental: no git, Python too old, uv missing
so installs crawl, Ollama not running, the agent binary not on PATH, a key
unset. Each surfaces as a different error at a different depth. This lists
them in one place. Only git and Python are fatal; the rest is information.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from .agents import PRESETS
from .environment import cache_root
from .models import HOSTS, base_url_for

OK, WARN, BAD, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def version_of(binary: str, *args: str) -> str | None:
    if shutil.which(binary) is None:
        return None
    try:
        out = subprocess.run([binary, *args], capture_output=True, text=True, timeout=10)
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except (subprocess.SubprocessError, OSError, IndexError):
        return "present"


def ollama_models(url: str) -> list[str] | None:
    """Model names from a running Ollama, or None if it is not answering."""
    probe = url.rsplit("/v1", 1)[0] + "/api/tags"
    try:
        with urllib.request.urlopen(probe, timeout=3) as resp:
            data = json.loads(resp.read())
        return sorted(m.get("name", "?") for m in data.get("models", []))
    except Exception:
        return None


def dir_size(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def checks(colour: bool = True) -> tuple[list[tuple[str, str, str]], bool]:
    """(label, status, detail) rows, and whether anything fatal is missing."""
    ok, warn, bad, reset = (OK, WARN, BAD, RESET) if colour else ("", "", "", "")
    rows: list[tuple[str, str, str]] = []
    fatal = False

    git = version_of("git", "--version")
    if git:
        rows.append(("git", f"{ok}ok{reset}", git.replace("git version ", "")))
    else:
        rows.append(("git", f"{bad}missing{reset}", "every command needs it"))
        fatal = True

    py = sys.version.split()[0]
    if sys.version_info >= (3, 10):
        rows.append(("python", f"{ok}ok{reset}", py))
    else:
        rows.append(("python", f"{bad}too old{reset}", f"{py}; 3.10+ required"))
        fatal = True
    try:
        import tomllib  # noqa: F401
        rows.append(("tomllib", f"{ok}ok{reset}", "pyproject.toml extras will be detected"))
    except ModuleNotFoundError:
        try:
            import tomli  # noqa: F401
            rows.append(("tomllib", f"{ok}ok{reset}", "via tomli"))
        except ModuleNotFoundError:
            rows.append(("tomllib", f"{warn}missing{reset}",
                         "Python 3.10 without tomli: pyproject extras will be missed (pip install tomli)"))

    uv = version_of("uv", "--version")
    rows.append(("uv", f"{ok}ok{reset}" if uv else f"{warn}absent{reset}",
                 uv.replace("uv ", "") + ", fast installs" if uv else "pip will be used; uv is much faster"))
    for tool, why in (("go", "Go repositories"), ("node", "JavaScript repositories"),
                      ("cargo", "Rust repositories")):
        v = version_of(tool, "--version" if tool != "go" else "version")
        rows.append((tool, f"{ok}ok{reset}" if v else f"{DIM if colour else ''}absent{reset}",
                     (v.replace("go version ", "")[:24] if v else f"needed only for {why}")))

    url = base_url_for(HOSTS["ollama"])
    models = ollama_models(url)
    if models is None:
        rows.append(("ollama", f"{warn}not running{reset}", f"{url}; `ollama serve` for local models"))
    else:
        shown = ", ".join(models[:5]) + (f", +{len(models) - 5} more" if len(models) > 5 else "")
        rows.append(("ollama", f"{ok}running{reset}", f"{len(models)} models: {shown}" if models
                     else "no models pulled; try `ollama pull qwen3:8b`"))

    keys = [("ANTHROPIC_API_KEY", "anthropic:")]
    keys += [(h.key_var, f"{h.name}:") for h in HOSTS.values() if h.key_required and h.key_var]
    for var, prefix in keys:
        present = bool(os.environ.get(var))
        rows.append((var, f"{ok}set{reset}" if present else f"{DIM if colour else ''}not set{reset}",
                     f"unlocks {prefix}"))

    for name, p in PRESETS.items():
        where = shutil.which(p.binary)
        rows.append((f"agent {name}", f"{ok}on PATH{reset}" if where else f"{DIM if colour else ''}absent{reset}",
                     where or p.install))

    cache = cache_root()
    if cache.exists():
        envs = [d for d in cache.iterdir() if d.is_dir() and d.name != "repos"]
        repos = list((cache / "repos").glob("*/*/*")) if (cache / "repos").exists() else []
        rows.append(("cache", f"{ok}ok{reset}",
                     f"{cache}: {len(envs)} environment(s), {len(repos)} cached repo(s), "
                     f"{human(dir_size(cache))}"))
    else:
        rows.append(("cache", f"{DIM if colour else ''}empty{reset}", f"{cache} will be created on first use"))

    return rows, fatal


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-color", action="store_true")
    cfg = ap.parse_args()
    colour = not cfg.no_color and sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    rows, fatal = checks(colour)
    width = max(len(label) for label, _, _ in rows) + 2
    for label, status, detail in rows:
        pad = 14 + (len(status) - len(status.encode().decode()) if False else 0)
        plain = status
        for code in (OK, WARN, BAD, DIM, RESET):
            plain = plain.replace(code, "")
        print(f"  {label:<{width}}{status}{' ' * max(1, 14 - len(plain))}{DIM if colour else ''}{detail}{RESET if colour else ''}")
    if fatal:
        print("\nsomething every command needs is missing; fix the red lines first", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
