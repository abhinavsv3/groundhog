# H2 — collateral damage, three worked examples

Pre-registered hypothesis H2: *a measurable fraction of attempts that satisfy
FAIL_TO_PASS break previously-passing tests, and a FAIL_TO_PASS-only harness
would score those as solves.*

All three cases below come from the main sweep. Each is a real agent run
against a real mined commit, scored by the full harness. Each patch is committed under `study/patches/` so the claim can be
checked without re-running anything.

## The pattern is one thing, three times

Every case is the same mechanic: the agent called `write_file` with only the
function it was asked to add, and the write replaced the module. The new
function works. Everything else in the file is gone.

| task | subject | model | P2P tests broken | tool calls | read the file first? |
|---|---|---|---|---|---|
| `87eb7a4e9b0c` | Add `count_words()` | qwen2.5-coder:7b | **9** | 1 | no |
| `aeadabccfd89` | Add `strip_prefix()` | qwen2.5-coder:7b | **4** | 6 | no |
| `edfeba3c22f6` | Add `chunk()` | qwen2.5-coder:7b | **1** | 6 | yes (2 reads) |

All three: `fail_to_pass_passed = true`, `solved = false`.
A FAIL_TO_PASS-only harness reports 3 more solves than the agent earned —
on a base of 3 genuine solves, that is a doubling of the reported score.

## Case 1 — `87eb7a4e9b0c`, nine functions deleted in one call

[`study/patches/87eb7a4e9b0c.diff`](patches/87eb7a4e9b0c.diff) — the human fix for the same task is alongside it as `.human.diff`.

The whole patch, a 62-line module reduced to 2 lines:

```diff
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

`count_words` is correct. The task's FAIL_TO_PASS test passes.

Tool record: `{"calls": 1, "by_name": {"write_file": 1}, "first_edit_turn": 1,
"recovered_from_text": 1}`. One call. No `list_files`, no `read_file`, no
`run_tests`. The agent never looked at what it was overwriting, and — because
the call arrived as prose rather than as a tool call — it was text-recovered on
the way in.

This is the cleanest statement of why P2P is not optional. The agent did the
task it was given, perfectly, and destroyed the module doing it.

## Case 2 — `aeadabccfd89`, it ran the tests and shipped anyway

[`study/patches/aeadabccfd89.diff`](patches/aeadabccfd89.diff)

```diff
-"""Small text helpers."""
+def strip_prefix(text, prefix):
+    return text[len(prefix):] if text.startswith(prefix) else text
-def clamp(...): ...
-def chunk(...): ...
-def dedupe(...): ...
-def truncate(...): ...
-def parse_bool(...): ...
+def clamp(value, min_value, max_value):
+    return max(min(value, max_value), min_value)
```

Four P2P tests broken. What makes this case worse than case 1 is the tool
record: `{"write_file": 2, "run_tests": 3, "list_files": 1}`. The agent invoked
the test suite three times across nine turns. It had the failures in front of
it. Its repair was to hand-restore `clamp` — one of the five functions it had
deleted — and stop.

Partial recovery is not recovery. The harness scores what is in the tree.

## Case 3 — `edfeba3c22f6`, reading the file was not enough

[`study/patches/edfeba3c22f6.diff`](patches/edfeba3c22f6.diff)

```diff
-"""Small text helpers."""
+from typing import List
-def clamp(value, low, high):
-    """Constrain value to the range [low, high]."""
-    return max(low, min(value, high))
+def chunk(lst: List[int], n: int) -> List[List[int]]:
+    return [lst[i:i+n] for i in range(0, len(lst), n)]
```

One P2P test broken — the module held only `clamp` at this commit, so there was
less to destroy. The interesting part is `read_file: 2`. The agent *did* read
the module before writing it, and still emitted a replacement containing only
the new function. Reading the file does not, on its own, prevent the failure.

It also annotated `chunk` as `List[int] -> List[List[int]]`. The repository's
`chunk` is generic over element type. No test catches that, in either direction.

## What this does and does not establish

Establishes: the failure is real, it is not a scoring artifact, it is
reproducible from saved patches, and it is invisible to a FAIL_TO_PASS-only
harness. Three of six qwen2.5-coder FAIL_TO_PASS successes were of this kind —
half.

Does not establish: a rate. Three cases from 38 attempts of one model on one
repository is an existence proof with a worked mechanism, not an estimate.
The Wilson interval on 3/38 is 3%–21%, which is too wide to characterise
anything, and all three come from a single model family.

Also worth stating plainly: qwen3:8b produced **zero** collateral-damage cases
in 35 attempts, and never emitted a tool call as prose. The failure is not
universal to small local models. It tracks the same axis as the text-recovery
rate — see `study/preregistration.md`, H3b.

## Reproducing

```
python3 -m groundhog run <repos>/toolbox \
    --models openai:qwen2.5-coder:7b --tasks tasks/sample-toolbox.jsonl \
    --out results/main-openai-qwen2.5-coder-7b-toolbox.jsonl \
    --max-turns 14 --timeout 150
```

Sampling is temperature-driven and not seeded; the specific tasks that fail
this way will move between runs. The mechanic reproduces.
