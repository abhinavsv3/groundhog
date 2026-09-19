"""SWE-bench export — interop, and one honest difference."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import export  # noqa: E402

REQUIRED = {"instance_id", "repo", "base_commit", "patch", "test_patch",
            "problem_statement", "FAIL_TO_PASS", "PASS_TO_PASS"}


@pytest.fixture
def repo(tmp_path):
    def sh(*a):
        subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)
    sh("git", "init", "-q")
    sh("git", "config", "user.email", "t@e.com")
    sh("git", "config", "user.name", "T")
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    (tmp_path / "t.py").write_text("def test_f():\n    assert True\n")
    sh("git", "add", "-A"); sh("git", "commit", "-q", "-m", "init")
    (tmp_path / "a.py").write_text("def f():\n    return 2\n")
    (tmp_path / "t.py").write_text("def test_f():\n    assert f() == 2\n")
    sh("git", "add", "-A"); sh("git", "commit", "-q", "-m", "Fix f")
    return tmp_path


def task(repo):
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    parent = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD~1"],
                            capture_output=True, text=True).stdout.strip()
    return {"sha": sha, "parent": parent, "subject": "Fix f",
            "source_files": ["a.py"], "test_files": ["t.py"],
            "fail_to_pass": ["t.py::test_f"], "pass_to_pass": ["t.py::test_other"],
            "date": "2026-01-01T00:00:00Z"}


class TestInstance:
    def test_carries_every_required_field(self, repo):
        assert REQUIRED <= set(export.to_instance(repo, "o/n", task(repo)))

    def test_base_commit_is_the_parent(self, repo):
        t = task(repo)
        assert export.to_instance(repo, "o/n", t)["base_commit"] == t["parent"]

    def test_patch_and_test_patch_are_separate(self, repo):
        i = export.to_instance(repo, "o/n", task(repo))
        assert "a.py" in i["patch"] and "t.py" not in i["patch"]
        assert "t.py" in i["test_patch"] and "a.py" not in i["test_patch"]

    def test_fail_to_pass_is_json_encoded(self, repo):
        """SWE-bench stores these as JSON strings, not lists."""
        i = export.to_instance(repo, "o/n", task(repo))
        assert json.loads(i["FAIL_TO_PASS"]) == ["t.py::test_f"]

    def test_instance_id_is_slash_free(self, repo):
        assert "/" not in export.to_instance(repo, "owner/name", task(repo))["instance_id"]

    def test_problem_statement_admits_it_is_not_an_issue(self, repo):
        """An agent given an exact executable spec is doing a different job
        from one given a vague human bug report. Do not blur that."""
        statement = export.to_instance(repo, "o/n", task(repo))["problem_statement"]
        assert "did not come from a GitHub issue" in statement
        assert "t.py::test_f" in statement
