# mistral scored 0/38. That number measures Groundhog, not mistral.

Found while checking the sweep output before reporting it. It is the reason
`mistral:latest` is excluded from every capability claim in `REPORT.md`.

## The number that looked like a result

```
mistral:latest    n=38   solved=0   f2p=0   collateral=0   calls=11   errors=0
```

Zero solves, zero harness errors, zero warnings. It reads as a clean
measurement of a weak model.

## The diagnostic that contradicts it

Counting attempts in which the harness parsed *no tool call at all*:

| model | attempts with 0 tool calls | never edited a file | total calls |
|---|---|---|---|
| `mistral:latest` | **34/38 (89%)** | 36/38 | 11 |
| `llama3.1` | 1/38 (2%) | 4/38 | 294 |
| `qwen2.5-coder:7b` | 1/38 (2%) | 16/38 | 133 |
| `qwen3:8b` | 1/38 (2%) | 6/38 | 230 |
| `qwen2.5-coder:14b` | 0/22 (0%) | 9/22 | 121 |

Every other model sits at 0–2%. mistral is at 89%. That is not the shape of a
capability gradient; a weak model still *calls tools* and fails. It is the
shape of an input the harness cannot read.

Also visible in the turn counts: 34 of 38 attempts ended at **exactly turn 3**.
The loop sets `done = not calls` — "the model made no tool call" and "the model
says it is finished" are the same state — so a model emitting no parseable call
is nudged twice and terminated, every time, identically.

## What mistral actually sent

`scripts/probe_model_format.py` replays one turn with the real system prompt
and the real tool schema and prints the message body before the parser touches
it. Captured 2026-09-20:

```
parsed tool calls: 0 []
done flag: True
----------------------------------------------------------------------
 To resolve the ImportError, I need to add the missing `clamp` function in the
 `toolbox/text.py` file. [...]

 Here's the updated toolbox/text.py with the clamp function:

 ```python
 def clamp(value, min_value, max_value):
     return max(min_value, min(value, max_value))
 ...
 ```

 After updating the source code, run the tests again [...]

 `DONE`
```

mistral diagnosed the failure correctly, named the right file, and **wrote a
correct `clamp`**. It emitted the work as a markdown code fence with the path
in prose.

`parse_text_tool_calls()` recovers JSON objects carrying a `name` and
`arguments`. It does not recover a fenced code block plus a filename in a
sentence. So the call was discarded, `done` fired, and the attempt was scored
as a failure — 38 times, silently.

## What this is an instance of

The same bug shape as the editable-install shadowing, the PEP 735 groups, the
stale venv fingerprint and the `tail` exit code before it: **the system
produced a plausible number instead of an error.** Nothing crashed. The
`errors` column read 0. A model that was doing roughly the right thing on every
task was recorded as having done nothing at all on all of them.

Had this sweep been run for a leaderboard rather than read closely, "mistral:
0%" would have been published.

## What was and was not done about it

**Not fixed during the study.** Teaching the parser to read fenced code blocks
would make the harness better and would also invalidate cross-model comparison
unless all 174 attempts were re-run — and tuning a parser until one model's
score improves is its own bias. The harness stays as it was for the whole
sweep.

**Reported instead.** `mistral:latest` is excluded from H3a and H3b and from
every solve-rate comparison. Its row appears in the report carrying this
caveat, not as a data point.

This was the confound the pre-registration declared in advance:

> `recovered_from_text` is a confound for H3a. A model whose calls arrive as
> prose is being measured partly on Groundhog's parser.

mistral is the limiting case — a model measured *entirely* on Groundhog's
parser. The declaration anticipated a bias in a rate. What actually happened
was a total loss of signal that nothing in the output flagged.

## The fix the harness needs

Not a better parser. A refusal to report. A run in which a model produces zero
tool calls across most attempts is not a low score, it is a failed measurement,
and the harness should say so loudly instead of printing `0%`. Filed as an
issue against this repository.

## Reproducing

```
python3 scripts/probe_model_format.py openai:mistral:latest
python3 scripts/probe_model_format.py openai:qwen3:8b      # for contrast
```
