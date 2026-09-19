"""The demo is the project's main claim, executable. It must not rot."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import demo  # noqa: E402


class TestDemoRepo:
    @pytest.fixture(scope="class")
    def repo(self, tmp_path_factory):
        root = tmp_path_factory.mktemp("demo") / "demolib"
        demo.build_repo(root)
        return root

    def log(self, repo):
        return subprocess.run(["git", "-C", str(repo), "log", "--format=%s"],
                              capture_output=True, text=True).stdout

    def test_has_feature_commits(self, repo):
        subjects = self.log(repo)
        for name, *_ in demo.FEATURES:
            assert f"Add {name}()" in subjects

    def test_includes_a_refactor_for_validation_to_reject(self, repo):
        assert "Tidy clamp internals" in self.log(repo)

    def test_has_a_second_module_so_cross_module_damage_is_possible(self, repo):
        assert (repo / "demolib" / "numbers.py").exists()
        assert (repo / "tests" / "test_numbers.py").exists()

    def test_declares_its_own_dependencies(self, repo):
        """The demo claims zero-config setup; that needs a real manifest."""
        assert "dependency-groups" in (repo / "pyproject.toml").read_text()


class TestAgents:
    def test_one_honest_agent_and_several_cheats(self):
        names = [name for name, *_ in demo.AGENTS]
        assert "honest" in names
        assert {"saboteur", "test-editor", "idle"} <= set(names)

    def test_every_agent_command_is_templated_safely(self):
        for name, _, _, template in demo.AGENTS:
            rendered = template.format(sha="abc123", source="src/a.py", test="tests/t.py")
            assert "{" not in rendered, f"{name} left an unfilled placeholder"

    def test_saboteur_touches_another_module_not_the_test(self):
        _, _, _, template = next(a for a in demo.AGENTS if a[0] == "saboteur")
        rendered = template.format(sha="s", source="src/a.py", test="tests/t.py")
        assert "numbers.py" in rendered and "tests/t.py" not in rendered.split("&&")[1]

    def test_test_editor_touches_the_test(self):
        _, _, _, template = next(a for a in demo.AGENTS if a[0] == "test-editor")
        assert "{test}" in template


class TestShorten:
    def test_replaces_the_workspace_path(self, tmp_path):
        assert demo.shorten(f"{tmp_path}/out/x.jsonl", tmp_path) == "$DEMO/out/x.jsonl"

    def test_abbreviates_the_interpreter(self, tmp_path):
        assert demo.shorten(sys.executable, tmp_path) == "python"

    def test_leaves_ordinary_arguments_alone(self, tmp_path):
        assert demo.shorten("--limit", tmp_path) == "--limit"
