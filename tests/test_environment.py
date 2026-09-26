"""Environment detection -- mostly regression tests for bugs that failed silently."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import environment as env  # noqa: E402


def write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class TestExtras:
    def test_picks_narrowest_test_extra(self):
        pyproject = {"project": {"optional-dependencies": {"dev": ["ruff"], "tests": ["pytest"]}}}
        assert env.test_extras(pyproject) == ["tests"]

    def test_falls_back_to_dev(self):
        pyproject = {"project": {"optional-dependencies": {"dev": ["pytest"], "docs": ["sphinx"]}}}
        assert env.test_extras(pyproject) == ["dev"]

    def test_ignores_unrelated_extras(self):
        assert env.test_extras({"project": {"optional-dependencies": {"docs": ["sphinx"]}}}) == []

    def test_dependency_groups_are_not_extras(self):
        """PEP 735 groups cannot be installed with .[name]; pip exits 0 and
        installs nothing. This is the bug that silently broke attrs."""
        assert env.test_extras({"dependency-groups": {"tests": ["hypothesis"]}}) == []


class TestDependencyGroups:
    def test_resolves_a_group(self):
        pyproject = {"dependency-groups": {"tests": ["hypothesis", "pympler"]}}
        assert env.resolve_groups(pyproject) == ["hypothesis", "pympler"]

    def test_follows_include_group(self):
        pyproject = {
            "dependency-groups": {
                "base": ["hypothesis"],
                "tests": [{"include-group": "base"}, "pympler"],
            }
        }
        assert env.resolve_groups(pyproject) == ["hypothesis", "pympler"]

    def test_survives_a_cycle(self):
        pyproject = {
            "dependency-groups": {
                "a": [{"include-group": "b"}, "x"],
                "tests": [{"include-group": "a"}],
                "b": [{"include-group": "a"}, "y"],
            }
        }
        assert set(env.resolve_groups(pyproject)) == {"x", "y"}

    def test_prefers_tests_over_dev(self):
        pyproject = {"dependency-groups": {"dev": ["ruff", "tox"], "tests": ["pytest"]}}
        assert env.resolve_groups(pyproject) == ["pytest"]

    def test_no_groups(self):
        assert env.resolve_groups({}) == []


class TestDetection:
    def test_finds_requirements_files(self, tmp_path):
        write(tmp_path, "requirements.txt", "pytest\n")
        write(tmp_path, "requirements/tests.txt", "hypothesis\n")
        found = env.find_requirement_files(tmp_path)
        assert "requirements.txt" in found and "requirements/tests.txt" in found

    def test_plan_for_a_pyproject_repo(self, tmp_path):
        write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n\n[dependency-groups]\ntests = ["hypothesis"]\n')
        plan = env.detect(tmp_path)
        assert plan.language == "python"
        assert any("-e ." in c for c in plan.install)
        assert any("hypothesis" in c for c in plan.install)

    def test_quotes_requirements_with_markers(self, tmp_path):
        write(
            tmp_path,
            "pyproject.toml",
            '[project]\nname = "x"\n\n[dependency-groups]\ntests = [\'cloudpickle; python_version >= "3.9"\']\n',
        )
        plan = env.detect(tmp_path)
        joined = " ".join(plan.install)
        assert "'cloudpickle; python_version >= \"3.9\"'" in joined

    def test_explains_itself_when_there_is_no_python(self, tmp_path):
        write(tmp_path, "main.go", "package main\n")
        with pytest.raises(env.DetectionFailed) as exc:
            env.detect(tmp_path)
        assert "pyproject.toml" in str(exc.value)
        assert "language" in str(exc.value)


class TestFingerprint:
    def make(self, tmp_path, deps: str):
        write(tmp_path, "pyproject.toml", f'[project]\nname = "x"\n\n[dependency-groups]\ntests = [{deps}]\n')
        return env.detect(tmp_path)

    def test_changes_when_dependencies_change(self, tmp_path):
        a = self.make(tmp_path, '"hypothesis"').fingerprint(tmp_path)
        b = self.make(tmp_path, '"hypothesis", "pympler"').fingerprint(tmp_path)
        assert a != b

    def test_changes_when_the_plan_changes(self, tmp_path):
        """Hashing only the dep files meant a detection fix left users on a
        stale, wrongly-built environment with no signal anything was wrong."""
        plan = self.make(tmp_path, '"hypothesis"')
        before = plan.fingerprint(tmp_path)
        plan.install = [*plan.install, "pip install extra-thing"]
        assert plan.fingerprint(tmp_path) != before

    def test_stable_for_identical_input(self, tmp_path):
        plan = self.make(tmp_path, '"hypothesis"')
        assert plan.fingerprint(tmp_path) == plan.fingerprint(tmp_path)


class TestNoTomllib:
    def test_missing_parser_is_said_out_loud(self, tmp_path, monkeypatch):
        """On 3.10 without tomli, extras are missed; the plan must say so."""
        from groundhog import environment
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\n[project.optional-dependencies]\ntest = ["pytest", "hypothesis"]\n'
        )
        monkeypatch.setattr(environment, "_toml_parser", lambda: None)
        plan = environment.detect(tmp_path)
        assert any("NOT parsed" in n for n in plan.notes)
        assert not any("[test]" in cmd for cmd in plan.install)

    def test_with_a_parser_extras_are_found(self, tmp_path):
        from groundhog import environment
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\n[project.optional-dependencies]\ntest = ["pytest"]\n'
        )
        plan = environment.detect(tmp_path)
        assert any(".[test]" in cmd for cmd in plan.install)
        assert not any("NOT parsed" in n for n in plan.notes)
