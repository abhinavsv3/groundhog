"""Manifest hashing, environment wrappers, and the contamination split."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import report, validate  # noqa: E402


class TestEnvWrapper:
    def test_no_wrapper_is_a_passthrough(self):
        assert validate.wrap("pytest -q", None) == "pytest -q"
        assert validate.wrap("pytest -q", "") == "pytest -q"

    def test_substitutes_and_quotes(self):
        out = validate.wrap("pytest -q", "conda run -n env {cmd}")
        assert out.startswith("conda run -n env ") and "pytest -q" in out

    def test_appends_when_no_placeholder(self):
        assert validate.wrap("pytest", "nix develop -c") == "nix develop -c pytest"

    def test_quoting_survives_a_hostile_command(self):
        out = validate.wrap("pytest; rm -rf /", "conda run {cmd}")
        assert "'pytest; rm -rf /'" in out


class TestManifest:
    @pytest.fixture
    def repo(self, tmp_path):
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
        for key, value in (("user.email", "t@e.com"), ("user.name", "T")):
            subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
        (tmp_path / "a.txt").write_text("x")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "x"], check=True, capture_output=True)
        return tmp_path

    def cfg(self):
        return argparse.Namespace(since="1 year ago", p2p_scope="file", keep_duplicates=False)

    def tasks(self, shas):
        return [{"sha": s, "date": f"2026-01-0{i + 1}T00:00:00Z"} for i, s in enumerate(shas)]

    def test_hash_is_stable(self, repo):
        a = validate.manifest(repo, self.tasks(["a", "b"]), self.cfg())
        b = validate.manifest(repo, self.tasks(["a", "b"]), self.cfg())
        assert a["hash"] == b["hash"]

    def test_hash_ignores_ordering(self, repo):
        a = validate.manifest(repo, self.tasks(["a", "b"]), self.cfg())
        b = validate.manifest(repo, self.tasks(["b", "a"]), self.cfg())
        assert a["hash"] == b["hash"], "task order must not change the identity"

    def test_hash_changes_with_membership(self, repo):
        a = validate.manifest(repo, self.tasks(["a", "b"]), self.cfg())
        b = validate.manifest(repo, self.tasks(["a", "b", "c"]), self.cfg())
        assert a["hash"] != b["hash"]

    def test_records_the_commit_range(self, repo):
        stamp = validate.manifest(repo, self.tasks(["a", "b"]), self.cfg())
        assert stamp["commit_range"] == ["2026-01-01", "2026-01-02"]
        assert stamp["task_count"] == 2


class TestCutoffLookup:
    def test_matches_a_known_family(self):
        assert report.cutoff_for("openai:qwen2.5-coder:7b", {}) == report.CUTOFFS["qwen2.5-coder"]

    def test_override_wins(self):
        assert report.cutoff_for("openai:qwen2.5-coder:7b", {"qwen2.5-coder": "2030-01-01"}) == "2030-01-01"

    def test_unknown_model_returns_none(self):
        assert report.cutoff_for("openai:some-private-model", {}) is None


class TestContaminationSplit:
    def tasks(self, dates):
        return {f"t{i}": {"sha": f"t{i}", "date": f"{d}T00:00:00Z"}
                for i, d in enumerate(dates)}

    def results(self, outcomes, model="openai:qwen2.5-coder:7b"):
        return [{"task_id": f"t{i}", "model": model, "solved": s}
                for i, s in enumerate(outcomes)]

    def test_splits_on_the_cutoff(self, capsys):
        report.contamination(
            self.results([True, True, False, False]),
            self.tasks(["2023-01-01", "2023-06-01", "2026-01-01", "2026-06-01"]),
            {},
        )
        out = capsys.readouterr().out
        assert "pre-cutoff" in out and "post-cutoff" in out

    def test_says_so_when_the_cutoff_is_unknown(self, capsys):
        report.contamination(
            self.results([True], model="openai:mystery-model"),
            self.tasks(["2023-01-01"]),
            {},
        )
        assert "No cutoff known" in capsys.readouterr().out

    def test_states_the_confound(self, capsys):
        """Era is not purely contamination, and the report must say so."""
        report.contamination(
            self.results([True, False]),
            self.tasks(["2023-01-01", "2026-01-01"]),
            {},
        )
        assert "not proof" in capsys.readouterr().out

    def test_silent_without_dates(self, capsys):
        report.contamination(self.results([True]), {"t0": {"sha": "t0"}}, {})
        assert capsys.readouterr().out == ""
