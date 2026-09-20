# Validation: what became a task, and what did not

310 candidates attempted across 14 repositories in 3 languages.

## Result

| | count | share |
|---|---|---|
| Became tasks | **165** | 53% |
| Rejected: tests passed without the fix | 43 | 30% of rejections |
| Rejected: environment could not run the tests | **99** | **68% of rejections** |
| Harness errors | 3 | — |

The second row is the mining criterion working as designed — refactors and
formatting commits whose tests pass without the fix, which no amount of diff
analysis would separate from real bug fixes.

**The third row is ours.** Two thirds of all rejections are repositories
Groundhog could not build well enough to run, not repositories with nothing to
offer. That is the honest measure of zero-config environment inference, and it
is worse than the five-repo README table implies.

## Per repository

| repo | valid | refactor | env failure | notes |
|---|---|---|---|---|
| werkzeug | 19 | 6 | **0** | |
| click | 19 | 2 | 4 | |
| marshmallow | 18 | 6 | 1 | |
| jinja | 17 | 5 | **0** | |
| httpx | 16 | 7 | 2 | |
| cattrs | 16 | 0 | 9 | |
| rich | 15 | 2 | 8 | slow suite: 638s |
| attrs | 12 | 8 | 3 | |
| starlette | 9 | 0 | 16 | |
| typer | 6 | 1 | **0** | |
| structlog | 6 | 4 | 15 | |
| gotoolbox | 6 | 0 | **0** | Go |
| tenacity | 4 | 2 | 19 | collection error |
| flask | **2** | 0 | **22** | pytest API drift |

Five repositories account for 81 of the 99 environment failures. Four have none
at all.

## Two diagnosed causes

**flask** — `AttributeError: module '_pytest.monkeypatch' has no attribute
'notset'`. The commit's own `conftest.py` needs the pytest that was current when
it was written; Groundhog installs today's. **Historical code is checked out
against present-day dependencies.** Nothing in the design addresses this, and a
repo whose test scaffolding tracks pytest closely will fail wholesale.

**tenacity** — collection error before any test runs. Same shape: the test
module cannot import under the installed dependency set.

## A prediction that did not hold

If dependency drift were the dominant cause, environment failures should
concentrate in older commits. Tested directly:

| year | valid | env failure | env failure rate |
|---|---|---|---|
| 2023 | 5 | 5 | 38% |
| 2024 | 28 | 11 | 23% |
| 2025 | 26 | 28 | 44% |
| 2026 | 106 | 55 | 30% |

**No trend.** The failures are concentrated by *repository*, not by era — flask
fails across its whole range, werkzeug fails nowhere. Drift is a real mechanism
in flask's case and is not the aggregate explanation.

Recorded because it was predicted before it was measured, and was wrong.

## What this means for the study

The task set is drawn from repositories Groundhog happens to handle well, which
is a selection effect on the *repositories*, not on the tasks within them. A
solve rate measured here describes agent behaviour on
`werkzeug`-and-`jinja`-shaped projects, not on Python projects generally.

Environment failures are not distributed evenly, so any comparison between
cohorts drawn from different repository mixes would be confounded by this. The
study sample holds the repository mix fixed across models for exactly that
reason.
