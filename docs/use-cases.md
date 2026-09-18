# What Groundhog is for

Groundhog turns a repository's own commit history into a verified benchmark. That
one capability supports several quite different jobs. They are listed here
roughly by how well the tool currently serves them — the honest version, not the
pitch.

---

## 1. "Can we trust agents on our codebase?" — works today

The question thousands of teams are asking with no way to answer it. A team is
deciding whether to let an agent open pull requests, how much review to require,
or whether to buy seats — and all they have is a leaderboard about Django
internals.

Groundhog answers it directly: mine tasks from your own recent commits, run your
agent, and see what fraction it closes.

**Why a public leaderboard cannot answer this.** Across 130 SWE-bench submissions
and 10 repositories, model *ranking* barely moves between repos (Spearman 0.88) —
but the same model's *pass rate* swings a median of 35 points. "75% on SWE-bench"
tells you almost nothing about what fraction of *your* bugs get fixed. See
[`analysis/divergence.py`](../analysis/divergence.py).

---

## 2. Regression testing your agent setup — works today

You changed a system prompt, upgraded a model, added an MCP server, edited your
`CLAUDE.md`. Did that help or hurt?

Nobody measures this, because there has been nothing to measure against. Pin a
task set, run before and after, compare. This is the use case most likely to be
run repeatedly rather than once.

---

## 3. Harness and prompt research — works, with caveats

Hold the task set fixed, change one thing about the agent, measure the delta.
Tool naming, tool descriptions, turn limits, retrieval strategy, context layout.

Objective scoring and uncontaminated tasks make this a better substrate than most
hand-rolled harnesses. **But the statistics are not there yet:** Groundhog runs
each task once, reports point estimates with no confidence intervals, and 37 tasks
only detects large effects. Detecting something subtle like a naming effect needs
repeats, paired analysis, and 150–300 tasks. See the `research` issues.

---

## 4. Evaluating on private code — works today, undersold

No public benchmark can evaluate code you are not allowed to send anywhere.
Groundhog runs locally, needs no Docker, and with a local model via Ollama
nothing leaves the machine at all. For regulated or proprietary codebases this is
the only option that exists.

---

## 5. Model selection — mostly answered elsewhere

The original pitch, and largely wrong. Ranking transfers between repos, so the
public leaderboard usually picks correctly. Use Groundhog for this only when
models are close enough that 35 points of repo-specific variance could decide it.

We tested our own premise and it did not hold. It is documented rather than
buried.

---

## 6. Training data generation — use something else

Validated fail-to-pass tasks are exactly what SWE-agent training pipelines want,
and an exporter to SWE-bench format would be a small, welcome contribution.

But if generating training data at scale is your goal,
[SWE-smith](https://github.com/SWE-bench/SWE-smith),
[SWE-bench-Live](https://github.com/microsoft/SWE-bench-Live) and
[SWE-Factory](https://github.com/DeepSoftwareAnalytics/swe-factory) are built for
it and better at it. Groundhog optimises for a laptop and a decision, not a
cluster and a dataset.

---

## Where it is weakest

**Environment setup is inferred, not guaranteed.** Zero-config works on the
five repos tested so far. Native extensions, service dependencies and unusual
build systems will still need manual flags.

**Python only, really.** Mining recognises Go, Rust, TS and JS test files;
validation has only been exercised on Python.

**Tests are a proxy for correctness.** A model can make tests pass in ways a
reviewer would reject.
