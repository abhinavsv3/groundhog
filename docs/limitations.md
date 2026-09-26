# Honest limitations

The README keeps the short version. This is the long one, including the bugs
that bit this project and are worth knowing if you build something similar.

## What a solve rate actually measures

Not "the fraction of your bugs an agent can fix." Groundhog's tasks are
historical changes that happen to satisfy its mining criteria: a single commit,
touching both source and tests, 3–200 lines, at most 5 source files, where the
new tests fail without the fix. That systematically excludes bugs fixed without
a regression test, features, refactors, multi-commit work, architectural
change, dependency upgrades and anything operational. A 40% score means *40% of
that slice* — a real and useful slice, and a narrow one. Treat it as a
comparable index across agent configurations, not as an estimate of your bug
backlog.

## Environments are inferred, and inference is not guaranteed

Groundhog reads `pyproject.toml`, PEP 735 dependency groups, extras and
`requirements*.txt`, then builds a cached venv — no flags needed on the five
Python repos in the study. Go, JavaScript and Rust are detected from `go.mod`,
`package.json` and `Cargo.toml`. Repos with native extensions, service
dependencies or unusual build systems still need `--venv`, `--test-cmd` or
`--env-cmd`. Other projects solve this with an LLM that infers build commands;
Groundhog deliberately does not, so that mining and validation need no API key.

On Python 3.10 with no `tomli` installed, `pyproject.toml` cannot be parsed and
extras are missed. The plan says so loudly; `groundhog doctor` checks for it.

## This is isolation, not a security sandbox

The agent can only read and write inside a throwaway git worktree, and edits
to test files are rejected. But building an environment runs that repository's
own `pip install` and test suite **on your machine**, and an external agent
started with `--agent` has a shell. A repository you do not trust can execute
code that way, exactly as it could if you cloned and tested it by hand. Do not
point Groundhog at untrusted code without OS-level isolation of your own.

## Editable installs silently poison results

A package installed `-e` from the original clone shadows the worktree, so a
src-layout repo imports the *fixed* code and every test passes no matter what
the model wrote. Groundhog forces the worktree onto `PYTHONPATH` to prevent
this. It cost us 20 tasks on click before we caught it, and it failed
*silently*. The JavaScript equivalent — a package importing itself by name
through a symlinked `node_modules` — is handled the same way.

## Per-repo results are not comparable across repos

A model scoring 60% here and 45% elsewhere says nothing about the two repos'
relative difficulty. Hold the repo fixed and compare models, or hold the model
fixed and compare agent configurations.

## The task set skews small, local and synchronous

Mining selects single commits of 3–200 lines across at most 5 files, with
tests. Across 26 mined httpx candidates — from an async HTTP library — **none
touched concurrency code at all.** `report --tasks` breaks the score down by
scope, size and concurrency so that skew is visible rather than averaged away.

## Tests are a proxy for correctness, not correctness

An agent can make tests pass in ways the original author would reject in
review. Groundhog checks that `PASS_TO_PASS` tests still pass and that test
files were not edited, which rules out the crudest cheats — not the subtle
ones. The saved patches are there so a human can look.

## Flaky tests look like agent failures

A test that passes 70% of the time passes the fail-to-pass check and then
fails agents at random. `validate --stability N` re-runs the fixed state and
rejects tasks whose outcomes move; `bench` uses 2 by default. It catches
timing flakes, not tests that depend on the date or the network.

## Public repos may be contaminated

The repos benchmarked in the study are popular and open; their commits and
patches may sit in model training data, which inflates scores by an unknown
amount. Mining commits merged after a model's cutoff mitigates this, and
`report --cutoff` splits the score by era. Running Groundhog on a **private**
repository removes the concern almost entirely — an evaluation distribution
nobody has trained on is one of the better reasons to use this on your own
code rather than reading a public leaderboard.

## Local models of 7–14B are for exercising the harness

In the calibration run, qwen2.5-coder at 7B and 14B solved 0 of 10 real httpx
tasks — while editing source on half of them, so they engage and get it wrong
rather than failing to act. Models of this size are useful for catching
harness breakage at $0, not for producing a capability number. See
[analysis/local-models.md](../analysis/local-models.md).

Two things had to be built for them to produce honest numbers at all. Some
models emit tool calls as text (qwen2.5-coder's Ollama template has no native
tool support), so Groundhog parses JSON blobs in the message body back into
calls. And weak models routinely announce success having edited nothing, so
Groundhog runs the tests when a model claims to be finished and pushes back.
Before that check, a model that wrote zero files and a model that tried hard
scored identically.

## Rust is parser-tested only

Detection, target naming and the `cargo test` parser have unit tests against
captured output. Nobody has yet run `validate` on a real crate from this
project's machines. [Issue 31](https://github.com/abhinavsv3/groundhog/issues/31)
stays open until someone does.
