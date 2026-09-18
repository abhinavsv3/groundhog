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

Hold everything fixed except the names of the tools exposed to the model.

```python
# groundhog/run.py — condition B
def tools_for(test_files):
    return [
        {"name": "inspect_directory", ...},   # was list_files
        {"name": "open_source_file", ...},    # was read_file
        {"name": "apply_patch", ...},         # was write_file
        {"name": "verify", ...},              # was run_tests
    ]
```

```bash
python -m groundhog run . --tasks tasks/pinned.jsonl --repeats 5 \
    --models anthropic:claude-opus-5 --out results/names-plain.jsonl
# swap in condition B
python -m groundhog run . --tasks tasks/pinned.jsonl --repeats 5 \
    --models anthropic:claude-opus-5 --out results/names-descriptive.jsonl

python -m groundhog compare results/names-plain.jsonl results/names-descriptive.jsonl \
    --label-a plain --label-b descriptive
```

The comparison is paired on tasks and tested with an exact McNemar test. Only the
tasks where the two conditions disagree carry information, which is what makes a
modest task set usable.

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
