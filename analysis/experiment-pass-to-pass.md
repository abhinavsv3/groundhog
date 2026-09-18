# Does PASS_TO_PASS scoring change the answer?

A small end-to-end experiment, run entirely on a laptop with Ollama. No API
spend.

## Why this experiment

Groundhog scored tasks on FAIL_TO_PASS alone until [#4] landed. The worry was
theoretical: an agent could make the target test pass while breaking everything
around it, and a FAIL_TO_PASS-only harness would call that a fix.

We wanted to know whether that actually happens, or whether it is the kind of
risk that never materialises.

## Setup

A 12-commit Python library, each commit adding one small utility plus its test —
the shape Groundhog mines. Deliberately easy, because the point was to get a
7B laptop model above a zero base rate, not to measure capability.

```
$ groundhog mine toolbox --since "10 years ago"     12 candidates
$ groundhog validate toolbox                        12/12 valid, 5s, no flags
$ groundhog run toolbox --models openai:qwen2.5-coder:7b --max-turns 12
```

Each attempt records both what a FAIL_TO_PASS-only harness would have concluded
and what the strict rule concludes. **Identical runs, two scoring rules** — a
perfectly paired comparison with no stochasticity between conditions.

## Result

| Scoring | Solved | Rate | 95% CI |
|---|---|---|---|
| FAIL_TO_PASS only | 5 / 12 | 42% | 19–68% |
| FAIL_TO_PASS + PASS_TO_PASS | 2 / 12 | 17% | 5–45% |

Three attempts made their target test pass **while breaking tests that were
already green**:

| Task | Target test | PASS_TO_PASS broken |
|---|---|---|
| `count_words()` | passed | 8 |
| `truncate()` | passed | 3 |
| `dedupe()` | passed | 2 |

The mechanism was consistent: the model overwrote the source file with only the
function it had been asked for, deleting every function added by earlier
commits. Its own test passed. The library was destroyed.

Nobody constructed this. A 7B model produced it spontaneously, three times out
of twelve, on its first outing.

## What this does and does not establish

**Does:** the pipeline runs end to end — mine, validate, run, score, compare —
on a repo it had never seen, with no configuration. And the PASS_TO_PASS guard
catches real cheating by a real model, not just by synthetic test agents.

**Does not:** establish that 42% vs 17% is a statistically reliable difference.
Groundhog's own `compare` says so:

```
change            -25%   over 12 shared tasks
  solved by naive only:  3
  solved by strict only: 0
  no detectable difference (p = 0.250)

  Only 3 tasks differ between the conditions. That is too few to detect
  anything but a very large effect.
  An unpaired study of this effect would need ~62 tasks; you have 12.
```

With three discordant pairs, an exact McNemar test cannot return below p = 0.25
no matter how lopsided they are. The magnitude is large; the sample is not.
Establishing the size of this effect properly needs ~60+ tasks.

**Also worth noting:** a first run of the same configuration scored 1/12 strict
rather than 2/12. Temperature matters, and single runs of 12 tasks are noisy.
Use `--repeats`.

## The honest summary

The guard fires on real behaviour, and the direction is unambiguous — every
discordant task went the same way, because a broken library cannot accidentally
un-break itself. Whether the true inflation is 2x or 5x, this experiment cannot
say.

[#4]: https://github.com/abhinavsv3/groundhog/issues/4
