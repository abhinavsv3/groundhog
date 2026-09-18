# Using Groundhog for research

Groundhog was built as an engineering tool, but the property that makes it useful
for measurement — a fixed, verified task set with objective scoring — is what an
ablation needs.

## What makes it a usable substrate

**Objective scoring.** A task passes when the repository's own tests pass. No
judge model, no rubric, no LLM grading a response. Whatever drift affects your
experiment, it is not scoring drift.

**Verified solvability.** Every task is checked to fail without the original fix
and pass with it. A task that nothing can solve tells you nothing; a task that
passes without any change is worse, because it silently inflates every condition
equally.

**Contamination control.** Tasks come from commits. Mine commits merged after a
model's training cutoff and you have evaluation data that model provably has not
memorised. Re-mine next quarter for a fresh set at no labelling cost.

**A harness you can actually read.** The agent loop is ~240 lines in one file.
Changing the tool set, the prompt or the turn limit is editing a function, not
navigating a framework.

## A worked example: does tool naming matter?

Hold everything fixed except the names of the tools your agent exposes, and let
Groundhog run **your** agent rather than its own:

```bash
# condition A — your current tool names
python -m groundhog run . --tasks tasks/pinned.jsonl --repeats 5 \
    --agent-cmd "./my-agent --tools tools-plain.json --task {prompt_file}" \
    --out results/names-plain.jsonl

# condition B — descriptive names, nothing else changed
python -m groundhog run . --tasks tasks/pinned.jsonl --repeats 5 \
    --agent-cmd "./my-agent --tools tools-descriptive.json --task {prompt_file}" \
    --out results/names-descriptive.jsonl

python -m groundhog compare results/names-plain.jsonl results/names-descriptive.jsonl \
    --label-a plain --label-b descriptive
```

Your agent gets a prepared git worktree and a prompt file; what it does in
between is entirely yours. Placeholders available in `--agent-cmd` are
`{prompt_file}`, `{prompt}`, `{repo}` and `{test_cmd}`, and the same values are
exported as `GROUNDHOG_PROMPT_FILE` and `GROUNDHOG_TEST_CMD`.

**Scoring stays outside your agent's reach**, which is what makes the comparison
worth anything:

- every `FAIL_TO_PASS` test must pass
- every `PASS_TO_PASS` test must *still* pass — so deleting the rest of the suite
  does not read as a fix
- the test files must be byte-identical to what Groundhog handed over; an agent
  with a shell can edit them, and editing them fails the task

The comparison is paired on tasks and tested with an exact McNemar test.

**A caution specific to this experiment.** Tool naming is plausibly a small
effect, and pass/fail is a lossy instrument for detecting one — you are
collapsing an entire session into a single bit. A 45-task run generates on the
order of 1,500 tool calls, and naming should move *tool-selection* behaviour long
before it moves solve rate. Instrumentation for that is
[#9](https://github.com/abhinavsv3/groundhog/issues/9); until it lands, expect a
null result on a small task set and do not read it as "naming does not matter".

## Statistical power — read this before designing the study

This is where agent ablations usually go wrong. Groundhog prints a warning when a
null result is uninformative, but the design decision is yours.

| Effect you want to detect | Unpaired tasks needed (80% power) |
|---|---|
| 40% → 60% (large) | ~100 |
| 40% → 50% (moderate) | ~390 |
| 40% → 45% (subtle) | ~1,500 |

Pairing does considerably better than these numbers, because it removes
between-task variance. But the shape holds: **a 40-task set can detect a large
effect and nothing else.** If you expect tool naming to move pass rate by three
points, no amount of care on a small set will show it.

Practical consequences:

- **Mine more repositories.** Task count is the binding constraint, and mining is
  cheap — `pallets/click` yielded 82 candidates from 400 commits. Ten repos gets
  you into the hundreds.
- **Use `--repeats`.** Repeats reduce per-task noise but do not multiply your
  sample size; five attempts on 40 tasks is not 200 independent observations.
  Groundhog collapses repeats by majority vote for exactly this reason.
- **Report intervals, not point estimates.** `compare` prints Wilson intervals.
  A benchmark number without one invites over-reading.

## Reporting results

A Groundhog number is meaningless without the task set that produced it. Report:

- the repositories, and the commit range mined from each
- how many candidates were validated, and how many rejected
- the model, the turn limit, and `--repeats`
- the Groundhog version or commit

Two studies that both say "42% on Groundhog" are not comparable unless those
match.

## Check your harness before you trust your result

We found four bugs in two days that produced plausible numbers rather than
errors — an editable install shadowing the worktree, models claiming completion
having edited nothing, PEP 735 groups installing silently, and a stale
environment cache. See [harness-artifacts.md](../analysis/harness-artifacts.md),
which finds the same class of failure in published SWE-bench submissions.

Before reporting an effect, check the boring explanations:

- Does every condition actually *edit files*? An agent that never acts scores
  zero for reasons unrelated to your variable.
- Is the error count zero? `analysis/spread.py` separates harness failures from
  model failures.
- Does a deliberately crippled condition (`--max-turns 1`) show a significant
  difference? If it does not, your setup cannot detect anything.

That last one is the cheapest sanity check available, and it is worth running
before any real experiment.

## Citing

See [CITATION.cff](../CITATION.cff). If you use
[`analysis/divergence.py`](../analysis/divergence.py) or
[`analysis/artifacts.py`](../analysis/artifacts.py), cite the
[SWE-bench experiments repository](https://github.com/SWE-bench/experiments)
alongside it — the underlying data is theirs.
