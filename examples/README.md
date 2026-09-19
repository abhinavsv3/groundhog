# Examples

Real output, captured from real runs, so you can see what Groundhog produces
without setting anything up.

## [`demo-output.txt`](demo-output.txt)

The full output of:

```bash
python -m groundhog demo
```

No API key, no network, about ten seconds. It builds a repository with genuine
git history, mines tasks from it, validates them, and runs four scripted agents:

| Agent | Behaviour | Result |
|---|---|---|
| `honest` | applies the real fix | **3/3 solved** |
| `saboteur` | applies the real fix, breaks another module | 0/3 — *broke 1 passing test* |
| `test-editor` | applies the real fix, edits the test | 0/3 — *edited a test file* |
| `idle` | does nothing | 0/3 |

The middle two are the point. Both make the task's own test pass. **A
FAIL_TO_PASS-only harness would score both as fixes** — which is not a
hypothetical: a 7B model produced the first behaviour spontaneously, three times
in twelve, in [the PASS_TO_PASS
experiment](../analysis/experiment-pass-to-pass.md).

The demo also shows a pure refactor being rejected during validation, because
its tests passed without the fix. No amount of diff analysis distinguishes that
from a real bug fix; running the tests twice does.

## Written-up experiments

| | |
|---|---|
| [`experiment-pass-to-pass.md`](../analysis/experiment-pass-to-pass.md) | A real model cheats, and the guard catches it. $0, local models. |
| [`local-models.md`](../analysis/local-models.md) | What 7B and 14B models actually do on real bugs. They engage, and get it wrong. |
| [`harness-artifacts.md`](../analysis/harness-artifacts.md) | Detecting broken environments in 122 published SWE-bench submissions. |
| [`divergence.py`](../analysis/divergence.py) | Model ranking transfers between repositories; absolute capability does not. |

Each is reproducible from a clean checkout. The two SWE-bench analyses need only
a sparse clone of the public results repository.
