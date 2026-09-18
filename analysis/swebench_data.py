#!/usr/bin/env python3
"""Shared loader for SWE-bench published per-repo results.

Not every published file is trustworthy, so both analyses go through the same
integrity gate rather than each trusting the data on its own terms.

Thirteen historical Verified files carry the full test-split denominators
(2,294) instead of the Verified subset (500), deflating their rates by roughly
3.7x -- reported upstream as SWE-bench/experiments#484. Rather than hard-coding
that list, we take the modal denominator per repository and split as ground
truth and drop submissions that disagree; this recovers exactly those thirteen
without being told about them, and will catch the next variant.
"""

from __future__ import annotations

import json
from pathlib import Path


def load_results(root: Path, min_instances: int = 1) -> tuple[dict[str, dict[str, tuple[int, int]]], list[str]]:
    """Return (submission -> repo -> (resolved, total), rejected submissions)."""
    raw_rows: dict[str, dict[str, tuple[int, int]]] = {}
    for path in sorted(root.glob("evaluation/*/*/results/resolved_by_repo.json")):
        split = path.parents[2].name
        name = f"{split}/{path.parents[1].name}"
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        rows = {
            repo: (int(v["resolved"]), int(v["total"]))
            for repo, v in raw.items()
            if isinstance(v, dict) and int(v.get("total", 0)) > 0
        }
        if rows:
            raw_rows[name] = rows

    tallies: dict[tuple[str, str], dict[int, int]] = {}
    for name, rows in raw_rows.items():
        split = name.split("/", 1)[0]
        for repo, (_, total) in rows.items():
            tallies.setdefault((split, repo), {})
            tallies[(split, repo)][total] = tallies[(split, repo)].get(total, 0) + 1
    expected = {key: max(counts, key=counts.get) for key, counts in tallies.items()}

    out: dict[str, dict[str, tuple[int, int]]] = {}
    rejected: list[str] = []
    for name, rows in raw_rows.items():
        split = name.split("/", 1)[0]
        if any(total != expected.get((split, repo), total) for repo, (_, total) in rows.items()):
            rejected.append(name)
            continue
        kept = {r: v for r, v in rows.items() if v[1] >= min_instances}
        if kept:
            out[name] = kept
    return out, rejected
