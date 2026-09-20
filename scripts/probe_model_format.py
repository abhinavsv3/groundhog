#!/usr/bin/env python3
"""Show what a model actually emits, before Groundhog interprets it.

A result record says how many tool calls were parsed. It does not say what the
model sent, so a model that works perfectly well in a format the parser does
not recognise is indistinguishable from a model that did nothing. That
ambiguity is what this script exists to remove.

    python3 scripts/probe_model_format.py openai:mistral:latest

Prints the raw message body alongside what the parser made of it.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from groundhog.models import connect  # noqa: E402
from groundhog.run import SYSTEM, tools_for  # noqa: E402

PROMPT = (
    "Repository: toolbox\n"
    "Tests that must pass: tests/test_text.py\n\n"
    "Current failure:\n```\n"
    "ImportError: cannot import name 'clamp' from 'toolbox.text'\n"
    "```\n\n"
    "Fix the source so these tests pass."
)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    model = sys.argv[1]
    chat = connect(model, SYSTEM)
    chat.say(PROMPT)
    reply = chat.reply(tools_for({"tests/test_text.py"}))

    print(f"model: {model}")
    print(f"parsed tool calls: {len(reply.tool_calls)} "
          f"{[c.name for c in reply.tool_calls]}")
    print(f"recovered from text: "
          f"{sum(1 for c in reply.tool_calls if c.id.startswith('text_'))}")
    print(f"done flag (set when no call is parsed): {reply.done}")
    print("-" * 70)
    print(reply.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
