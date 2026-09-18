# Running Groundhog continuously

The single-run question — "what fraction of our bugs can an agent close?" — is
answered once and then forgotten. The recurring question is more useful:

> **Did the change we just made to our agent setup help or hurt?**

You change a system prompt, upgrade a model, add an MCP server, edit
`CLAUDE.md`, raise a turn limit. Today nobody measures any of it. This is how to.

## The shape of it

```
                 pin a task set  ────────────────┐
                                                 │
  before the change ──► groundhog run ──► baseline.jsonl
                                                 │
  after the change  ──► groundhog run ──► current.jsonl
                                                 │
                        groundhog compare  ◄─────┘
                                 │
                    exits non-zero on regression
```

The task set must be **pinned**, not re-mined, between the two runs. Comparing
across different task sets tells you nothing.

## Locally, in two commands

```bash
# once: mine and freeze a task set
python -m groundhog mine .        --since "12 months ago"
python -m groundhog validate .    --out tasks/pinned.jsonl

# before and after your change
python -m groundhog run . --models anthropic:claude-opus-5 \
    --tasks tasks/pinned.jsonl --repeats 3 --out results/baseline.jsonl
# ... make the change ...
python -m groundhog run . --models anthropic:claude-opus-5 \
    --tasks tasks/pinned.jsonl --repeats 3 --out results/current.jsonl

python -m groundhog compare results/baseline.jsonl results/current.jsonl \
    --label-a before --label-b after --fail-on-regression
```

```
              PASS RATE            95% CI
before             30%         18% – 45%
after              52%         37% – 67%

change            +23%   over 40 shared tasks

PAIRED TEST (McNemar, exact)
  solved by before only:  3
  solved by after only:  12
  solved by both:  9    neither: 16

  after is better (p = 0.0352)
```

**Why paired.** Comparing two aggregate percentages throws away the fact that
both runs faced the same tasks. McNemar's test uses only the tasks where the two
conditions disagree, which removes task difficulty from the comparison entirely
and is what makes a 40-task set usable at all.

**Why `--repeats`.** Models are stochastic. One attempt per task per condition
means you are partly measuring sampling noise. Three or more collapses each task
to a majority outcome, which is what a reliability measurement should use —
"solved at least once" rewards variance, which is the opposite of what you want.

## In CI

`.github/workflows/groundhog-regression.yml` in this repo is a working example.
The important parts:

```yaml
- name: Benchmark the current agent config
  run: |
    python -m groundhog run . \
      --models "${{ vars.GROUNDHOG_MODEL }}" \
      --tasks tasks/pinned.jsonl \
      --repeats 3 \
      --out results/current.jsonl
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

- name: Compare against the stored baseline
  run: |
    python -m groundhog compare \
      results/baseline.jsonl results/current.jsonl \
      --fail-on-regression --fail-under 0.30
```

`compare` exits non-zero when the current run is *significantly* worse, or when
the pass rate drops below a floor you set. That is the gate.

## Cost, which is the real constraint

A nightly run of 40 tasks × 3 repeats against a frontier model is 120 agent
sessions. That is real money, every night, forever.

Three ways to keep it sane:

- **Sample.** Run a random 15 of your pinned tasks each night and the full set
  weekly. Nightly numbers get wider confidence intervals; the weekly one is the
  number you trust.
- **Trigger on change, not on schedule.** Run when the agent config actually
  changes — a path filter on your prompt files, `CLAUDE.md`, or model pin — which
  is when the answer can differ.
- **Use a cheap model as a tripwire.** A small model's pass rate moves with
  harness breakage even when it is too weak to solve much. It will not detect
  subtle quality regressions, but it will catch the day someone breaks retrieval.

## Refreshing the task set

Pinned tasks go stale: they stop reflecting the code your team writes now, and a
long-lived set can leak into training data.

Re-mine quarterly, or whenever your codebase changes shape. Treat a re-mine as a
**new baseline** — numbers before and after are not comparable, and `compare`
will tell you so by reporting how few tasks are shared.

## What this cannot tell you

That your agent is *good*. It tells you whether it got better or worse at closing
bugs your team already fixed once, with tests that already existed. A model can
make tests pass in ways a reviewer would reject, and this will score that as a
win.
