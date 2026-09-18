#!/usr/bin/env python3
"""Turn a results file into a terminal table and a standalone leaderboard page."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def summarise(records: list[dict]) -> list[dict]:
    by_model: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_model[r["model"]].append(r)

    rows = []
    for model, attempts in by_model.items():
        solved = sum(1 for a in attempts if a["solved"])
        costs = [a["cost_usd"] for a in attempts if a.get("cost_usd") is not None]
        times = sorted(a["seconds"] for a in attempts)
        rows.append(
            {
                "model": model,
                "solved": solved,
                "total": len(attempts),
                "pass_rate": solved / len(attempts) if attempts else 0.0,
                "cost": sum(costs) if costs else None,
                "cost_per_solve": (sum(costs) / solved) if costs and solved else None,
                "median_seconds": times[len(times) // 2] if times else 0.0,
                "errors": sum(1 for a in attempts if a.get("error")),
            }
        )
    return sorted(rows, key=lambda r: (-r["pass_rate"], r["cost"] or 0))


def money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


def print_table(rows: list[dict], records: list[dict]) -> None:
    if not rows:
        print("no results")
        return

    width = max(len(r["model"]) for r in rows) + 2
    print()
    print(f"{BOLD}{'MODEL':<{width}}{'SOLVED':>8}{'RATE':>8}{'COST':>10}{'PER SOLVE':>12}{'MEDIAN':>9}{RESET}")
    print(DIM + "-" * (width + 47) + RESET)
    for i, r in enumerate(rows):
        lead = BOLD if i == 0 else ""
        print(
            f"{lead}{r['model']:<{width}}"
            f"{str(r['solved']) + '/' + str(r['total']):>8}"
            f"{r['pass_rate'] * 100:>7.0f}%"
            f"{money(r['cost']):>10}"
            f"{money(r['cost_per_solve']):>12}"
            f"{r['median_seconds']:>8.0f}s{RESET}"
        )

    tasks = sorted({r["task_id"]: r["subject"] for r in records}.items())
    models = [r["model"] for r in rows]
    lookup = {(r["model"], r["task_id"]): r["solved"] for r in records}

    print(f"\n{BOLD}PER TASK{RESET}")
    head = " " * 42 + "".join(f"{m.split(':')[-1][:11]:>13}" for m in models)
    print(DIM + head + RESET)
    for task_id, subject in tasks:
        line = f"{subject[:40]:<42}"
        for m in models:
            ok = lookup.get((m, task_id))
            mark = f"{GREEN}pass{RESET}" if ok else (f"{RED}fail{RESET}" if ok is not None else f"{DIM}-{RESET}")
            line += " " * 9 + mark
        print(line)
    print()


def write_site(rows: list[dict], records: list[dict], repo: str, out: Path, template: Path) -> None:
    payload = {
        "repo": repo,
        "models": rows,
        "tasks": [
            {"task_id": tid, "subject": sub}
            for tid, sub in sorted({r["task_id"]: r["subject"] for r in records}.items())
        ],
        "results": [
            {"model": r["model"], "task_id": r["task_id"], "solved": r["solved"]} for r in records
        ],
    }
    html = template.read_text().replace("__DATA__", json.dumps(payload))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results/results.jsonl"))
    ap.add_argument("--repo", default="", help="repo name shown on the page")
    ap.add_argument("--site", type=Path, help="also write a standalone HTML page here")
    ap.add_argument("--template", type=Path, default=Path("site/template.html"))
    cfg = ap.parse_args()

    if not cfg.results.exists():
        print(f"no results at {cfg.results}")
        return 1
    records = [json.loads(l) for l in cfg.results.read_text().splitlines() if l.strip()]
    rows = summarise(records)
    print_table(rows, records)

    if cfg.site:
        write_site(rows, records, cfg.repo or "unknown repo", cfg.site, cfg.template)
        print(f"wrote {cfg.site}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
