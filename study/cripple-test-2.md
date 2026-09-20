# The cripple test failed a second time, for a different reason

Run 2026-09-19, after the pre-registered gate in `cripple-test.md` failed.
Explicitly **post-hoc**. It does not replace that failure; both are reported.

## Why it was re-run

The pre-registered gate used `qwen2.5-coder:7b`, which scores ~8% on this task
set. A cripple test measures a *decrease*. An arm that starts at 8% has no
headroom to decrease from, so the test could not have succeeded at any sample
size. `qwen3:8b` scores ~52% on the same tasks — the headroom the test needed.

Changing the model after seeing a failure is exactly the move pre-registration
exists to prevent, and it is recorded here as such. What makes it defensible is
that the original design's flaw is visible in the design, not in the result: an
8% baseline cannot be halved into detectability.

## Result

Same 12 pinned toolbox tasks, `--max-turns 1` against `--max-turns 14`,
`--max-nudges 0`, paired.

| arm | solved | met FAIL_TO_PASS | collateral | tool calls | harness errors | mean |
|---|---|---|---|---|---|---|
| 1 turn | **0/12** | 1 | 1 | 12 | 0 | 94s |
| 14 turns | **4/12** | 7 | 3 | 90 | 1 | 636s |

```
1-turn       0%     0% – 24%
14-turn     33%    14% – 61%
change    +33pts

McNemar (exact, paired)
  solved by 1-turn only:   0
  solved by 14-turn only:  4
  solved by both:          0     neither: 8
  p = 0.125 — no detectable difference
```

The direction is perfect. Four tasks flipped fail → solve when given more
turns; **none** flipped the other way. The effect is 33 points. And it is still
not significant.

## Why it could not have passed

The exact McNemar test sees only discordant pairs — tasks where the two arms
disagree. Concordant tasks carry no information about the difference. With all
discordant pairs falling in one direction, the smallest attainable p is
`2 × (1/2)^k` for `k` pairs:

| discordant pairs | smallest possible p | |
|---|---|---|
| 3 | 0.250 | cannot clear 0.05 |
| **4** | **0.125** | **observed** |
| 5 | 0.0625 | cannot clear 0.05 |
| **6** | **0.031** | first value that clears 0.05 |

A 33-point effect over 12 tasks yields about four discordant pairs. The gate
needed six. **No outcome consistent with the true effect size could have
passed at n = 12.** Groundhog's own output said so as it printed the result:
*"An unpaired study of this effect would need ~21 tasks; you have 12."*

So the gate has now failed twice, both times from a design error in the gate
rather than a defect in the harness, and both times diagnosable before the run:

1. **First** — the crippled arm had no headroom to fall from.
2. **Second** — the task set was too small to resolve the effect that existed.

The second failure is the more interesting one. It is a concrete instance of
the thing this project keeps finding: a 12-task benchmark cannot detect a
33-point difference, and a benchmark that reports a single percentage without
an interval will hide that from you.

## Is the harness valid?

The gate is the instrument that was supposed to answer this, and it did not.
Saying otherwise afterwards would be inventing a pass. What can be said is
narrower, and is stated in the report as such:

- Harness errors: 1 in 24 attempts across both arms.
- Directionality is clean — 4 flips, all one way.
- The scripted agents in `groundhog demo` still separate honest (3/3) from
  idle (0/3) with no model involved.

That is evidence of a *power* failure rather than a *validity* failure. It is
not a substitute for the gate. Accordingly, nothing in `REPORT.md` is presented
as a confirmatory hypothesis test. Results are descriptive: rates with Wilson
intervals, per-model and per-repository breakdowns, and mechanism shown through
saved patches (`study/collateral.md`).

## An unregistered finding, larger than the one being tested

The main sweep's `qwen3:8b` scored **9/12** on these same twelve tasks. This
arm's 14-turn condition scored **4/12**. The single difference is
`--max-nudges 0`.

The nudge mechanism — running the test suite when a model claims it has
finished, and pushing back when it has not — is worth **five of twelve tasks**
for this model. That is larger than the turn-limit effect the gate was built to
measure.

It also changes the failure profile. `qwen3:8b` produced **zero**
collateral-damage cases in all 38 sweep attempts. With nudges off, it produced
**three in twelve**. Nudging was suppressing them.

This was not pre-registered and is not tested here. It is recorded because it
is a confound for any Groundhog number reported without stating the nudge
setting, and because it suggests the harness's scaffolding can matter more than
the model's turn budget — which, if it holds up, is a result about agent
evaluation generally and not about `qwen3:8b`.

## Reproducing

```
bash scripts/cripple_test_v2.sh          # REPOS_DIR must be set
```

Raw: `results/cripple2-1.jsonl`, `results/cripple2-14.jsonl`,
`study/cripple2.json`.
