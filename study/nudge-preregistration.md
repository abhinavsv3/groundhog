# Pre-registration: does the nudge mechanism carry the score?

Written 2026-09-19, **after** the effect was noticed in the cripple-test data
and **before** the replication below was run. The distinction matters and is
kept explicit throughout.

## Where this came from

It was not hypothesised in advance. It fell out of comparing two runs that
differed in one flag:

| | source | nudges | solved |
|---|---|---|---|
| A | `results/main-openai-qwen3-8b-toolbox.jsonl` | on (max 2) | **9/12** |
| B | `results/cripple2-14.jsonl` | `--max-nudges 0` | **4/12** |

Same model (`qwen3:8b`), same 12 pinned toolbox tasks, same `--max-turns 14`.
Verified identical task-ID sets, not merely identical counts.

```
nudges on    9/12   75%   47% – 91%
nudges off   4/12   33%   14% – 61%

McNemar (exact, paired)
  solved with nudges only:  5
  solved without only:      0
  both: 4    neither: 3
  p = 0.0625
```

Five discordant pairs, all one direction. The floor at five pairs is 0.0625, so
this misses 0.05 by one pair.

**This is an exploratory result and is reported as one.** It was found by
looking, the sample was not chosen in advance, and extending it now because it
nearly reached significance is the precise behaviour that makes p-values
meaningless. So the 12 toolbox tasks are not extended. They stay as the
exploratory finding that motivated what follows.

## The replication, declared before it is run

**H-N.** For `qwen3:8b` at `--max-turns 14`, disabling the nudge mechanism
reduces the solve rate.

**Direction is stated in advance:** nudges-on ≥ nudges-off. A result in the
opposite direction falsifies H-N regardless of magnitude.

**Tasks.** The 10 remaining validated tasks that already have a nudges-on
result from the main sweep, on repositories the exploratory set did not touch:
`gotoolbox` (6) and `jstoolbox` (4). These are Go and JavaScript, so the
replication also tests whether the effect is a Python-harness artifact.

**Arms.** Nudges-on is already recorded — `main-openai-qwen3-8b-gotoolbox.jsonl`
and `-jstoolbox.jsonl`, run before this file existed. Only the nudges-off arm
is new: identical invocation plus `--max-nudges 0`.

**Analysis.** Exact McNemar, paired on task ID, two-sided, α = 0.05. Wilson
intervals on both arms. Reported together with, never merged into, the
exploratory 12.

**Power, computed now rather than afterwards.** Ten tasks is small. If the
effect is the ~42 points seen in exploration, expect ~4 discordant pairs, which
floors at p = 0.125 and **cannot reach significance**. Six discordant pairs are
needed. This replication is therefore expected to be *descriptive*: it can
confirm direction and magnitude, and it cannot deliver a significant p on its
own.

That is stated here, in advance, so that a non-significant result is not
afterwards explained away. Pooling the 22 tasks would clear the bar easily —
and would be the same peeking-then-extending this section refuses.

**What would falsify H-N.** Any of: the nudges-off arm scoring at or above
nudges-on; discordant pairs splitting in both directions; or the effect
appearing only in Python.

**Stopping rule.** One run. No extension, no re-run, whatever comes back.

## Why it is worth the 50 minutes of GPU

If it holds, the claim is not about `qwen3:8b`. It is that a piece of harness
scaffolding — re-running the tests when a model claims to be finished, and
telling it the truth — moved the score more than tripling the turn budget did.
The gate this study built to measure the turn budget found 33 points; this
found 42.

It also changes the failure profile. `qwen3:8b` produced zero collateral-damage
cases across all 38 sweep attempts with nudges on, and three in twelve with
them off. The scaffolding was suppressing the failure mode described in
`study/collateral.md`.

Any benchmark number published without stating its nudge-equivalent setting is,
if this holds, not comparable to another lab's. That is a result about agent
evaluation, not about one 8B model.
