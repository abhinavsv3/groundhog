#!/usr/bin/env python3
"""groundhog <command> -- mine, validate, run, report."""

from __future__ import annotations

import sys

USAGE = """groundhog -- turn a repo's git history into a coding-model benchmark

  groundhog mine     <repo>   find commits that changed source and tests together
  groundhog validate <repo>   keep only the ones that fail without the fix
  groundhog run      <repo>   race models against the validated tasks
  groundhog report            print the table and build the leaderboard page
  groundhog compare  <a> <b>  paired comparison of two runs, for CI and ablations

Each command takes --help.
"""


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    command, sys.argv = sys.argv[1], [f"groundhog {sys.argv[1]}", *sys.argv[2:]]
    if command == "mine":
        from .mine import main as run_it
    elif command == "validate":
        from .validate import main as run_it
    elif command == "run":
        from .run import main as run_it
    elif command == "report":
        from .report import main as run_it
    elif command == "compare":
        from .compare import main as run_it
    else:
        print(f"unknown command {command!r}\n\n{USAGE}", file=sys.stderr)
        return 2
    return run_it()


if __name__ == "__main__":
    raise SystemExit(main())
