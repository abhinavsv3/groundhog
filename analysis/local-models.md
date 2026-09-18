# Local models on real bugs: a calibration run

Run on 2026-09-18 against `encode/httpx` tasks, via Ollama on an M1 Pro (16GB).
Stopped early — once the pattern was clear, the remaining hours had nothing left
to teach.

| Model | Solved | Edited source | Harness errors | Mean time |
|---|---|---|---|---|
| qwen2.5-coder:7b | 0 / 8 | 3 / 8 | 0 | 55s |
| qwen2.5-coder:14b | 0 / 2 | 2 / 2 | 0 | 259s |

## What it established

**The harness is clean.** Zero harness errors in 10 attempts. Every failure was
the model getting the fix wrong, not the tooling falling over. That was the point
of the run, and it passed.

**Local 7–14B models do not close real bugs in a mature codebase.** Not a
surprise, but worth stating with numbers rather than assuming it.

**They fail by acting wrongly, not by failing to act.** Half the attempts edited
source. The models engage with the task and produce a plausible-looking patch
that does not pass. That distinction matters: "wrote nothing" would have implied
a harness problem, and originally it *was* one.

**Every single attempt hit the nudge ceiling.** Mean nudges = 2.0 out of a
maximum of 2, across every attempt and both models. Local models declare victory
without ever running the tests, universally. Before the completion check existed,
this run would have recorded them as finishing quickly and failing — indistinguish-
able from trying hard and failing.

**Model size costs more than it returns here.** The 14B was 4.7x slower than the
7B, not the ~2x its parameter count suggests, on a 16GB machine. It edited source
more reliably and still solved nothing.

## What it did not establish

Anything about per-repo spread, which is the question Groundhog exists to answer.
Spread is undefined when every score is zero. Measuring it needs models that
solve a meaningful fraction of tasks; see `analysis/divergence.py` for the same
question answered on 130 SWE-bench submissions instead.

## Reproducing

```bash
REPOS_DIR=~/src scripts/local_run.sh
```
