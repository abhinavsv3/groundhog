#!/usr/bin/env python3
"""Does model ranking depend on which repository you measure it on?

Groundhog's premise is that "best model overall" is a poor guide to "best model
on your repo". That is a testable claim, and SWE-bench publishes the data needed
to test it: every submission to the Verified leaderboard ships a
`resolved_by_repo.json` giving resolved/total per repository.

This reads those files and asks three questions:

  1. How well do two repos agree on how to rank models?  (Spearman)
  2. How often does the better-overall model lose on a specific repo?
  3. How far can a single model move between repos?

Usage:
    python analysis/divergence.py /path/to/SWE-bench/experiments
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

MIN_INSTANCES = 20   # per repo, per model -- below this a flip is mostly noise
MIN_REPOS = 6        # a model must cover this many qualifying repos
MIN_GAP = 0.05       # overall pass-rate gap before we call one model "better"


def load(root: Path) -> dict[str, dict[str, tuple[int, int]]]:
    """model -> repo -> (resolved, total)"""
    models: dict[str, dict[str, tuple[int, int]]] = {}
    for path in sorted(root.glob("evaluation/verified/*/results/resolved_by_repo.json")):
        name = path.parents[1].name
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        rows = {
            repo: (v["resolved"], v["total"])
            for repo, v in raw.items()
            if isinstance(v, dict) and v.get("total")
        }
        if rows:
            models[name] = rows
    return models


def build_matrix(models: dict) -> tuple[list[str], list[str], dict]:
    """Keep repos with enough instances and models covering enough of them."""
    repo_totals: dict[str, int] = {}
    for rows in models.values():
        for repo, (_, total) in rows.items():
            repo_totals[repo] = max(repo_totals.get(repo, 0), total)
    repos = sorted(r for r, t in repo_totals.items() if t >= MIN_INSTANCES)

    rates: dict[tuple[str, str], float] = {}
    keep: list[str] = []
    for model, rows in models.items():
        covered = [r for r in repos if rows.get(r, (0, 0))[1] >= MIN_INSTANCES]
        if len(covered) < MIN_REPOS:
            continue
        keep.append(model)
        for repo in covered:
            resolved, total = rows[repo]
            rates[(model, repo)] = resolved / total
    return sorted(keep), repos, rates


def overall(model: str, repos: list[str], rates: dict) -> float:
    vals = [rates[(model, r)] for r in repos if (model, r) in rates]
    return sum(vals) / len(vals) if vals else 0.0


def spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation, average ranks for ties. No scipy."""
    def rank(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            shared = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = shared
            i = j + 1
        return out

    ra, rb = rank(a), rank(b)
    n = len(a)
    mean_a, mean_b = sum(ra) / n, sum(rb) / n
    num = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    den = (sum((x - mean_a) ** 2 for x in ra) * sum((y - mean_b) ** 2 for y in rb)) ** 0.5
    return num / den if den else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experiments", type=Path, help="clone of SWE-bench/experiments")
    ap.add_argument("--json", type=Path, help="also write the findings here")
    cfg = ap.parse_args()

    models_raw = load(cfg.experiments)
    if not models_raw:
        print("no resolved_by_repo.json files found", file=sys.stderr)
        return 1
    models, repos, rates = build_matrix(models_raw)

    print(f"\n{len(models_raw)} submissions on disk")
    print(f"{len(models)} models x {len(repos)} repos qualify "
          f"(repo >= {MIN_INSTANCES} instances, model covers >= {MIN_REPOS} repos)\n")

    # --- 1. do repos agree on how to rank models? -------------------------
    print("RANK AGREEMENT BETWEEN REPOS (Spearman)")
    pairs = []
    for x, y in combinations(repos, 2):
        both = [m for m in models if (m, x) in rates and (m, y) in rates]
        if len(both) < 10:
            continue
        rho = spearman([rates[(m, x)] for m in both], [rates[(m, y)] for m in both])
        pairs.append((rho, x, y, len(both)))
    pairs.sort()
    for rho, x, y, n in pairs[:5]:
        print(f"  {rho:>6.2f}   {x:<28} vs {y:<28} n={n}")
    print("  ...")
    for rho, x, y, n in pairs[-3:]:
        print(f"  {rho:>6.2f}   {x:<28} vs {y:<28} n={n}")
    mean_rho = sum(p[0] for p in pairs) / len(pairs)
    print(f"\n  mean rho across {len(pairs)} repo pairs: {mean_rho:.2f}")

    # --- 2. how often does the better model lose on a repo? ---------------
    ranked = sorted(models, key=lambda m: -overall(m, repos, rates))
    flips = total_comparisons = 0
    worst: list[tuple[float, str, str, str]] = []
    for better, worse in combinations(ranked, 2):
        gap = overall(better, repos, rates) - overall(worse, repos, rates)
        if gap < MIN_GAP:
            continue
        for repo in repos:
            if (better, repo) not in rates or (worse, repo) not in rates:
                continue
            total_comparisons += 1
            delta = rates[(worse, repo)] - rates[(better, repo)]
            if delta > 0:
                flips += 1
                worst.append((delta, repo, worse, better))

    print(f"\nRANKING FLIPS ON INDIVIDUAL REPOS")
    print(f"  compared only model pairs separated by >= {MIN_GAP:.0%} overall")
    print(f"  {flips:,} flips in {total_comparisons:,} comparisons "
          f"= {flips / total_comparisons:.1%} of the time, the worse model wins")
    worst.sort(reverse=True)
    print("\n  biggest upsets:")
    for delta, repo, winner, loser in worst[:5]:
        print(f"    {repo:<26} {winner[:34]:<36} beats {loser[:34]} by {delta:+.0%}")

    # --- 3. how much does one model move between repos? -------------------
    print(f"\nPER-MODEL SPREAD ACROSS REPOS")
    spreads = []
    for m in models:
        vals = [(rates[(m, r)], r) for r in repos if (m, r) in rates]
        if len(vals) < MIN_REPOS:
            continue
        lo, hi = min(vals), max(vals)
        spreads.append((hi[0] - lo[0], m, lo, hi))
    spreads.sort(reverse=True)
    for spread, m, lo, hi in spreads[:5]:
        print(f"  {spread:>5.0%}  {m[:40]:<42} {lo[0]:.0%} on {lo[1]:<22} -> {hi[0]:.0%} on {hi[1]}")
    median_spread = sorted(s[0] for s in spreads)[len(spreads) // 2]
    print(f"\n  median spread: {median_spread:.0%}")

    findings = {
        "submissions": len(models_raw),
        "models": len(models),
        "repos": repos,
        "mean_spearman": round(mean_rho, 3),
        "repo_pairs": len(pairs),
        "flips": flips,
        "comparisons": total_comparisons,
        "flip_rate": round(flips / total_comparisons, 4) if total_comparisons else None,
        "median_spread": round(median_spread, 4),
    }
    if cfg.json:
        cfg.json.parent.mkdir(parents=True, exist_ok=True)
        cfg.json.write_text(json.dumps(findings, indent=2))
        print(f"\nwrote {cfg.json}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
