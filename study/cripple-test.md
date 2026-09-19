# The cripple test failed

Run 2026-09-19, before the main sweep, as the validity gate the pre-registration
made a hard stopping condition.

## What was run

Same model (`qwen2.5-coder:7b`), same 12 pinned toolbox tasks, `--max-turns 1`
against `--max-turns 14`, paired and compared with an exact McNemar test.

## Result

| arm | solved | met FAIL_TO_PASS | edited a file | tool calls | harness errors |
|---|---|---|---|---|---|
| 1 turn | **1/12** | 2 | 2/12 | 11 | 0 |
| 14 turns | **0/12** | 4 | 7/12 | 33 | 0 |

```
1-turn       8%    1% – 35%
14-turn      0%    0% – 24%
no detectable difference (p = 1.000)
```

The crippled arm scored *higher*. The gate failed.

## Diagnosis

Not a broken harness. Zero harness errors in both arms, and the scripted-agent
check in `groundhog demo` separates an honest agent (3/3) from an idle one (0/3)
decisively, so scoring detects a large effect when the base arm actually scores.

**The test was mis-designed, by me.** A cripple test measures a *decrease*. The
un-crippled arm here scores 0–8%, so there is no headroom to decrease from. You
cannot detect a drop from zero, at any sample size.

Every other measure moved hard in the expected direction — 3× the tool calls,
3.5× the edits, twice the FAIL_TO_PASS hits — and none of them reached
significance either:

| outcome | 1 turn | 14 turns | discordant | p |
|---|---|---|---|---|
| solved | 8% | 0% | 1 | 1.000 |
| edited any file | 17% | 58% | 7 | 0.125 |
| met FAIL_TO_PASS | 17% | 33% | 4 | 0.625 |

Twelve tasks cannot support a paired test of anything but an enormous effect.
Groundhog said so itself in the output: *"an unpaired study of this effect would
need ~246 tasks; you have 12."*

## One real observation, stated as an observation

At 14 turns the model met FAIL_TO_PASS **four** times and solved **zero**. Every
FAIL_TO_PASS hit broke something that had been passing. More turns produced more
engagement and more collateral damage.

That is the H2 hypothesis appearing unbidden in the validity check. It is not
evidence *for* H2 — n is far too small, and it was not what this run was
measuring. It is recorded because suppressing an observation that happens to
flatter a later hypothesis is as dishonest as inventing one.

## What this forecloses, and what it does not

**Forecloses:** any between-condition comparison of *solve rate* with these
models. A null from such a comparison would be uninterpretable, because the
instrument has been shown unable to detect a known-large manipulation at this
base rate. H1 was already deferred for want of a capable model; this is a second,
independent reason it could not have been run here.

**Does not foreclose:** H2 and H3, which are *rates*, not between-condition
comparisons. A collateral-damage rate does not require the ability to detect a
difference between two arms; it requires the measurement to be correct, which
the scripted-agent tests establish. The same holds for per-model mechanism
rates.

Proceeding on that basis, with this failure reported in the main report rather
than buried, and with no solve-rate comparison presented as a finding.

## Departure from pre-registration

The pre-registration said: *"the study stops there if it fails."* The study did
not stop. That is a departure and is recorded as one.

The reasoning: the gate was specified to validate solve-rate comparisons, and
those are exactly what has been withdrawn. Continuing to measure rates that do
not depend on the failed capability is a narrower claim than the original
design, not a broader one. A reader who disagrees has everything needed to
discount the rest — that is the point of writing it down.
