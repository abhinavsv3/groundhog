# Groundhog

**Your git history is an eval dataset. Nobody is using it.**

Every commit that changes source *and* tests is a verified problem/solution pair:
someone wrote a failing test, wrote the fix, and CI proved it worked. Your repo has
thousands of them, generated for free as a byproduct of your team doing its job.

Groundhog mines them, verifies them, and turns them into a benchmark for coding
models — on *your* codebase, in *your* language, with *your* conventions.

```
$ groundhog mine  https://github.com/encode/httpx
27 candidates from 171 commits                                    6s

$ groundhog validate
8/12 became real tasks                                           17s

$ groundhog run --models claude-opus-5,gpt-5.2,gemini-3-pro
```

## Why not just read a leaderboard?

Public benchmarks tell you which model is best at Django and sympy internals.
That is a real fact about the world and a poor predictor of how a model will do
in your TypeScript monorepo with unusual conventions and a slow test suite.

Groundhog answers a narrower and more useful question: **which model is best
here, in this repo?**

## How a task is built

Take a commit that touched both source and tests. Check out its parent. Copy in
the new tests but none of the new source. Then run the tests twice:

```
parent + new tests             must FAIL    (the task is solvable)
parent + new tests + real fix  must PASS    (the task is fair)
```

A candidate only becomes a task if both hold. That one check is what separates a
genuine bug fix from a refactor — and no amount of diff analysis can do it. On
the httpx run above, it automatically threw out a `ruff` formatting commit and
two pure refactors, because their tests passed without the fix.

The model then sees the repo at the parent commit and the failing test output.
It has to write the source change itself.

## Contamination

Tasks come from commits. Point Groundhog at commits merged after a model's
training cutoff and you get evals that model provably has not memorized. Re-run
next month against next month's commits and you have a fresh benchmark, forever,
with no human labeling.

## Install

```bash
git clone https://github.com/abhinavsv3/groundhog
cd groundhog && pip install -e .
```

## Usage

```bash
# 1. Find commits that look like tasks (pure git analysis, fast)
python -m groundhog.mine /path/to/repo --since "6 months ago"

# 2. Prove they actually fail-then-pass (runs the test suite)
python -m groundhog.validate /path/to/repo --venv .venv

# 3. Race models against them
python -m groundhog.run --models claude-opus-5,gpt-5.2
```

## Status

Early. Working today:

- [x] Task mining from git history
- [x] Fail-to-pass validation with worktree isolation
- [ ] Model runner (agent loop + sandboxed edits)
- [ ] Leaderboard UI
- [ ] Language support beyond Python

## Honest limitations

**Environments are the hard part, not mining.** Groundhog has to install your
deps and run your tests deterministically at an arbitrary old commit. This is the
reason "benchmark any repo" tools don't already exist. Right now you supply the
venv and the test command; repos with heavy native deps, service dependencies, or
drifting lockfiles will fight you.

**Per-repo results are not comparable across repos.** A model scoring 60% on your
code and 45% on someone else's tells you nothing about the two repos' difficulty.
Groundhog is for comparing *models*, holding the repo fixed.

**Tests are a proxy for correctness, not correctness.** A model can make tests
pass in ways the original author would reject.

## License

MIT
