#!/usr/bin/env python3
"""Compare two Groundhog runs on the same task set.

This is the regression check: run before a change, run after, compare. It is
also how you run an ablation -- change one thing about the agent, hold the tasks
fixed, and see whether the difference survives a significance test.

Exits non-zero when a run has regressed, so it can gate CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .stats import mcnemar, tasks_needed, wilson

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def load(path: Path, model: str | None = None) -> list[dict]:
    records = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    if model:
        records = [r for r in records if r["model"] == model]
    return records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("baseline", type=Path, help="results from before the change")
    ap.add_argument("current", type=Path, help="results from after the change")
    ap.add_argument("--model", help="restrict both sides to one model")
    ap.add_argument("--label-a", default="baseline")
    ap.add_argument("--label-b", default="current")
    ap.add_argument("--fail-under", type=float, default=None,
                    help="exit 1 if the current pass rate falls below this (0-1)")
    ap.add_argument("--fail-on-regression", action="store_true",
                    help="exit 1 if the current run is significantly worse")
    ap.add_argument("--json", type=Path)
    cfg = ap.parse_args()

    for path in (cfg.baseline, cfg.current):
        if not path.exists():
            print(f"no results at {path}", file=sys.stderr)
            return 2

    a, b = load(cfg.baseline, cfg.model), load(cfg.current, cfg.model)
    if not a or not b:
        print("one side has no matching records", file=sys.stderr)
        return 2

    stamps_a = {r.get("manifest", "") for r in a}
    stamps_b = {r.get("manifest", "") for r in b}
    if stamps_a and stamps_b and stamps_a != stamps_b and all(stamps_a | stamps_b):
        print(f"\n{RED}WARNING: these runs used different task sets "
              f"({', '.join(sorted(stamps_a))} vs {', '.join(sorted(stamps_b))}).{RESET}")
        print(f"{DIM}  Only shared task ids are compared, so the result below is "
              f"a smaller\n  comparison than it appears. Pin one task set and "
              f"re-run both arms.{RESET}")

    result = mcnemar(a, b)
    lo_a, hi_a = wilson(round(result.a_rate * result.shared_tasks), result.shared_tasks)
    lo_b, hi_b = wilson(round(result.b_rate * result.shared_tasks), result.shared_tasks)
    delta = result.b_rate - result.a_rate

    print(f"\n{BOLD}{'':<12}{'PASS RATE':>11}{'95% CI':>18}{RESET}")
    print(f"{cfg.label_a:<12}{result.a_rate:>10.0%}{f'{lo_a:.0%} – {hi_a:.0%}':>18}")
    print(f"{cfg.label_b:<12}{result.b_rate:>10.0%}{f'{lo_b:.0%} – {hi_b:.0%}':>18}")

    colour = GREEN if delta > 0 else (RED if delta < 0 else "")
    print(f"\n{colour}{'change':<12}{delta:>+10.0%}{RESET}   over {result.shared_tasks} shared tasks")

    print(f"\n{BOLD}PAIRED TEST (McNemar, exact){RESET}")
    print(f"  solved by {cfg.label_a} only:  {result.a_only}")
    print(f"  solved by {cfg.label_b} only:  {result.b_only}")
    print(f"  solved by both:  {result.both}    neither: {result.neither}")
    print(f"\n  {BOLD}{result.verdict(cfg.label_a, cfg.label_b)}{RESET}")

    note = result.power_note()
    if note:
        print(f"\n  {DIM}{note}{RESET}")
    if abs(delta) > 0 and not result.significant:
        need = tasks_needed(result.a_rate, abs(delta) or 0.05)
        if need:
            print(f"  {DIM}An unpaired study of this effect would need ~{need} tasks; "
                  f"you have {result.shared_tasks}.{RESET}")

    payload = {
        "baseline_rate": round(result.a_rate, 4),
        "current_rate": round(result.b_rate, 4),
        "delta": round(delta, 4),
        "shared_tasks": result.shared_tasks,
        "p_value": round(result.p_value, 5),
        "significant": result.significant,
        "verdict": result.verdict(cfg.label_a, cfg.label_b),
    }
    if cfg.json:
        cfg.json.parent.mkdir(parents=True, exist_ok=True)
        cfg.json.write_text(json.dumps(payload, indent=2))

    status = 0
    if cfg.fail_under is not None and result.b_rate < cfg.fail_under:
        print(f"\n{RED}FAIL: pass rate {result.b_rate:.0%} is below the floor of "
              f"{cfg.fail_under:.0%}{RESET}")
        status = 1
    if cfg.fail_on_regression and result.significant and result.b_rate < result.a_rate:
        print(f"\n{RED}FAIL: significant regression (p = {result.p_value:.4f}){RESET}")
        status = 1
    print()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
