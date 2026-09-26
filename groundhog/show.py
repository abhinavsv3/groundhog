#!/usr/bin/env python3
"""Inspect one task: what it asked, and what every agent did to it.

    groundhog show 87eb7a4e9b0c
    groundhog show 87eb7a4e9b0c --model qwen2.5-coder:7b

After a run the interesting question is *how* an attempt failed, and the
answer is spread over tasks/*.jsonl, results/*.jsonl and results/patches/.
This assembles it. The human's patch is printed last, for comparison.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BOLD, DIM, GREEN, RED, CYAN, YELLOW, RESET = (
    "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[36m", "\033[33m", "\033[0m",
)


def rows(paths: list[Path]) -> list[dict]:
    """Every record, tagged with the file it came from.

    The same model can attempt the same task in several experiments (a
    main sweep, a cripple test, an ablation), so the file is part of an
    attempt's identity and is shown next to it.
    """
    out: list[dict] = []
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    out.append({**json.loads(line), "_source": str(path)})
                except json.JSONDecodeError:
                    continue
    return out


def find_task(task_id: str, task_files: list[Path]) -> dict | None:
    for task in rows(task_files):
        if task.get("sha", "").startswith(task_id):
            return task
    return None


def find_attempts(task_id: str, result_files: list[Path], model: str | None) -> list[dict]:
    seen: set[tuple[str, int, str]] = set()
    out = []
    for r in rows(result_files):
        if not r.get("task_id", "").startswith(task_id[:12]):
            continue
        if model and not (r["model"] == model or r["model"].endswith(":" + model)):
            continue
        key = (r["model"], int(r.get("run_index", 0)), r["_source"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def find_patch(record: dict, patch_dirs: list[Path]) -> Path | None:
    named = record.get("patch_file")
    if named and Path(named).is_file():
        return Path(named)
    safe = record["model"].replace("/", "_").replace(":", "-")
    stem = f"{record['task_id']}.{safe}.{record.get('run_index', 0)}.diff"
    for d in patch_dirs:
        if (d / stem).is_file():
            return d / stem
        # Older study patches were stored by task id alone.
        if (d / f"{record['task_id']}.diff").is_file():
            return d / f"{record['task_id']}.diff"
    return None


def find_human_patch(task_id: str, patch_dirs: list[Path]) -> Path | None:
    for d in patch_dirs:
        p = d / f"{task_id}.human.diff"
        if p.is_file():
            return p
    return None


def paint(diff: str, colour: bool) -> str:
    if not colour:
        return diff
    out = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            out.append(BOLD + line + RESET)
        elif line.startswith("+"):
            out.append(GREEN + line + RESET)
        elif line.startswith("-"):
            out.append(RED + line + RESET)
        elif line.startswith("@@"):
            out.append(CYAN + line + RESET)
        else:
            out.append(line)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", help="task id (sha prefix)")
    ap.add_argument("--model", help="only this model's attempts (provider prefix optional)")
    ap.add_argument("--tasks", type=Path, action="append",
                    help="task files to search (default: tasks/*.jsonl)")
    ap.add_argument("--results", type=Path, action="append",
                    help="result files to search (default: results/*.jsonl)")
    ap.add_argument("--patches", type=Path, action="append",
                    help="patch directories (default: results/patches, study/patches)")
    ap.add_argument("--no-diff", action="store_true", help="omit the patches")
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    cfg = ap.parse_args()

    colour = cfg.color == "always" or (cfg.color == "auto" and sys.stdout.isatty()
                                        and not os.environ.get("NO_COLOR"))
    b, d, r = (BOLD, DIM, RESET) if colour else ("", "", "")

    task_files = cfg.tasks or sorted(Path("tasks").glob("*.jsonl"))
    result_files = cfg.results or sorted(Path("results").glob("*.jsonl"))
    patch_dirs = cfg.patches or [Path("results/patches"), Path("study/patches")]

    task = find_task(cfg.task, task_files)
    attempts = find_attempts(cfg.task, result_files, cfg.model)
    if task is None and not attempts:
        print(f"no task or attempt matching {cfg.task!r} in "
              f"{len(task_files)} task file(s) and {len(result_files)} result file(s)",
              file=sys.stderr)
        return 1

    task_id = (task or attempts[0])["sha" if task else "task_id"][:12]
    subject = (task or attempts[0])["subject"]
    print(f"\n{b}{task_id}  {subject}{r}")
    if task:
        traits = ", ".join(str(task[k]) for k in ("shape", "size") if task.get(k))
        if task.get("concurrency"):
            traits += ", concurrency"
        print(f"{d}{task.get('date', '')[:10]}  {task.get('language', '')}  {traits}{r}")
        print(f"{d}source: {', '.join(task.get('source_files', []))}{r}")
        print(f"{d}tests:  {', '.join(task.get('test_files', []))}{r}")
        f2p, p2p = task.get("fail_to_pass") or [], task.get("pass_to_pass") or []
        print(f"\n{b}FAIL_TO_PASS{r} ({len(f2p)})")
        for t in f2p:
            print(f"  {t}")
        print(f"{b}PASS_TO_PASS{r} ({len(p2p)}, scope {task.get('p2p_scope', 'file')})")
        for t in p2p[:12]:
            print(f"  {d}{t}{r}")
        if len(p2p) > 12:
            print(f"  {d}... and {len(p2p) - 12} more{r}")
        if task.get("failure_excerpt"):
            print(f"\n{b}Failure before the fix{r}")
            for line in task["failure_excerpt"].splitlines():
                print(f"  {d}{line}{r}")

    if attempts:
        print(f"\n{b}ATTEMPTS{r} ({len(attempts)})")
    for rec in sorted(attempts, key=lambda x: (x["model"], x["_source"], x.get("run_index", 0))):
        tools = rec.get("tools") or {}
        if rec.get("tampered_with_tests"):
            verdict, why = f"{RED if colour else ''}not solved{r}", "edited a test file"
        elif rec.get("solved"):
            verdict, why = f"{GREEN if colour else ''}solved{r}", ""
        elif rec.get("fail_to_pass_passed"):
            verdict = f"{YELLOW if colour else ''}not solved{r}"
            why = f"target tests passed, but broke {rec.get('broke_pass_to_pass', 0)} passing test(s)"
        else:
            verdict, why = f"{RED if colour else ''}not solved{r}", (rec.get("error") or "target tests still fail")
        run_tag = f" r{rec['run_index'] + 1}" if rec.get("run_index") else ""
        print(f"\n  {b}{rec['model']}{run_tag}{r}  {verdict}  {d}{why}{r}")
        if len(result_files) > 1:
            print(f"  {d}from {rec['_source']}{r}")
        calls = tools.get("by_name") or {}
        summary = ", ".join(f"{k} x{v}" for k, v in sorted(calls.items(), key=lambda kv: -kv[1]))
        print(f"  {d}{rec.get('turns', 0)} turns, {tools.get('calls', 0)} tool calls"
              f"{': ' + summary if summary else ''}; {rec.get('seconds', 0):.0f}s"
              f"{'; text-recovered ' + str(tools['recovered_from_text']) if tools.get('recovered_from_text') else ''}{r}")
        if rec.get("files_touched"):
            print(f"  {d}touched: {', '.join(rec['files_touched'][:6])}{r}")
        if not cfg.no_diff:
            patch = find_patch(rec, patch_dirs)
            if patch:
                print(f"  {d}{patch}{r}")
                print(paint(patch.read_text().rstrip(), colour))
            elif rec.get("files_touched"):
                print(f"  {d}(no patch on disk; run without --no-patches to keep them){r}")

    if not cfg.no_diff:
        human = find_human_patch(task_id, patch_dirs)
        if human:
            print(f"\n{b}THE HUMAN'S FIX{r}  {d}{human}{r}")
            print(paint(human.read_text().rstrip(), colour))
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
