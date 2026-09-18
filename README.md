# Groundhog

**Your git history is an eval dataset. You are probably not using it.**

Every commit that changes source *and* tests is a verified problem/solution pair:
someone wrote a failing test, wrote the fix, and CI proved it worked. Your repo has
hundreds of them, produced for free as a byproduct of your team doing its job.

Groundhog mines them, verifies them, and races coding models against them — on
*your* codebase, in *your* language, with *your* conventions.

```
$ groundhog mine  ~/src/httpx
27 candidates from 171 commits                                             6s

$ groundhog validate ~/src/httpx --venv .venv
8/12 became real tasks                                                    17s

$ groundhog run ~/src/httpx --models anthropic:claude-opus-5,openai:gpt-5.2
$ groundhog report --site site/index.html

MODEL                    SOLVED    RATE      COST   PER SOLVE   MEDIAN
----------------------------------------------------------------------
anthropic:claude-opus-5     6/8     75%     $1.84       $0.31      94s
openai:gpt-5.2              5/8     63%     $0.42       $0.08      61s
```

## Why not just read a leaderboard?

We tested that premise before making the claim, and it mostly did not hold.

Using SWE-bench's published per-repo results (130 leaderboard submissions across
10 repositories), repos agree strongly on how to rank models: **mean Spearman
rho of 0.88**, and in pairs of models separated by at least 5% overall, the worse
model wins on a given repo only **3.3%** of the time. Pick the top model off a
public leaderboard and you will be right on your repo almost always.

So Groundhog will rarely change *which* model you choose. What it tells you is
something the leaderboards cannot:

**Absolute capability does not transfer at all.** The median model swings **35
percentage points** between its best and worst repository -- the same agent
solving 76% of scikit-learn tasks solves 36% of sphinx tasks. "Model X is 75% on
SWE-bench" predicts almost nothing about what fraction of *your* bugs it will fix.

That is the number you need before pointing an agent at a backlog, and the only
way to get it is to measure on your own code.

Reproduce the analysis yourself:

```bash
git clone --filter=blob:none --sparse https://github.com/SWE-bench/experiments
python analysis/divergence.py experiments
```

## How a task is built

Take a commit that touched both source and tests. Check out its parent. Copy in the
new tests but none of the new source. Then run the tests twice:

```
parent + new tests             must FAIL    (the task is solvable)
parent + new tests + real fix  must PASS    (the task is fair)
```

A candidate becomes a task only if both hold. That single check is what separates a
genuine bug fix from a refactor, and no amount of diff analysis can do it. On the
runs below it threw out a `ruff` formatting commit and two pure refactors, because
their tests passed without the fix.

The model then sees the repo at the parent commit and the failing test output, and
has to write the source change itself. Test files are read-only — editing the test
is failing the task.

## Measured on three real repos

| Repo | Domain | Commits scanned | Candidates | Validated tasks |
|---|---|---|---|---|
| encode/httpx | HTTP client | 171 | 27 | 8 / 12 tried |
| pallets/click | CLI framework | 400 | 82 | 17 / 20 tried |
| Textualize/rich | terminal rendering | 400 | 37 | 12 / 20 tried |

Clone to verified tasks takes under a minute per repo once the environment exists.

## Install

```bash
git clone https://github.com/abhinavsv3/groundhog
cd groundhog && pip install -e .
```

No runtime dependencies. Everything is stdlib, because every dependency is another
way a benchmark run dies on somebody's laptop.

## Usage

```bash
python -m groundhog mine     /path/to/repo --since "6 months ago"
python -m groundhog validate /path/to/repo --venv /path/to/venv
python -m groundhog run      /path/to/repo --models anthropic:claude-opus-5,openai:gpt-5.2
python -m groundhog report   --site site/index.html --repo owner/name
```

Models are named `provider:model`. Anything with an OpenAI-compatible API
(OpenRouter, Together, Groq, vLLM, Ollama) works through the `openai` provider with
`GROUNDHOG_OPENAI_BASE_URL` pointed at it.

## Prior art, and where this sits

This is a crowded field and the core construction is not new. Groundhog's
fail-to-pass mining is the same idea as
[SWE-bench](https://github.com/SWE-bench/SWE-bench), and several projects already
automate task generation from arbitrary repos:

- [SWE-smith](https://github.com/SWE-bench/SWE-smith) — turn any repo into a SWE-gym
- [SWE-bench-Live](https://github.com/microsoft/SWE-bench-Live) — continuously updated tasks, LLM-built environments
- [SWE-Factory](https://github.com/DeepSoftwareAnalytics/swe-factory) — automated pipeline, multi-language
- [R2E-Gym](https://github.com/R2E-Gym/R2E-Gym) — procedurally curated environments

Those are research infrastructure aimed at producing **training data** at scale.
They want Docker, conda, and multi-agent environment builders; SWE-smith says
plainly that macOS is not supported.

Groundhog aims at something smaller: a tool an engineer runs on a laptop to answer
a **decision** — which model to point at this repo — in minutes, with no Docker.
If you need training data at scale, use the projects above; they are better at it.

## Honest limitations

**Environments are the hard part.** Groundhog must install your deps and run your
tests at an arbitrary old commit. Right now you supply the venv and test command.
Repos with native extensions, service dependencies, or drifting lockfiles will fight
you. The projects above solved this with an LLM that infers build commands;
Groundhog has not.

**Editable installs silently poison results.** A package installed `-e` from the
original clone shadows the worktree, so a src-layout repo imports the *fixed* code
and every test passes no matter what the model wrote. Groundhog forces the
worktree onto `PYTHONPATH` to prevent this. It cost us 20 tasks on click before we
caught it, and it failed *silently* — worth knowing if you build something similar.

**Per-repo results are not comparable across repos.** A model scoring 60% here and
45% elsewhere says nothing about the two repos' relative difficulty. Hold the repo
fixed and compare models.

**Tests are a proxy for correctness, not correctness.** A model can make tests pass
in ways the original author would reject in review.

**Python only, for now.** The mining patterns recognise Go, Rust, TS and JS test
files, but validation has only been exercised on Python.

## License

MIT
