#!/usr/bin/env python3
"""Find harness artifacts hiding in published benchmark results.

A coding-agent benchmark can fail in a way that produces a plausible number
instead of an error: a broken environment makes every task in one repository
fail, the submission still reports a total, and nobody notices. We hit four such
bugs building Groundhog -- editable installs shadowing the worktree, models
claiming completion having edited nothing, PEP 735 groups installing silently,
and a stale environment cache. Every one produced believable results.

This asks whether the same thing is visible in the public record.

Method. Model each submission's per-repo success as a two-factor logit:

    logit(p[s,r]) = ability[s] + easiness[r]

fitted by alternating maximum likelihood over all submissions and repositories.
A submission that is broken on one repository -- rather than merely weak there --
shows up as a large negative Pearson residual on that cell alone, while its other
cells fit. Residuals are standardised by binomial variance, so a 0/20 cell counts
as far stronger evidence than 0/2.

This cannot prove a harness bug; only the submitters' logs can. It says where to
look, and how much of reported variance is not plausibly capability.

Usage:
    python analysis/artifacts.py /path/to/SWE-bench/experiments
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

MIN_INSTANCES = 10      # per cell; below this binomial noise swamps everything
MIN_REPOS = 5           # a submission must span this many repos to be modelled
FLAG_Z = 4.0            # standardised residual worth investigating
EPS = 1e-6


def load(root: Path) -> tuple[dict[str, dict[str, tuple[int, int]]], list[str]]:
    """Load per-repo results, discarding submissions whose denominators are wrong.

    Not every published file is trustworthy. Thirteen historical Verified files
    carry the full test-split totals (2,294) rather than the Verified subset
    (500), which deflates their rates by roughly 3.7x -- see SWE-bench/experiments
    issue #484. Fitting on them would drag the model and manufacture outliers.

    Rather than hard-coding those thirteen, we take the modal denominator per
    repository across all submissions as ground truth and drop any submission
    that disagrees. That generalises to whatever the next bookkeeping bug is.
    """
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

    # modal denominator per (split, repo)
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
        kept = {r: v for r, v in rows.items() if v[1] >= MIN_INSTANCES}
        if len(kept) >= MIN_REPOS:
            out[name] = kept
    return out, rejected


def logit(p: float) -> float:
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def fit(data: dict[str, dict[str, tuple[int, int]]], rounds: int = 200):
    """Alternating maximum likelihood for logit(p) = ability + easiness."""
    submissions = sorted(data)
    repos = sorted({r for rows in data.values() for r in rows})

    ability = {s: logit(sum(v[0] for v in data[s].values()) / max(1, sum(v[1] for v in data[s].values()))) for s in submissions}
    easiness = {r: 0.0 for r in repos}

    def newton(cells, offsets, start):
        """One Newton step on a single parameter, given its cells."""
        x = start
        for _ in range(25):
            grad = hess = 0.0
            for (k, n), off in zip(cells, offsets):
                p = sigmoid(x + off)
                grad += k - n * p
                hess -= n * p * (1 - p)
            if abs(hess) < EPS:
                break
            step = grad / hess
            x -= step
            x = max(min(x, 12.0), -12.0)
            if abs(step) < 1e-8:
                break
        return x

    for _ in range(rounds):
        moved = 0.0
        for r in repos:
            cells = [data[s][r] for s in submissions if r in data[s]]
            offs = [ability[s] for s in submissions if r in data[s]]
            new = newton(cells, offs, easiness[r])
            moved = max(moved, abs(new - easiness[r]))
            easiness[r] = new
        for s in submissions:
            cells = [data[s][r] for r in data[s]]
            offs = [easiness[r] for r in data[s]]
            new = newton(cells, offs, ability[s])
            moved = max(moved, abs(new - ability[s]))
            ability[s] = new
        if moved < 1e-7:
            break

    # centre for interpretability
    mean_e = sum(easiness.values()) / len(easiness)
    for r in easiness:
        easiness[r] -= mean_e
    for s in ability:
        ability[s] += mean_e
    return ability, easiness


def residuals(data, ability, easiness) -> list[dict]:
    out = []
    for s, rows in data.items():
        for r, (k, n) in rows.items():
            p = sigmoid(ability[s] + easiness[r])
            var = n * p * (1 - p)
            if var < EPS:
                continue
            out.append(
                {
                    "submission": s,
                    "repo": r,
                    "observed": k / n,
                    "expected": p,
                    "n": n,
                    "z": (k - n * p) / math.sqrt(var),
                }
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experiments", type=Path)
    ap.add_argument("--flag-z", type=float, default=FLAG_Z)
    ap.add_argument("--json", type=Path)
    cfg = ap.parse_args()

    data, rejected = load(cfg.experiments)
    if not data:
        print("no usable results found", file=sys.stderr)
        return 1

    if rejected:
        print(f"\nexcluded {len(rejected)} submissions with denominators that "
              f"disagree with the modal split size:")
        for name in rejected[:6]:
            print(f"  {name}")
        if len(rejected) > 6:
            print(f"  ... and {len(rejected) - 6} more")
        print("  (see SWE-bench/experiments#484 -- fitting on these manufactures outliers)")

    cells = sum(len(v) for v in data.values())
    repos = sorted({r for rows in data.values() for r in rows})
    print(f"\n{len(data)} submissions x {len(repos)} repos = {cells} cells "
          f"(cells with >= {MIN_INSTANCES} instances)\n")

    ability, easiness = fit(data)
    res = residuals(data, ability, easiness)

    print("REPOSITORY DIFFICULTY (fitted, higher = easier)")
    for r in sorted(easiness, key=lambda r: -easiness[r]):
        print(f"  {easiness[r]:>+6.2f}  {r}")

    low = sorted([x for x in res if x["z"] <= -cfg.flag_z], key=lambda x: x["z"])
    high = sorted([x for x in res if x["z"] >= cfg.flag_z], key=lambda x: -x["z"])
    affected = {x["submission"] for x in low}

    print(f"\nCELLS INCONSISTENT WITH THE MODEL  (|z| >= {cfg.flag_z:.0f})")
    print(f"  {len(low)} far below expectation, {len(high)} far above, out of {len(res)} cells")
    print(f"  {len(affected)}/{len(data)} submissions ({len(affected)/len(data):.0%}) have at least one")

    # The asymmetry is the real evidence. Binomial noise is symmetric, so both
    # tails should be equally populated. A broken environment can only make a
    # submission fail, never succeed -- so infrastructure failure shows up as an
    # excess in the negative tail only. Comparing the two tails also controls
    # for model misspecification, since both are computed from the same fit.
    print("\nTAIL ASYMMETRY  (the actual evidence)")
    print(f"  {'z threshold':<14}{'BELOW':>8}{'ABOVE':>8}{'BY CHANCE':>12}{'ENRICHMENT':>13}")
    for threshold in (2.0, 2.5, 3.0, 3.5, 4.0):
        below = sum(1 for x in res if x["z"] <= -threshold)
        above = sum(1 for x in res if x["z"] >= threshold)
        # normal one-tail probability, via the complementary error function
        tail = 0.5 * math.erfc(threshold / math.sqrt(2))
        expected = tail * len(res)
        ratio = below / expected if expected > 0 else float("inf")
        print(f"  |z| >= {threshold:<8.1f}{below:>8}{above:>8}{expected:>12.1f}{ratio:>12.1f}x")
    print("\n  Symmetric tails would mean noise. An excess below only means")
    print("  something is making submissions fail on particular repositories.")

    print(f"\nWORST UNDERPERFORMING CELLS — where a broken environment would show")
    print(f"  {'z':>7}  {'OBSERVED':>9}{'EXPECTED':>10}{'N':>5}  REPO / SUBMISSION")
    for x in low[:12]:
        print(f"  {x['z']:>7.1f}  {x['observed']:>8.0%}{x['expected']:>10.0%}{x['n']:>5}  "
              f"{x['repo'].split('/')[-1][:18]:<20}{x['submission'].split('/')[-1][:40]}")

    zeros = [x for x in low if x["observed"] == 0.0 and x["expected"] > 0.3]
    print(f"\n  of these, {len(zeros)} scored EXACTLY zero where the model expects >30% —")
    print("  a total failure on one repository while performing normally elsewhere,")
    print("  which is what a broken environment looks like and what weak capability does not.")

    findings = {
        "submissions": len(data),
        "excluded_submissions": rejected,
        "repos": len(repos),
        "cells": len(res),
        "flag_z": cfg.flag_z,
        "cells_below": len(low),
        "cells_above": len(high),
        "submissions_affected": len(affected),
        "share_affected": round(len(affected) / len(data), 4),
        "hard_zeros": len(zeros),
        "repo_easiness": {r: round(v, 3) for r, v in easiness.items()},
        "worst": low[:25],
    }
    if cfg.json:
        cfg.json.parent.mkdir(parents=True, exist_ok=True)
        cfg.json.write_text(json.dumps(findings, indent=2))
        print(f"\nwrote {cfg.json}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
