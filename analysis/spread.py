#!/usr/bin/env python3
"""How much does a model's success rate move between repositories?

The SWE-bench study in divergence.py found that *ranking* transfers between
repos (Spearman 0.88) but *absolute capability* does not -- the median model
swings 35 points between its best and worst repo. That spread is the number a
public leaderboard cannot give you, and this measures it on your own runs.

Usage:
    python analysis/spread.py results/all.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def rates(records: list[dict]) -> tuple[dict, list[str], list[str]]:
    counts: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for r in records:
        cell = counts[(r["model"], r.get("repo", "?"))]
        cell[0] += bool(r["solved"])
        cell[1] += 1
    models = sorted({m for m, _ in counts})
    repos = sorted({p for _, p in counts})
    table = {k: (v[0], v[1], v[0] / v[1] if v[1] else 0.0) for k, v in counts.items()}
    return table, models, repos


def short(model: str) -> str:
    return model.split(":", 1)[-1] if model.startswith("openai:") or model.startswith("anthropic:") else model


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", type=Path, nargs="?", default=Path("results/all.jsonl"))
    cfg = ap.parse_args()

    if not cfg.results.exists():
        print(f"no results at {cfg.results}", file=sys.stderr)
        return 1
    records = load(cfg.results)
    table, models, repos = rates(records)

    width = max((len(short(m)) for m in models), default=10) + 2
    print(f"\n{BOLD}PASS RATE BY REPO{RESET}")
    header = f"{'MODEL':<{width}}" + "".join(f"{r[:12]:>14}" for r in repos) + f"{'OVERALL':>10}{'SPREAD':>9}"
    print(BOLD + header + RESET)
    print(DIM + "-" * len(header) + RESET)

    spreads = []
    for model in models:
        cells = [table.get((model, r)) for r in repos]
        line = f"{short(model):<{width}}"
        present = [c[2] for c in cells if c]
        for c in cells:
            line += f"{'-':>14}" if not c else f"{c[0]}/{c[1]} ({c[2]:.0%})".rjust(14)
        solved = sum(c[0] for c in cells if c)
        total = sum(c[1] for c in cells if c)
        spread = (max(present) - min(present)) if len(present) > 1 else 0.0
        spreads.append((spread, short(model)))
        line += f"{solved}/{total}".rjust(10) + f"{spread:>8.0%}"
        print(line)

    if not any(s for s, _ in spreads):
        print(f"\n{DIM}Every model scored the same everywhere -- usually means all zeros.{RESET}")

    solved_total = sum(1 for r in records if r["solved"])
    print(f"\n{solved_total}/{len(records)} attempts solved overall")
    if spreads:
        med = sorted(s for s, _ in spreads)[len(spreads) // 2]
        print(f"median spread across repos: {med:.0%}")
        print(f"{DIM}SWE-bench leaderboard models, same measure: 35%{RESET}")

    errs = [r for r in records if r.get("error")]
    if errs:
        print(f"\n{len(errs)} attempts errored -- these are harness failures, not model failures:")
        seen = set()
        for r in errs[:5]:
            key = r["error"][:60]
            if key not in seen:
                seen.add(key)
                print(f"  {short(r['model']):<24} {key}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
