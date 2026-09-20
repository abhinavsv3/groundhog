# Nudge replication — result

Run against `study/nudge-preregistration.md`, which was committed before this
ran and fixed the direction, the task set, the analysis and the power
calculation in advance.

## What came back

Ten Go and JavaScript tasks, `qwen3:8b`, `--max-turns 14`, nudges the only
difference.

```
nudges off    4/10    40%    17% – 69%
nudges on     8/10    80%    49% – 94%
change      +40pts

McNemar (exact, paired)
  solved with nudges only:  4
  solved without only:      0
  both: 4    neither: 2
  p = 0.125
```

## Against what was declared

| declared in advance | outcome |
|---|---|
| Direction: nudges-on ≥ nudges-off | **holds** — 80% against 40% |
| Falsified if pairs split both ways | **0 of 4** went the other way |
| Falsified if the effect is Python-only | **holds in Go and JavaScript** |
| Expect ~4 discordant pairs, floor p = 0.125, cannot reach significance | **exactly 4 pairs, p = 0.125** |

The pre-registration predicted the result *including its non-significance*, and
that is what arrived. The effect size replicated closely on fresh tasks in
different languages: **42 points** in the exploratory Python set, **40 points**
here.

Two independent experiments, same direction, same magnitude, neither
individually significant. That is replication, which is a different and in some
ways better kind of evidence than one run clearing 0.05. It is not a
substitute for adequate power, and it is not claimed as one.

**The 22 tasks are not pooled.** Pooling would give nine one-directional
discordant pairs and clear the bar comfortably. It would also be precisely the
peeking-then-extending the pre-registration refused, so the number is not
computed here.

## What did not replicate

The secondary observation did not hold. In the Python toolbox set, turning
nudges off produced **3 collateral-damage cases in 12**. In this Go and
JavaScript set it produced **0 in 10**.

So "nudging suppresses collateral damage" is, on this evidence, a Python
finding and possibly a `write_file`-semantics finding — the mechanic in
`study/collateral.md` is whole-module replacement, and the Go and JS tasks in
this set are smaller files with less to destroy. It is not a general property
of the nudge mechanism, and `REPORT.md` says so.

The solve-rate effect replicated. The failure-mode effect did not. Reporting
only the first would have been the more flattering choice.

## Why it matters

The claim is not about `qwen3:8b`. Re-running the test suite when a model says
it has finished, and telling it the truth, moved the solve rate by 40 points —
more than tripling the turn budget did in the same harness on the same tasks
(33 points, `study/cripple-test-2.md`).

A published benchmark number that does not state its scaffolding is therefore
not comparable to another lab's number, even on identical tasks with an
identical model. Groundhog records `nudges` per attempt; most harnesses have an
equivalent and few report it.

## Raw

`results/nonudge-gotoolbox.jsonl`, `results/nonudge-jstoolbox.jsonl`,
`results/nudge-all.jsonl`, `study/nudge-replication.json`.
