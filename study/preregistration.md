# Pre-registration — the Ollama cohort study

Written 2026-09-19, before any model was run against the study task set.
**Not edited afterwards.** Deviations are recorded in `REPORT.md` under
"Departures from pre-registration", not by amending this file.

## What is being measured, and what is not

The study as originally designed had two hypotheses. Only one of them is
runnable on this machine, and saying so up front is the point of a
pre-registration.

**H1 — contamination.** Do agents solve tasks mined from commits predating a
model's training cutoff at a higher rate than tasks from after it?

**H1 is deferred, not attempted.** There is no API key on this machine, so the
only available models are local 7–14B ones, and those scored 0/10 on real httpx
tasks in earlier calibration. Two cohorts of zero measure task difficulty, not
era — a failure mode listed explicitly in the study design. The infrastructure
is built (`report --cutoff`), both cohorts will be mined and pinned, and H1 runs
as one command when a capable model is available. It will not be reported as a
null result, because it was never powered to produce one.

**H2 — collateral damage.** How often does an agent make its target test pass
while breaking something that already passed?

H2 is measurable regardless of solve rate, because it is conditioned on
`fail_to_pass_passed`, not on `solved`. It is the primary outcome.

**H3 — mechanism (added at design time, not post hoc).** Do models differ
measurably in *how* they work, independent of whether they succeed?

Solve rate over ~150 tasks is ~150 observations, mostly zero for a laptop model.
The same runs produce thousands of tool calls. H3 asks whether tool-error rate,
unknown-tool rate, text-recovered-call rate and turns-to-first-edit separate
models that all score near zero. This is the hypothesis that makes local models
scientifically useful rather than merely cheap.

## Hypotheses and expected direction

| | Claim | Expected |
|---|---|---|
| H2 | Collateral damage occurs at a non-trivial rate | > 5% of attempts that meet FAIL_TO_PASS |
| H3a | Models differ in tool-call error rate | qwen2.5-coder lower than general models |
| H3b | Tool calls arrive as text at model-specific rates | qwen2.5-coder ~100%, qwen3/llama near 0% |
| H3c | Larger models within a family reach their first edit sooner | 14b earlier than 7b |
| Cripple | `--max-turns 1` scores significantly worse than `--max-turns 25` | Yes, or the study is invalid |

## Outcomes

**Primary (H2).** Collateral-damage rate = attempts where
`fail_to_pass_passed == true and solved == false`, over all attempts meeting
FAIL_TO_PASS. Reported with a Wilson interval, overall and per model. Every
such case is inspected via its saved patch, and at least three are described
concretely. A rate without examples is not a finding.

**Secondary (H3).** Per-model tool metrics with Wilson intervals where they are
proportions.

**Validity gate (cripple test).** Same model, same tasks, `--max-turns 1` vs
`--max-turns 25`, compared with `groundhog compare` (exact McNemar, paired). If
this does not come out significantly worse, the setup cannot detect a known-large
effect and no null reported afterwards means anything. **The study stops there
if it fails.**

**Harness-error rate** = attempts with a non-empty `error` that is neither a
tamper nor a provider failure. Must be near zero. If it differs systematically
between models, H3 is contaminated by the harness rather than measuring models.

## Stopping rules

- Runs stop when every pinned task has been attempted, or at 12 hours of wall
  clock, whichever comes first.
- No peeking-then-extending. If a result is underpowered it is reported as
  underpowered.
- If the cripple test fails, the study stops and reports that.
- Zero total solves across all models means H2 is unmeasurable; that will be
  reported as an inconclusive primary outcome rather than reframed into
  something that did produce a number.

## Declared confounds and limits

1. **Task difficulty is not held constant across repos.** Absolute capability
   swings a median of 36 points between repositories. Per-repo breakdowns are
   reported so a single repo cannot carry an effect.
2. **The task distribution is narrow.** Mining selects single commits of 3–200
   lines across at most 5 files, with tests. Across 26 mined httpx candidates,
   none touched concurrency code. Results generalise to that slice and no
   further.
3. **Tests are a proxy for correctness.** An agent can make tests pass in ways a
   reviewer would reject. PASS_TO_PASS and tamper detection rule out the crude
   cheats, not the subtle ones.
4. **Local models are weak.** Near-zero solve rates are expected. H2 and H3 are
   designed to survive that; the solve rate itself is not the deliverable.
5. **Machine-specific.** Every timing number is from one M1 Pro with 16 GB, and
   the 14b model is memory-constrained there. Timings do not transfer.
6. **`recovered_from_text` is a confound for H3a.** A model whose calls arrive as
   prose is being measured partly on Groundhog's parser. Reported alongside
   error rate rather than folded into it.

## Power

With roughly 150 tasks and five models, per-model solve-rate comparisons detect
only large effects. H2 pools across all attempts and so has several hundred
observations. H3 pools across all tool calls and has thousands.

A null H2 result means **"no large effect at this sample size"**, never "no
effect". Committed to that phrasing now, before seeing any number.

## Fixed before the run

- Task sets are pinned and committed; their manifest hashes appear in the report.
- Models: qwen2.5-coder:7b, qwen2.5-coder:14b, qwen3:8b, llama3.1:8b, mistral:7b.
- `--max-turns 14`, `--repeats 1` for the main sweep (repeats reduce per-task
  noise but do not multiply sample size, and wall clock is the binding
  constraint on a laptop).
- `--p2p-scope` as auto-selected per language: `file` for Python and
  JavaScript, `full` for Go.
