#!/usr/bin/env python3
"""Mine benchmark task candidates from a repository's git history.

A candidate is a commit that changes both source and tests. We keep the tests
and throw away the source change; the model's job is to write source that makes
the tests pass again. Because the tasks come from commits, a repo's recent
history is a benchmark no model has been trained on.

This stage is pure git analysis -- fast, no test execution. Validating that a
candidate actually fails-then-passes happens in validate.py.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

TEST_PATTERNS = [
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)test_[^/]+\.py$"),
    re.compile(r"[^/]+_test\.(py|go|rs|ts|js)$"),
    re.compile(r"[^/]+\.(test|spec)\.(ts|tsx|js|jsx)$"),
    re.compile(r"(^|/)spec/"),
]

# Files that change constantly and teach a model nothing.
NOISE_PATTERNS = [
    re.compile(r"(^|/)(CHANGELOG|HISTORY|AUTHORS)"),
    re.compile(r"\.(lock|sum|md|rst|txt|cfg|toml|ini|yaml|yml|json)$"),
    re.compile(r"(^|/)(docs?|examples?|vendor|node_modules)/"),
]

# git writes revert subjects verbatim, so the target is recoverable.
REVERT = re.compile(r'^Revert\s+"(?P<subject>.+)"\s*$')

SOURCE_EXTENSIONS = {".py", ".go", ".rs", ".ts", ".tsx", ".js", ".jsx", ".java", ".rb", ".c", ".cc", ".cpp", ".h"}


def is_test_file(path: str) -> bool:
    return any(p.search(path) for p in TEST_PATTERNS)


def is_noise(path: str) -> bool:
    return any(p.search(path) for p in NOISE_PATTERNS)


def is_source_file(path: str) -> bool:
    return Path(path).suffix in SOURCE_EXTENSIONS and not is_test_file(path)


@dataclass
class Candidate:
    sha: str
    parent: str
    date: str
    subject: str
    test_files: list[str]
    source_files: list[str]
    source_lines_changed: int
    test_lines_added: int
    reverts: str = ""

    @property
    def task_id(self) -> str:
        return self.sha[:12]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def iter_commits(repo: Path, since: str, limit: int) -> list[tuple[str, str, str]]:
    """Return (sha, iso-date, subject) for non-merge commits, newest first."""
    out = git(
        repo,
        "log",
        "--no-merges",
        f"--since={since}",
        f"--max-count={limit}",
        "--pretty=format:%H%x00%cI%x00%s",
    )
    commits = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, date, subject = line.split("\x00", 2)
        commits.append((sha, date, subject))
    return commits


def numstat(repo: Path, sha: str) -> list[tuple[int, int, str]]:
    """Return (added, deleted, path) for each file touched by a commit."""
    out = git(repo, "show", "--numstat", "--format=", sha)
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        if added == "-" or deleted == "-":  # binary file
            continue
        rows.append((int(added), int(deleted), path))
    return rows


def evaluate(repo: Path, sha: str, date: str, subject: str, cfg: argparse.Namespace) -> tuple[Candidate | None, str]:
    """Return (candidate, reason-if-rejected)."""
    try:
        parents = git(repo, "rev-list", "--parents", "-n", "1", sha).split()
    except RuntimeError as exc:
        return None, f"git error: {exc}"
    if len(parents) < 2:
        return None, "root commit"
    parent = parents[1]

    rows = numstat(repo, sha)
    if not rows:
        return None, "no text changes"

    test_files, source_files = [], []
    source_lines = test_lines_added = 0
    for added, deleted, path in rows:
        if is_noise(path):
            continue
        if is_test_file(path):
            test_files.append(path)
            test_lines_added += added
        elif is_source_file(path):
            source_files.append(path)
            source_lines += added + deleted

    if not test_files:
        return None, "no test changes"
    if not source_files:
        return None, "test-only commit"
    if test_lines_added == 0:
        return None, "tests only deleted"
    if source_lines > cfg.max_source_lines:
        return None, f"source change too large ({source_lines} lines)"
    if source_lines < cfg.min_source_lines:
        return None, f"source change too small ({source_lines} lines)"
    if len(source_files) > cfg.max_source_files:
        return None, f"touches too many files ({len(source_files)})"

    revert_match = REVERT.match(subject.strip())

    return (
        Candidate(
            reverts=revert_match.group("subject") if revert_match else "",
            sha=sha,
            parent=parent,
            date=date,
            subject=subject,
            test_files=sorted(test_files),
            source_files=sorted(source_files),
            source_lines_changed=source_lines,
            test_lines_added=test_lines_added,
        ),
        "",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", type=Path, help="path to a git repository")
    ap.add_argument("--since", default="6 months ago", help="only consider commits after this date")
    ap.add_argument("--limit", type=int, default=500, help="how many commits to scan")
    ap.add_argument("--max-source-lines", type=int, default=200)
    ap.add_argument("--min-source-lines", type=int, default=3)
    ap.add_argument("--max-source-files", type=int, default=5)
    ap.add_argument("--out", type=Path, default=Path("tasks/candidates.jsonl"))
    ap.add_argument("--verbose", action="store_true", help="show why commits were rejected")
    cfg = ap.parse_args()

    if not (cfg.repo / ".git").exists():
        print(f"error: {cfg.repo} is not a git repository", file=sys.stderr)
        return 1

    commits = iter_commits(cfg.repo, cfg.since, cfg.limit)
    print(f"scanning {len(commits)} commits from {cfg.repo}", file=sys.stderr)

    candidates: list[Candidate] = []
    rejections: dict[str, int] = {}
    for sha, date, subject in commits:
        cand, reason = evaluate(cfg.repo, sha, date, subject, cfg)
        if cand is None:
            key = re.sub(r"\(.*\)", "(...)", reason)
            rejections[key] = rejections.get(key, 0) + 1
            if cfg.verbose:
                print(f"  skip {sha[:12]}  {reason}", file=sys.stderr)
            continue
        candidates.append(cand)

    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    with cfg.out.open("w") as fh:
        for cand in candidates:
            fh.write(json.dumps(asdict(cand)) + "\n")

    print(f"\n{len(candidates)} candidates -> {cfg.out}", file=sys.stderr)
    if rejections:
        print("rejected:", file=sys.stderr)
        for reason, count in sorted(rejections.items(), key=lambda kv: -kv[1]):
            print(f"  {count:4d}  {reason}", file=sys.stderr)
    for cand in candidates[:10]:
        print(f"  {cand.task_id}  {cand.source_lines_changed:4d}L  {cand.subject[:60]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
