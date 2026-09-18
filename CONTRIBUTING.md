# Contributing to Groundhog

Groundhog is small on purpose: about 1,300 lines, no runtime dependencies, and
four files that each do one thing. You should be able to read the whole thing in
an afternoon and change it with confidence.

## How it fits together

```
mine.py      git log  ->  candidate commits        (pure git, fast, no execution)
validate.py  candidates -> real tasks              (runs the tests twice)
run.py       tasks    ->  attempts                 (agent loop in a worktree)
report.py    attempts ->  table + leaderboard page
models.py    provider-agnostic chat with tools
```

Data moves between stages as JSONL files, so every stage is independently
runnable and independently testable. If you are changing one stage you can ignore
the others.

**The one invariant that matters:** a task is only real if the tests fail without
the original fix and pass with it. Everything else is convenience. If a change
makes that check weaker, it will not be merged, however useful it seems.

## Setup

```bash
git clone https://github.com/abhinavsv3/groundhog
cd groundhog
pip install -e . pytest
python -m pytest tests/ -q
```

The tests build throwaway git repositories on the fly, so there is nothing to
download and nothing to mock. They should run in under ten seconds.

## Ways in, roughly by difficulty

**Add a model provider** (easy). Subclass `Provider` in `models.py` with three
methods: `say`, `reply`, `give_results`. Anything OpenAI-compatible already works
via `GROUNDHOG_OPENAI_BASE_URL`, so this is only for genuinely different wire
formats.

**Add a language** (medium, most wanted). `mine.py` already recognises test files
for Go, Rust, TS and JS. What is missing is validation: a way to install
dependencies and run a subset of tests for that ecosystem. Start with
`source_roots()` in `validate.py` and the `--test-cmd` default.

**Add an agent adapter** (medium, most interesting). Groundhog ships its own
small agent loop, but the more useful question is how *your* setup performs.
An adapter runs an external agent — aider, SWE-agent, Claude Code, opencode —
against a prepared worktree and lets the same scoring apply.

**Extend environment inference** (hard, high impact). `environment.py` handles
Python projects — extras, PEP 735 dependency groups, `requirements*.txt` — and
caches a venv per repo. It does not handle native extensions, service
dependencies or unusual build systems, which still need `--venv` and
`--test-cmd`. Two traps worth knowing: dependency groups are *not* extras
(`pip install '.[tests]'` exits 0 having installed nothing), and the venv cache
key must include the install plan or your improvement silently won't apply.

## What makes a good pull request

- **A test.** Especially for mining and validation heuristics: add a commit shape
  to the synthetic repo in `tests/test_pipeline.py` and assert what should happen
  to it.
- **Evidence it works on a real repo.** "Mined 40 tasks from X, 12 validated" is
  worth more than any description.
- **No new runtime dependencies** unless there is genuinely no alternative. Every
  dependency is another way a benchmark run dies on somebody's laptop.
- **Honesty about limits.** If your change works for pytest but not unittest, say
  so in the docstring. Silent partial correctness is how benchmarks start lying.

## Things that will get a change rejected

- Weakening the fail-to-pass check
- Scoring anything other than the test suite (no judge models, no partial credit)
- Letting the model edit test files
- Reporting a number without saying how it was produced

## A bug worth knowing about before you start

Editable installs shadow the worktree. If a repo uses a `src/` layout and the
package is installed with `pip install -e .` from the original clone, then
`import yourpackage` resolves to the *original, fixed* code no matter what the
model wrote — every test passes and every task looks like a harmless refactor.

`source_roots()` forces the worktree onto `PYTHONPATH` to prevent this. It cost
us 20 tasks on `pallets/click` before we noticed, and it failed *silently*. If
you are adding a language, assume the same class of bug exists there and go
looking for it.
