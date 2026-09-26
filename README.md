# Groundhog

[![tests](https://github.com/abhinavsv3/groundhog/actions/workflows/test.yml/badge.svg)](https://github.com/abhinavsv3/groundhog/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/groundhog-eval?label=groundhog-eval)](https://pypi.org/project/groundhog-eval/)
[![study](https://img.shields.io/badge/study-174%20attempts%2C%20%240-orange)](https://scalingthoughts.com/groundhog/study.html)
[![license](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Your git history is an eval dataset. Most harnesses score it wrong.**

Groundhog mines coding-agent tasks from a repository's own commits, verifies
each one by running its tests, and races agents against them on *your*
codebase. It scores an attempt as solved only if the target tests pass **and**
every previously-passing test still passes **and** no test file was edited.

That last clause is not pedantry. Here is a real attempt from
[the study](study/REPORT.md): the task was "add `count_words()`", the agent
was qwen2.5-coder:7b, and the target test passed.

```diff
--- a/toolbox/text.py
+++ b/toolbox/text.py
@@ -1,62 +1,2 @@
-"""Small text helpers."""
-
-def clamp(value, low, high): ...
-def chunk(items, size): ...
-def dedupe(items): ...
-def truncate(value, length, suffix="..."): ...
-def parse_bool(value): ...
-def strip_prefix(value, prefix): ...
-def flatten(nested): ...
-def slugify(value): ...
-def safe_divide(numerator, denominator, default=0.0): ...
+def count_words(text):
+    return len(text.split())
```

One tool call, no read first. `count_words` is correct. Nine other functions
are gone. A harness that checks only the target test calls this a solve.
Across 174 attempts by five local models, that is what it looks like per model:

```
MODEL                       F2P-ONLY    TRUE  OVERSTATED  COLLATERAL
openai:qwen3:8b                17/38   17/38           0           0
openai:qwen2.5-coder:14b        9/22    8/22       +5pts   1 broke 1
openai:qwen2.5-coder:7b         6/38    3/38       +8pts  3 broke 14
openai:llama3.1:latest          2/38    1/38       +3pts   1 broke 1
```

Every patch behind those numbers is in [`study/patches/`](study/patches/),
and `groundhog show 87eb7a4e9b0c` prints the one above.

## See it in ten seconds

No API key, no network, nothing installed but [uv](https://docs.astral.sh/uv/):

```bash
uvx groundhog-eval demo
```

That builds a repository with real history, mines tasks from it, rejects a
refactor because its tests passed without the fix, and runs four scripted
agents. One applies the real fix. One applies the fix and breaks another
module. One applies the fix and edits the test. One does nothing. Only the
first scores. Captured output: [examples/demo-output.txt](examples/demo-output.txt).

## Run it on your own code

```bash
uv tool install groundhog-eval          # or: pip install groundhog-eval

groundhog bench pallets/click --agent claude-code      # the agent you use
groundhog bench ~/src/myrepo --models ollama:qwen3:8b  # a local model, $0
groundhog doctor                                       # what this machine can run
```

`bench` mines, validates, runs and reports in one go, under
`.groundhog/<repo>/`. The second invocation skips straight to the race. A URL
works anywhere a path does; the clone is cached.

What you get back, here from the study's own results file:

```
MODEL                       SOLVED   RATE         95% CI      COST   PER SOLVE   MEDIAN
---------------------------------------------------------------------------------------
openai:qwen3:8b              17/38    45%      30% – 60%         -           -     412s
openai:qwen2.5-coder:14b      8/22    36%      20% – 57%         -           -     111s
openai:qwen2.5-coder:7b       3/38     8%       3% – 21%         -           -      45s
openai:llama3.1:latest        1/38     3%       0% – 13%         -           -     111s
openai:mistral:latest         0/38          not measured         —           —     168s

  The top two intervals overlap — this ordering is not a result. Separating them
  would need roughly 538 tasks (you have 38), or use `groundhog compare`
  for a paired test, which needs far fewer.
```

Every rate carries a Wilson interval, because on a task set this size a gap
of ten points usually is not one. The last row is a model whose output the
harness could not read; it gets a refusal, not a 0%. You also get a Markdown copy for the pull request, a shields.io badge for the README,
a standalone HTML leaderboard, every attempt's diff, and a per-task grid.
Then `groundhog show <task>` for any attempt you want to understand, and
`groundhog compare before.jsonl after.jsonl` for whether a change to your
agent setup helped or hurt, with a paired significance test rather than two
percentages subtracted.

### Agents and models

`--agent` runs the coding agent you actually use, in a throwaway worktree,
and grades what it left behind:

| preset | verified | preset | verified |
|---|---|---|---|
| `claude-code` | yes | `gemini` | flags only |
| `opencode` | yes | `cursor` | flags only |
| `aider` | flags only | `copilot` | flags only |
| `codex` | flags only | `goose`, `amp` | flags only |

"Flags only" means the agent's documented non-interactive flags, not a run we
have watched succeed. `groundhog run --list-agents` prints the exact commands;
`--agent-cmd` takes anything else. If you verify a preset, please flip the flag.

`--models` runs Groundhog's own small tool-calling loop against any model:

```
anthropic:claude-sonnet-5     ollama:qwen3:8b        openrouter:anthropic/claude-sonnet-5
openai:gpt-5.2                vllm:my-model          groq:llama-3.3-70b-versatile
deepseek:deepseek-chat        gemini:gemini-2.5-pro  together: xai: mistral: fireworks: cerebras:
```

Local hosts need no key and cost $0. Hosted ones read the usual environment
variable and say where to get one if it is missing. `groundhog run --list-providers`.

### Languages

| language | detected by | runner | status |
|---|---|---|---|
| Python | `pyproject.toml`, `setup.py`, `requirements*.txt` | pytest | validated on five real repos |
| Go | `go.mod` | `go test -json` | validated |
| JavaScript, TypeScript | `package.json` | vitest, jest | validated |
| Rust | `Cargo.toml` | `cargo test` | parser tested; [needs a real crate](../../issues/31) |

No Docker. Environments are inferred from the project's own dependency
declarations and cached. Anything else: `--test-cmd` and `--env-cmd`.

## How a task is built

Take a commit that touched both source and tests. Check out its parent. Copy
in the new tests but none of the new source. Run the tests twice:

```
parent + new tests             must FAIL    (the task is solvable)
parent + new tests + real fix  must PASS    (the task is fair)
```

A candidate becomes a task only if both hold. That single check separates a
bug fix from a refactor, and no amount of diff analysis can. Optionally the
fixed state is run again to reject flaky tests (`--stability`). Tasks that
test the same change (a revert cycle, a re-apply) are collapsed, so a
confidence interval's independence assumption holds.

The agent then sees the repo at the parent commit and the failing test
output. Test files are read-only. Scoring is
FAIL_TO_PASS and PASS_TO_PASS, the same shape SWE-bench uses, and
`groundhog export` writes SWE-bench instances.

## In CI

```yaml
- uses: abhinavsv3/groundhog@v1
  with:
    tasks: tasks/pinned.jsonl
    baseline: results/baseline.jsonl
    agent: claude-code
  env:
    ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

Runs when your agent config changes, writes the table to the job summary,
fails on a significant regression. [docs/ci.md](docs/ci.md) covers pinning a
task set, why the test is paired, and how to keep the cost sane.

## What the study found

[`study/REPORT.md`](study/REPORT.md) is a pre-registered study of 174
attempts by five local models on five repositories in three languages, on one
laptop, for $0. It reports two things that worked and two that did not:

- **FAIL_TO_PASS-only scoring overstates.** The table above. Every patch is
  saved.
- **Models differ in how they work, not just how often they succeed.** Tool
  call counts, error rates and time-to-first-edit separate models that all
  scored zero.
- **The validity gate failed, twice.** A cripple test (same model, one turn
  instead of fourteen) did not score worse, because the un-crippled score was
  8% and there was no headroom. Reported as a failure, not buried.
- **One model was unmeasurable.** mistral wrote plausible fixes as markdown
  fences instead of tool calls. Groundhog refuses to print a percentage for a
  run like that rather than publishing a clean-looking 0%.

Live page: [scalingthoughts.com/groundhog/study.html](https://scalingthoughts.com/groundhog/study.html).

## What it cannot tell you

A solve rate is the share of *this slice* an agent can close: single commits
that changed source and tests, 3 to 200 lines, at most five files, with tests
that fail without the fix. That excludes features, refactors, multi-commit
work and every bug fixed without a regression test. Read it as a comparable
index across agent configurations, not as a forecast of your backlog.

Public repos may be in training data; a private repo is the cleanest
evaluation distribution there is. Tests are a proxy for correctness, and an
agent can pass them in ways a reviewer would reject. Building an environment
runs the repository's own install and test suite on your machine, so do not
point this at code you do not trust. The full list, including the bugs that
bit us, is in [docs/limitations.md](docs/limitations.md).

## Why not read a leaderboard?

We tested that premise and it mostly held: across 122 SWE-bench submissions,
repos agree on how to *rank* models (Spearman 0.86). What does not transfer
is the *number*: the median model swings 36 points between its best and
worst repository. "75% on SWE-bench" says little about your repo.
[docs/prior-art.md](docs/prior-art.md) has the analysis, the reproduction
command, and where Groundhog sits next to SWE-smith, SWE-bench-Live and
SWE-Factory.

## Contributing

About 3,500 lines of stdlib Python in stages that each do one thing.
[CONTRIBUTING.md](CONTRIBUTING.md) explains how they fit. Most wanted:
[verify an agent preset](../../issues/24),
[validate a Rust crate](../../issues/31),
[unittest-only Python repos](../../issues/37).

- [Good first issues](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- [Help wanted](../../issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)
- [What people use this for](docs/use-cases.md) · [Research use](docs/research.md) · [CI](docs/ci.md)

## Citing

GitHub's "Cite this repository" button reads [`CITATION.cff`](CITATION.cff).
Please also report `groundhog --version`, the repository, the commit range
mined, and the task-set hash that `validate` prints. Task sets differ between
repositories and time windows, so a number without them is not reproducible.

```bibtex
@software{sv_groundhog_2026,
  author  = {S V, Abhinav},
  title   = {{Groundhog}: Replaying Git History as a Regression Test for Coding Agents},
  year    = {2026},
  version = {0.2.0},
  url     = {https://github.com/abhinavsv3/groundhog},
  license = {Apache-2.0}
}
```

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
