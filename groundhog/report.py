#!/usr/bin/env python3
"""Turn a results file into a terminal table and a standalone leaderboard page."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from .stats import tasks_needed, wilson

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"
YELLOW = "\033[33m"


# Above this share of attempts producing no tool call at all, a run is not a
# low score -- it is a failed measurement, and printing a percentage for it
# states the harness's inability to read the model as the model's inability to
# work. See study/mistral-artifact.md for the case that prompted this.
SILENT_LIMIT = 0.5


def summarise(records: list[dict]) -> list[dict]:
    by_model: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_model[r["model"]].append(r)

    rows = []
    for model, attempts in by_model.items():
        solved = sum(1 for a in attempts if a["solved"])
        # What a harness that checks only the target tests would have reported.
        f2p_only = sum(1 for a in attempts if a.get("fail_to_pass_passed") or a["solved"])
        collateral = [a for a in attempts if a.get("fail_to_pass_passed") and not a["solved"]
                      and not a.get("tampered_with_tests")]
        tampered = sum(1 for a in attempts if a.get("tampered_with_tests"))
        silent = sum(1 for a in attempts if not (a.get("tools") or {}).get("calls", 0))
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
                "f2p_only_solved": f2p_only,
                "collateral_attempts": len(collateral),
                "collateral_tests_broken": sum(a.get("broke_pass_to_pass", 0) for a in collateral),
                "tampered": tampered,
                "silent": silent,
                "unmeasured": bool(attempts) and silent / len(attempts) > SILENT_LIMIT,
            }
        )
    return sorted(rows, key=lambda r: (r["unmeasured"], -r["pass_rate"], r["cost"] or 0))


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


def label(model: str) -> str:
    """A column heading that tells two models apart.

    Taking the last colon-separated part renders every ":latest" model as
    "latest", which is not a name.
    """
    name = model.split(":", 1)[-1] if model.count(":") > 1 else model
    return name.removesuffix(":latest")


def fit(name: str, width: int) -> str:
    """Trim a model name from the left, keeping the end.

    qwen2.5-coder:7b and qwen2.5-coder:14b differ only in their last few
    characters, so trimming the tail gives two identical column headings.
    """
    return name if len(name) <= width else "\u2026" + name[-(width - 1):]


def print_table(rows: list[dict], records: list[dict]) -> None:
    if not rows:
        print("no results")
        return

    width = max(len(r["model"]) for r in rows) + 2
    print()
    print(f"{BOLD}{'MODEL':<{width}}{'SOLVED':>8}{'RATE':>7}{'95% CI':>15}{'COST':>10}{'PER SOLVE':>12}{'MEDIAN':>9}{RESET}")
    print(DIM + "-" * (width + 61) + RESET)
    for i, r in enumerate(rows):
        if r["unmeasured"]:
            # Refuse the number. A run in which the model mostly never called a
            # tool measures the harness, and a percentage here would be read as
            # the model's capability.
            print(
                f"{DIM}{r['model']:<{width}}"
                f"{str(r['solved']) + '/' + str(r['total']):>8}"
                f"{'  not measured':>22}"
                f"{'—':>10}{'—':>12}"
                f"{r['median_seconds']:>8.0f}s{RESET}"
            )
            continue
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

    for r in rows:
        if r["unmeasured"]:
            print(
                f"\n{YELLOW}  {r['model']}: {r['silent']} of {r['total']} attempts parsed no "
                f"tool call at all.{RESET}\n"
                f"{DIM}  That is a failed measurement, not a 0% score -- the model may be "
                f"emitting perfectly\n  good work in a format this harness cannot read. "
                f"Check with:\n"
                f"      python3 scripts/probe_model_format.py {r['model']}\n"
                f"  Exclude it, or widen the parser, before reporting anything about "
                f"this model.{RESET}"
            )

    # Two overlapping intervals are not a ranking. Say so rather than letting the
    # order of the rows imply a result the data cannot support.
    ranked = [r for r in rows if not r["unmeasured"]]
    if len(ranked) >= 2 and ranked[0]["ci_low"] < ranked[1]["ci_high"]:
        rows_ = ranked
        need = tasks_needed(rows_[1]["pass_rate"], max(0.01, rows_[0]["pass_rate"] - rows_[1]["pass_rate"]))
        print(
            f"\n{DIM}  The top two intervals overlap — this ordering is not a result. "
            f"Separating them\n  would need roughly {need} tasks (you have {rows[0]['total']}), "
            f"or use `groundhog compare`\n  for a paired test, which needs far fewer.{RESET}"
        )

    tasks = sorted({r["task_id"]: r["subject"] for r in records}.items())
    models = [r["model"] for r in rows]
    unmeasured = {r["model"] for r in rows if r["unmeasured"]}
    lookup = {(r["model"], r["task_id"]): r["solved"] for r in records}

    print(f"\n{BOLD}PER TASK{RESET}")
    head = " " * 42 + "".join(f"{fit(label(m), 12):>13}" for m in models)
    print(DIM + head + RESET)
    for task_id, subject in tasks:
        line = f"{subject[:40]:<42}"
        for m in models:
            ok = lookup.get((m, task_id))
            if m in unmeasured or ok is None:
                # Not a failure: either the model was never run on this task, or
                # nothing it produced could be read. Marking these "fail" would
                # make both look like losses.
                mark = f"{DIM}-{RESET}"
            else:
                mark = f"{GREEN}pass{RESET}" if ok else f"{RED}fail{RESET}"
            line += " " * 9 + mark
        print(line)
    print()


def overstatement(rows: list[dict]) -> None:
    """What a FAIL_TO_PASS-only harness would have reported, next to the truth.

    Most harnesses check that the target tests pass and stop there. Groundhog
    also requires every previously-passing test to still pass and the test
    files to be untouched. The gap between the two numbers is agents that did
    the task and broke something else doing it -- the study's central finding,
    and the one number this tool exists to put in front of people.
    """
    shown = [r for r in rows if not r["unmeasured"]]
    if not any(r["f2p_only_solved"] > r["solved"] for r in shown):
        return  # no gap anywhere; an empty table would imply one was looked for and found

    width = max(len(r["model"]) for r in shown) + 2
    print(f"\n{BOLD}FAIL_TO_PASS-ONLY vs TRUE{RESET}  "
          f"{DIM}(what most harnesses report, next to what actually held){RESET}")
    header = (f"{'MODEL':<{width}}{'F2P-ONLY':>10}{'TRUE':>8}{'OVERSTATED':>12}"
              f"{'COLLATERAL':>12}   {'TESTS EDITED':<12}")
    print(BOLD + header + RESET)
    print(DIM + "-" * len(header) + RESET)
    for r in shown:
        n = r["total"]
        gap = (r["f2p_only_solved"] - r["solved"]) / n if n else 0.0
        colour = YELLOW if gap > 0 else ""
        collateral = (f"{r['collateral_attempts']} broke {r['collateral_tests_broken']}"
                      if r["collateral_attempts"] else "0")
        tampered = str(r["tampered"]) if r["tampered"] else "0"
        print(f"{colour}{r['model']:<{width}}"
              f"{str(r['f2p_only_solved']) + '/' + str(n):>10}"
              f"{str(r['solved']) + '/' + str(n):>8}"
              f"{f'+{gap * 100:.0f}pts' if gap else '0':>12}"
              f"{collateral:>12}   {tampered:<12}{RESET}")
    print(f"\n  {DIM}F2P-ONLY: attempts where the target tests passed. TRUE: those where "
          f"nothing else\n  broke and no test file was edited. COLLATERAL: attempts that "
          f"passed the target\n  and broke previously-passing tests, with how many. "
          f"Patches are in results/patches/.{RESET}")


def write_site(rows: list[dict], records: list[dict], repo: str, out: Path, template: Path) -> None:
    payload = {
        "repo": repo,
        # rows already carry "unmeasured" and "silent"; the page needs both so it
        # can refuse to draw a bar for a model the harness could not read.
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
    overstatement(rows)

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
