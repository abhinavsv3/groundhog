#!/usr/bin/env python3
"""Turn a results file into a terminal table and a standalone leaderboard page."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from .stats import tasks_needed, wilson

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
        lo, hi = wilson(solved, len(attempts))
        rows.append(
            {
                "model": model,
                "solved": solved,
                "total": len(attempts),
                "pass_rate": solved / len(attempts) if attempts else 0.0,
                "ci_low": lo,
                "ci_high": hi,
                "cost": sum(costs) if costs else None,
                "cost_per_solve": (sum(costs) / solved) if costs and solved else None,
                "median_seconds": times[len(times) // 2] if times else 0.0,
                "errors": sum(1 for a in attempts if a.get("error")),
            }
        )
    return sorted(rows, key=lambda r: (-r["pass_rate"], r["cost"] or 0))


# Approximate, published training cutoffs. Approximate is the point: these
# are announced dates, not observed ones, and a model's data can trail its
# stated cutoff. Override with --cutoff.
CUTOFFS = {
    "claude-opus-5": "2026-05-01",
    "claude-sonnet-5": "2026-05-01",
    "claude-haiku-4-5": "2025-10-01",
    "gpt-5.2": "2025-12-01",
    "qwen2.5-coder": "2024-09-01",
    "qwen3": "2025-04-01",
    "llama3.1": "2023-12-01",
    "mistral": "2023-09-01",
}


def cutoff_for(model: str, overrides: dict[str, str]) -> str | None:
    name = short(model)
    for key, date in {**CUTOFFS, **overrides}.items():
        if key in name:
            return date
    return None


def contamination(records: list[dict], tasks: dict[str, dict],
                  overrides: dict[str, str]) -> None:
    """Solve rate on commits predating a model's cutoff, versus after it.

    If public repo history is memorised, the older cohort should score higher.
    A gap is evidence of contamination; it is not proof, because post-cutoff
    commits are also *newer* commits and may differ in size, area and review
    standards. This measures era, and era is only partly contamination.
    """
    rows: dict[tuple[str, str], list[bool]] = defaultdict(list)
    missing: set[str] = set()

    for r in records:
        task = tasks.get(r["task_id"])
        if not task or not task.get("date"):
            continue
        cutoff = cutoff_for(r["model"], overrides)
        if not cutoff:
            missing.add(short(r["model"]))
            continue
        era = "pre-cutoff" if task["date"][:10] < cutoff else "post-cutoff"
        rows[(r["model"], era)].append(bool(r["solved"]))

    if not rows:
        if missing:
            print(f"\n{DIM}No cutoff known for {', '.join(sorted(missing))} — "
                  f"pass --cutoff name=YYYY-MM-DD for the contamination split.{RESET}")
        return

    print(f"\n{BOLD}BY COMMIT ERA{RESET}  {DIM}(pre/post the model's training cutoff){RESET}")
    print(f"{BOLD}{'MODEL':<22}{'ERA':<13}{'SOLVED':>8}{'RATE':>7}{'95% CI':>15}{RESET}")
    for model in sorted({m for m, _ in rows}):
        cutoff = cutoff_for(model, overrides)
        for era in ("pre-cutoff", "post-cutoff"):
            outcomes = rows.get((model, era), [])
            if not outcomes:
                continue
            solved, total = sum(outcomes), len(outcomes)
            lo, hi = wilson(solved, total)
            band = f"{lo:.0%} – {hi:.0%}"
            print(f"{short(model)[:20]:<22}{era:<13}"
                  f"{f'{solved}/{total}':>8}{solved / total:>7.0%}{band:>15}")
        print(f"{DIM}{'':<22}cutoff {cutoff}{RESET}")

    print(f"\n  {DIM}Post-cutoff commits are also newer commits. A gap here is "
          f"evidence of\n  memorisation, not proof of it — cutoff dates are "
          f"approximate and era\n  confounds with code age, size and review "
          f"standards.{RESET}")
    if missing:
        print(f"  {DIM}No cutoff known for: {', '.join(sorted(missing))}{RESET}")


def short(model: str) -> str:
    """Drop the provider prefix; `anthropic:claude-opus-5` -> `claude-opus-5`."""
    return model.split(":", 1)[-1] if ":" in model else model


def money(value: float | None) -> str:
    return "-" if value is None else f"${value:,.2f}"


def print_table(rows: list[dict], records: list[dict]) -> None:
    if not rows:
        print("no results")
        return

    width = max(len(r["model"]) for r in rows) + 2
    print()
    print(f"{BOLD}{'MODEL':<{width}}{'SOLVED':>8}{'RATE':>7}{'95% CI':>15}{'COST':>10}{'PER SOLVE':>12}{'MEDIAN':>9}{RESET}")
    print(DIM + "-" * (width + 61) + RESET)
    for i, r in enumerate(rows):
        lead = BOLD if i == 0 else ""
        band = f"{r['ci_low'] * 100:.0f}% – {r['ci_high'] * 100:.0f}%"
        print(
            f"{lead}{r['model']:<{width}}"
            f"{str(r['solved']) + '/' + str(r['total']):>8}"
            f"{r['pass_rate'] * 100:>6.0f}%"
            f"{band:>15}"
            f"{money(r['cost']):>10}"
            f"{money(r['cost_per_solve']):>12}"
            f"{r['median_seconds']:>8.0f}s{RESET}"
        )

    # Two overlapping intervals are not a ranking. Say so rather than letting the
    # order of the rows imply a result the data cannot support.
    if len(rows) >= 2 and rows[0]["ci_low"] < rows[1]["ci_high"]:
        need = tasks_needed(rows[1]["pass_rate"], max(0.01, rows[0]["pass_rate"] - rows[1]["pass_rate"]))
        print(
            f"\n{DIM}  The top two intervals overlap — this ordering is not a result. "
            f"Separating them\n  would need roughly {need} tasks (you have {rows[0]['total']}), "
            f"or use `groundhog compare`\n  for a paired test, which needs far fewer.{RESET}"
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


def mechanics(records: list[dict]) -> None:
    """How each model worked, independent of whether it succeeded.

    A pass rate over 100 tasks is 100 observations. The same runs produce
    thousands of tool calls, so these columns can separate models that all
    scored zero -- and can detect effects far too small to move a solve rate.
    """
    per_model: dict[str, dict[str, float]] = defaultdict(
        lambda: {"attempts": 0, "calls": 0, "errors": 0, "unknown": 0,
                 "text": 0, "edited": 0, "first_edit": 0.0, "first_edit_n": 0}
    )
    for r in records:
        stats = r.get("tools") or {}
        if not stats:
            continue
        row = per_model[r["model"]]
        row["attempts"] += 1
        row["calls"] += stats.get("calls", 0)
        row["errors"] += stats.get("errors", 0)
        row["unknown"] += stats.get("unknown_tool", 0)
        row["text"] += stats.get("recovered_from_text", 0)
        if stats.get("first_edit_turn") is not None:
            row["edited"] += 1
            row["first_edit"] += stats["first_edit_turn"]
            row["first_edit_n"] += 1

    rows = {m: v for m, v in per_model.items() if v["calls"]}
    if not rows:
        return

    width = max(len(short(m)) for m in rows) + 2
    print(f"\n{BOLD}HOW THEY WORKED{RESET}")
    header = (f"{'MODEL':<{width}}{'CALLS':>7}{'/TASK':>7}{'ERROR%':>8}"
              f"{'UNKNOWN':>9}{'AS TEXT':>9}{'EDITED':>8}{'1ST EDIT':>10}")
    print(BOLD + header + RESET)
    print(DIM + "-" * len(header) + RESET)
    for model, v in sorted(rows.items(), key=lambda kv: -kv[1]["calls"]):
        calls = v["calls"]
        first = v["first_edit"] / v["first_edit_n"] if v["first_edit_n"] else None
        edited = f"{int(v['edited'])}/{int(v['attempts'])}"
        when = f"turn {first:.1f}" if first else "-"
        print(
            f"{short(model):<{width}}{int(calls):>7}{calls / v['attempts']:>7.1f}"
            f"{v['errors'] / calls:>7.0%} {v['unknown'] / calls:>8.0%}"
            f"{v['text'] / calls:>9.0%}{edited:>8}{when:>10}"
        )
    print(f"\n  {DIM}AS TEXT: tool calls that arrived as prose and had to be "
          f"recovered — a model\n  property, and a confound if it differs across "
          f"a comparison.{RESET}")


def by_trait(records: list[dict], tasks: dict[str, dict]) -> None:
    """Where an agent is reliable, and where it is not."""
    traits = [("shape", "SCOPE"), ("size", "SIZE"), ("concurrency", "CONCURRENCY")]
    buckets: dict[tuple[str, str], list[bool]] = defaultdict(list)

    for r in records:
        task = tasks.get(r["task_id"])
        if not task:
            continue
        for key, _ in traits:
            value = task.get(key)
            if value in (None, ""):
                continue
            label = {True: "yes", False: "no"}.get(value, value)
            buckets[(key, label)].append(bool(r["solved"]))

    # Only worth printing where a trait actually varies. A task set that is
    # entirely single-file small changes has no contrast to show, and an empty
    # section implies the breakdown found nothing rather than that there was
    # nothing to find.
    contrasts = [
        (key, heading) for key, heading in traits
        if len({k[1] for k in buckets if k[0] == key}) >= 2
    ]
    if not contrasts:
        return

    print(f"\n{BOLD}BY CHANGE SHAPE{RESET}")
    for key, heading in contrasts:
        rows = sorted((k[1], v) for k, v in buckets.items() if k[0] == key)
        print(f"  {DIM}{heading}{RESET}")
        for label, outcomes in rows:
            solved, total = sum(outcomes), len(outcomes)
            lo, hi = wilson(solved, total)
            bar = "#" * round(solved / total * 20) if total else ""
            print(f"    {label:<14}{solved:>3}/{total:<4}{solved / total:>6.0%}"
                  f"   {lo:.0%}–{hi:.0%}".ljust(46) + f"{DIM}{bar}{RESET}")
    print(f"\n  {DIM}Small buckets have wide intervals; read the range, not the "
          f"point.{RESET}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, default=Path("results/results.jsonl"))
    ap.add_argument("--repo", default="", help="repo name shown on the page")
    ap.add_argument("--site", type=Path, help="also write a standalone HTML page here")
    ap.add_argument("--template", type=Path, default=Path("site/template.html"))
    ap.add_argument("--tasks", type=Path, help="validated tasks, for the change-shape breakdown")
    ap.add_argument("--cutoff", action="append", default=[], metavar="NAME=YYYY-MM-DD",
                    help="training cutoff for a model, for the contamination split")
    cfg = ap.parse_args()

    if not cfg.results.exists():
        print(f"no results at {cfg.results}")
        return 1
    records = [json.loads(l) for l in cfg.results.read_text().splitlines() if l.strip()]
    rows = summarise(records)
    print_table(rows, records)

    if cfg.tasks and cfg.tasks.exists():
        tasks = {}
        for line in cfg.tasks.read_text().splitlines():
            if line.strip():
                task = json.loads(line)
                tasks[task["sha"][:12]] = task
        by_trait(records, tasks)
        overrides = {}
        for item in cfg.cutoff:
            if "=" in item:
                name, _, date = item.partition("=")
                overrides[name.strip()] = date.strip()
        contamination(records, tasks, overrides)
    mechanics(records)

    if cfg.site:
        write_site(rows, records, cfg.repo or "unknown repo", cfg.site, cfg.template)
        print(f"wrote {cfg.site}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
