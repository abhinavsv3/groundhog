# Prior art, and why not just read a leaderboard

## We tested the premise first

Groundhog's original pitch was model selection: the public leaderboard is
about Django internals, so measure on your own repo before choosing. We tested
that premise before making the claim, and it mostly did not hold.

Using SWE-bench's published per-repo results (122 leaderboard submissions
across 7 repositories, after discarding 13 whose denominators are wrong — see
[experiments#484](https://github.com/SWE-bench/experiments/issues/484)), repos
agree strongly on how to rank models: **mean Spearman rho of 0.86**, and in
pairs of models separated by at least 5% overall, the worse model wins on a
given repo only **3.7%** of the time. Pick the top model off a public
leaderboard and you will be right on your repo almost always.

So Groundhog will rarely change *which* model you choose. What it tells you is
something the leaderboards cannot:

**Absolute capability transfers far worse than ranking does.** The median
model swings **36 percentage points** between its best and worst repository.
"Model X is 75% on SWE-bench" tells you little about what share of *your*
tasks it will close.

That is the number you need before pointing an agent at a backlog, and the
only way to get it is to measure on your own code. It is also why the use
case that survived is regression testing: hold the repo fixed, change one
thing about the agent, and see whether the difference is real.

Reproduce the analysis:

```bash
git clone --filter=blob:none --sparse https://github.com/SWE-bench/experiments
python analysis/divergence.py experiments
```

The same data produced a second finding: 13 of 122 submissions have per-repo
pass rates whose tail asymmetry indicates a broken environment rather than a
weak model. [analysis/harness-artifacts.md](../analysis/harness-artifacts.md)
explains the check; it was offered upstream as
[experiments#488](https://github.com/SWE-bench/experiments/issues/488).

## Where Groundhog sits

This is a crowded field and the core construction is not new. Groundhog's
fail-to-pass mining is the same idea as
[SWE-bench](https://github.com/SWE-bench/SWE-bench), and several projects
already automate task generation from arbitrary repos:

- [SWE-smith](https://github.com/SWE-bench/SWE-smith) — turn any repo into a SWE-gym
- [SWE-bench-Live](https://github.com/microsoft/SWE-bench-Live) — continuously updated tasks, LLM-built environments
- [SWE-Factory](https://github.com/DeepSoftwareAnalytics/swe-factory) — automated pipeline, multi-language
- [R2E-Gym](https://github.com/R2E-Gym/R2E-Gym) — procedurally curated environments

Those are research infrastructure aimed at producing **training data** at
scale. They want Docker, conda, and multi-agent environment builders;
SWE-smith says plainly that macOS is not supported.

Groundhog aims at something smaller: a tool an engineer runs on a laptop to
answer a **decision** — did this change to our agent setup help or hurt, and
how much of our own history can it reproduce — in minutes, with no Docker,
and with no API key needed to mine and validate. If you need training data at
scale, use the projects above; they are better at it. `groundhog export`
writes SWE-bench instances so the two are not a dead end for each other.

## The study's repositories

Every one of these ran with **no configuration** — no venv, no test command,
no flags. Groundhog read the project's own dependency declarations, built a
cached environment, and worked out how to run the tests.

| Repo | Domain | Candidates | Validated |
|---|---|---|---|
| encode/httpx | HTTP client | 27 | 10 / 15 tried |
| pallets/click | CLI framework | 82 | 12 / 15 tried |
| Textualize/rich | terminal rendering | 37 | 9 / 15 tried |
| python-attrs/attrs | class generation | 33 | 8 / 15 tried |
| tiangolo/typer | CLI framework | 7 | 6 / 7 tried |

45 verified tasks, after collapsing commits that test the same change — a
revert cycle in click produced three commits for one change, which would
otherwise have tripled its weight in the pass rate and broken the independence
assumption behind every interval reported.

Of the 22 rejections, 13 were commits whose tests passed without the fix —
refactors and formatting, exactly what the fail-to-pass check is for. The
other 9 were **our** failures, not the repos': environments Groundhog could
not build well enough to run the tests. That ratio is a fair measure of how
much of the remaining work is ours. The full rejection breakdown is in
[study/rejections.md](../study/rejections.md).

## Experiments

Written up, reproducible, and including the results that did not flatter the
project:

| | |
|---|---|
| [The study](../study/REPORT.md) | 174 attempts, five models, five repos, three languages, $0. Two hypotheses held, the validity gate failed twice, one model was unmeasurable. |
| [Collateral damage](../study/collateral.md) | Three saved patches where the target test passed and the module was destroyed. |
| [PASS_TO_PASS experiment](../analysis/experiment-pass-to-pass.md) | A 7B model made its target test pass while destroying the library — three times in twelve, unprompted. |
| [Local models](../analysis/local-models.md) | What 7–14B models do on real bugs: they engage, and get it wrong. Every attempt claimed completion without running the tests. |
| [Harness artifacts](../analysis/harness-artifacts.md) | Detecting broken environments in 122 published SWE-bench submissions, via tail asymmetry. |
| [Repo transfer](../analysis/divergence.py) | Ranking transfers between repositories (ρ = 0.86); absolute capability does not (36-point median swing). This falsified the project's original premise. |
| [The mistral artifact](../study/mistral-artifact.md) | A model that wrote correct fixes as markdown fences, scored 0% with no errors, and why the report now refuses that number. |
