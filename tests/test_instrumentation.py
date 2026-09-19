"""Tool-call instrumentation: how an attempt went, not just whether it worked."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import report  # noqa: E402
from groundhog.run import ToolStats  # noqa: E402

KNOWN = {"list_files", "read_file", "write_file", "run_tests"}


class TestToolStats:
    def test_counts_calls_and_names(self):
        stats = ToolStats()
        stats.record("read_file", "contents", 1, KNOWN)
        stats.record("read_file", "contents", 2, KNOWN)
        assert stats.calls == 2 and stats.by_name == {"read_file": 2}

    def test_counts_errors(self):
        stats = ToolStats()
        stats.record("read_file", "error: no such file", 1, KNOWN)
        assert stats.errors == 1

    def test_flags_unknown_tools(self):
        stats = ToolStats()
        stats.record("delete_everything", "error: unknown tool", 1, KNOWN)
        assert stats.unknown_tool == 1

    def test_records_the_turn_of_the_first_edit(self):
        stats = ToolStats()
        stats.record("read_file", "x", 1, KNOWN)
        stats.record("write_file", "wrote a.py", 4, KNOWN)
        stats.record("write_file", "wrote b.py", 6, KNOWN)
        assert stats.first_edit_turn == 4, "first edit only, not the last"

    def test_a_failed_write_is_not_an_edit(self):
        stats = ToolStats()
        stats.record("write_file", "error: test files are read-only", 2, KNOWN)
        assert stats.first_edit_turn is None and stats.errors == 1

    def test_no_edit_leaves_it_unset(self):
        stats = ToolStats()
        stats.record("run_tests", "TESTS FAIL", 1, KNOWN)
        assert stats.first_edit_turn is None


class TestMechanicsTable:
    def attempt(self, model, **stats):
        base = {"calls": 0, "errors": 0, "unknown_tool": 0,
                "recovered_from_text": 0, "first_edit_turn": None}
        base.update(stats)
        return {"model": model, "solved": False, "task_id": "t", "tools": base}

    def test_prints_per_model_rates(self, capsys):
        report.mechanics([
            self.attempt("openai:a", calls=10, errors=2, recovered_from_text=10),
            self.attempt("openai:b", calls=10, errors=0),
        ])
        out = capsys.readouterr().out
        assert "HOW THEY WORKED" in out and "20%" in out and "100%" in out

    def test_silent_without_instrumentation(self, capsys):
        """External agents leave no tool records; an empty table would imply
        they made no calls rather than that we could not see them."""
        report.mechanics([{"model": "m", "solved": False, "task_id": "t", "tools": {}}])
        assert capsys.readouterr().out == ""

    def test_separates_models_that_all_scored_zero(self, capsys):
        """The entire point: signal where solve rate has none."""
        report.mechanics([
            self.attempt("openai:careful", calls=20, errors=0, first_edit_turn=2),
            self.attempt("openai:flailing", calls=20, errors=12, first_edit_turn=9),
        ])
        out = capsys.readouterr().out
        assert "careful" in out and "flailing" in out and "60%" in out
