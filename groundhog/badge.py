#!/usr/bin/env python3
"""A shields.io endpoint badge for a results file.

    groundhog badge --results results/results.jsonl --out badge.json

Commit badge.json (or publish it on Pages) and put this in the README:

    ![agent pass rate](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/OWNER/REPO/main/badge.json)

The badge refuses to show a number for a model the harness could not read,
for the same reason the report does: a percentage in a README is the most
context-free place a number can live, and a wrong one lives there forever.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .report import summarise


def colour(rate: float) -> str:
    if rate >= 0.7:
        return "brightgreen"
    if rate >= 0.5:
        return "green"
    if rate >= 0.3:
        return "yellow"
    if rate >= 0.15:
        return "orange"
    return "red"


def badge(rows: list[dict], model: str | None, label: str) -> dict:
    """The shields endpoint schema: schemaVersion, label, message, color."""
    usable = [r for r in rows if not r["unmeasured"]]
    if model:
        usable = [r for r in usable if r["model"] == model or r["model"].endswith(":" + model)]
    if not usable:
        return {"schemaVersion": 1, "label": label, "message": "not measured", "color": "lightgrey",
                "isError": True}
    best = usable[0]
    message = f"{best['pass_rate'] * 100:.0f}% ({best['solved']}/{best['total']})"
    if len(usable) > 1 and not model:
        message += f" · {best['model'].split(':', 1)[-1]}"
    return {"schemaVersion": 1, "label": label, "message": message,
            "color": colour(best["pass_rate"])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", type=Path, default=Path("results/results.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("badge.json"))
    ap.add_argument("--model", help="one model's rate; default is the best measured model")
    ap.add_argument("--label", default="agent pass rate")
    cfg = ap.parse_args()

    if not cfg.results.exists():
        print(f"no results at {cfg.results}", file=sys.stderr)
        return 1
    records = [json.loads(l) for l in cfg.results.read_text().splitlines() if l.strip()]
    payload = badge(summarise(records), cfg.model, cfg.label)
    cfg.out.parent.mkdir(parents=True, exist_ok=True)
    cfg.out.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"{payload['label']}: {payload['message']} -> {cfg.out}", file=sys.stderr)
    return 0 if not payload.get("isError") else 1


if __name__ == "__main__":
    raise SystemExit(main())
