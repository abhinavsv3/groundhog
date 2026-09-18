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

## Measured on five real repos

Every one of these ran with **no configuration** — no venv, no test command, no
flags. Groundhog read the project's own dependency declarations, built a cached
environment, and worked out how to run the tests.

| Repo | Domain | Candidates | Validated |
|---|---|---|---|
| encode/httpx | HTTP client | 27 | 10 / 15 tried |
| pallets/click | CLI framework | 82 | 12 / 15 tried |
| Textualize/rich | terminal rendering | 37 | 9 / 15 tried |
| python-attrs/attrs | class generation | 33 | 8 / 15 tried |
| tiangolo/typer | CLI framework | 7 | 6 / 7 tried |

45 verified tasks. The rejects are mostly refactors and formatting commits whose
tests passed without the fix — exactly what the fail-to-pass check is for.

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
python -m groundhog validate /path/to/repo
python -m groundhog run      /path/to/repo --models anthropic:claude-opus-5,openai:gpt-5.2
python -m groundhog report   --site site/index.html --repo owner/name
```

Models are named `provider:model`. Anything with an OpenAI-compatible API
(OpenRouter, Together, Groq, vLLM, Ollama) works through the `openai` provider with
`GROUNDHOG_OPENAI_BASE_URL` pointed at it.

## Running it for free

Groundhog works against local models through Ollama, so a full benchmark costs
nothing:

```bash
ollama pull qwen2.5-coder:7b
export OPENAI_API_KEY=ollama
export GROUNDHOG_OPENAI_BASE_URL=http://localhost:11434/v1

python -m groundhog run /path/to/repo --models openai:qwen2.5-coder:7b
```

`scripts/local_run.sh` runs several models across several repos in one go.

Two things had to be built for local models to produce honest numbers, and both
are worth knowing if you build something similar:

**Some models emit tool calls as text.** qwen2.5-coder's Ollama template has no
native tool support, so a perfectly good call arrives as a JSON blob in the
message body. Scored naively it looks like total incapacity. Groundhog parses
those back into real tool calls.

**Never trust "I'm done".** Weak models routinely announce success having edited
nothing. Groundhog runs the tests when a model claims to be finished and pushes
back if they still fail. Before that check, a model that wrote zero files and a
model that tried hard scored identically -- we were measuring which model gave
up most politely.

## Status

Early, but working end to end.

- [x] Task mining from git history
- [x] Fail-to-pass validation with worktree isolation
- [x] Model runner with a sandboxed agent loop
- [x] Terminal report and leaderboard page
- [x] Local models via Ollama, for $0
- [x] Automatic environment setup — no venv or test command needed
- [ ] [Languages beyond Python](../../issues?q=is%3Aissue+label%3Alanguage)
- [ ] [External agent adapters](../../issues/4)
- [ ] [Repeats and confidence intervals](../../issues/5)

## Contributing

Groundhog is ~1,300 lines with no runtime dependencies, split into four stages
that each do one thing. [CONTRIBUTING.md](CONTRIBUTING.md) explains how they fit
together and where to start.

- [Good first issues](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- [Help wanted](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)
- [What people use this for](docs/use-cases.md)

The most wanted contributions are **a new language** (mining already recognises
Go, Rust, TS and JS test files — only validation is Python-only) and **automatic
environment setup**, which is what currently stands between this and a five-minute
first run.

## Citing Groundhog

If Groundhog is part of how you produced a result, please cite it. GitHub reads
[`CITATION.cff`](CITATION.cff), so the "Cite this repository" button in the
sidebar will generate BibTeX and APA for you.

```bibtex
@software{sv_groundhog_2026,
  author  = {S V, Abhinav},
  title   = {{Groundhog}: Turning Git History into Coding-Model Benchmarks},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/abhinavsv3/groundhog},
  license = {Apache-2.0}
}
```

**Please also state which version you used and how tasks were produced** — the
repository, the commit range mined, and how many candidates survived validation.
Task sets differ between repositories and between time windows, so a Groundhog
number is not reproducible without them. Reporting `groundhog --version` and the
validated task count is enough.

If you use the repo-transfer analysis in
[`analysis/divergence.py`](analysis/divergence.py), note that the underlying data
is from the [SWE-bench experiments
repository](https://github.com/SWE-bench/experiments) and should be cited
alongside it.

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

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
