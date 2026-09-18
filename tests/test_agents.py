"""External agent adapter, and the guards that make it safe to trust."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import agents, validate  # noqa: E402


class TestRender:
    def test_fills_placeholders(self):
        out = agents.render("aider --message-file {prompt_file}", prompt_file="/tmp/p.md")
        assert "/tmp/p.md" in out

    def test_quotes_every_substitution(self):
        """Commit subjects reach a shell; nothing is interpolated unquoted."""
        out = agents.render("run {prompt}", prompt="hi; rm -rf /")
        assert "; rm -rf /" not in out.replace("'hi; rm -rf /'", "")
        assert out.startswith("run '")

    def test_leaves_unknown_placeholders_alone(self):
        assert "{mystery}" in agents.render("x {mystery}", prompt_file="p")


class TestPrompt:
    def test_names_the_tests_that_must_pass(self):
        task = {"fail_to_pass": ["tests/t.py::test_a"], "test_files": ["tests/t.py"]}
        prompt = agents.build_prompt(task, "AssertionError", "pytest -q")
        assert "tests/t.py::test_a" in prompt
        assert "AssertionError" in prompt
        assert "pytest -q" in prompt

    def test_forbids_editing_tests(self):
        prompt = agents.build_prompt({"test_files": ["tests/t.py"]}, "boom", "pytest")
        assert "Do not modify any test file" in prompt

    def test_falls_back_to_test_files(self):
        prompt = agents.build_prompt({"test_files": ["tests/t.py"]}, "boom", "pytest")
        assert "tests/t.py" in prompt


class TestExternalRun:
    def test_runs_in_the_worktree(self, tmp_path):
        result = agents.run_external(tmp_path, "pwd", "prompt", "pytest", timeout=30)
        assert result.exit_code == 0
        assert str(tmp_path.resolve()) in result.output

    def test_agent_can_read_the_prompt_file(self, tmp_path):
        result = agents.run_external(tmp_path, "cat {prompt_file}", "THE-TASK", "pytest", timeout=30)
        assert "THE-TASK" in result.output

    def test_cleans_up_the_prompt_file(self, tmp_path):
        agents.run_external(tmp_path, "true", "p", "pytest", timeout=30)
        assert not (tmp_path / ".groundhog-task.md").exists()

    def test_timeout_is_recorded_not_raised(self, tmp_path):
        result = agents.run_external(tmp_path, "sleep 5", "p", "pytest", timeout=1)
        assert result.timed_out and "timed out" in result.error

    def test_failure_exit_code_is_captured(self, tmp_path):
        assert agents.run_external(tmp_path, "exit 3", "p", "pytest", timeout=30).exit_code == 3

    def test_exports_env_for_agents_that_look_there(self, tmp_path):
        result = agents.run_external(tmp_path, "echo $GROUNDHOG_TEST_CMD", "p", "pytest -q", timeout=30)
        assert "pytest -q" in result.output


class TestPytestParsing:
    """FAIL_TO_PASS / PASS_TO_PASS depend entirely on reading pytest correctly."""

    SAMPLE = """
tests/test_a.py::test_one PASSED                                         [ 25%]
tests/test_a.py::test_two FAILED                                         [ 50%]
tests/test_b.py::test_three SKIPPED                                      [ 75%]
tests/test_b.py::test_four[param-1] PASSED                               [100%]
"""

    def test_reads_each_outcome(self):
        out = validate.test_outcomes(self.SAMPLE)
        assert out["tests/test_a.py::test_one"] == "PASSED"
        assert out["tests/test_a.py::test_two"] == "FAILED"

    def test_handles_parametrised_tests(self):
        assert "tests/test_b.py::test_four[param-1]" in validate.test_outcomes(self.SAMPLE)

    def test_passing_excludes_failures_and_skips(self):
        assert validate.passing(self.SAMPLE) == {
            "tests/test_a.py::test_one",
            "tests/test_b.py::test_four[param-1]",
        }

    def test_empty_output(self):
        assert validate.passing("") == set()
