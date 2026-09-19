"""End-to-end tests over a throwaway repository built for the purpose."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import mine, run as run_command, validate  # noqa: E402


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    """A repo whose history contains one real fix, one refactor, one doc commit."""
    root = tmp_path_factory.mktemp("sample")
    sh(root, "git", "init", "-q")
    sh(root, "git", "config", "user.email", "t@example.com")
    sh(root, "git", "config", "user.name", "T")
    (root / "tests").mkdir()

    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "initial")

    # A real fix: new behaviour plus the test that proves it.
    (root / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef div(a, b):\n"
        "    if b == 0:\n        raise ValueError('division by zero')\n    return a / b\n"
    )
    (root / "tests" / "test_calc.py").write_text(
        "import pytest\n\nfrom calc import add, div\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_div():\n    assert div(6, 3) == 2\n\n"
        "def test_div_by_zero():\n    with pytest.raises(ValueError):\n        div(1, 0)\n"
    )
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "Add div with zero check")

    # A refactor: tests touched, behaviour identical. Must be rejected.
    (root / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef div(a, b):\n"
        "    if b == 0:\n        raise ValueError('division by zero')\n"
        "    result = a / b\n    return result\n"
    )
    (root / "tests" / "test_calc.py").write_text(
        (root / "tests" / "test_calc.py").read_text() + "\n\ndef test_div_float():\n    assert div(7, 2) == 3.5\n"
    )
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "Tidy div internals")

    # Docs only: must never become a candidate.
    (root / "README.md").write_text("# sample\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "Add readme")
    return root


def mine_config(**over) -> argparse.Namespace:
    cfg = argparse.Namespace(
        max_source_lines=200, min_source_lines=1, max_source_files=5
    )
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


class TestClassification:
    @pytest.mark.parametrize(
        "path",
        ["tests/test_x.py", "test_x.py", "src/foo_test.go", "app/Button.test.tsx", "spec/thing.rb"],
    )
    def test_recognises_tests(self, path):
        assert mine.is_test_file(path)

    @pytest.mark.parametrize("path", ["src/click/core.py", "httpx/_decoders.py", "main.go"])
    def test_recognises_source(self, path):
        assert mine.is_source_file(path)
        assert not mine.is_test_file(path)

    @pytest.mark.parametrize("path", ["CHANGELOG.md", "poetry.lock", "docs/guide.rst", "go.sum"])
    def test_ignores_noise(self, path):
        assert mine.is_noise(path)


class TestMining:
    def test_finds_commits_touching_source_and_tests(self, repo):
        commits = mine.iter_commits(repo, "10 years ago", 50)
        found = []
        for sha, date, subject in commits:
            cand, _ = mine.evaluate(repo, sha, date, subject, mine_config())
            if cand:
                found.append(cand.subject)
        assert "Add div with zero check" in found
        assert "Add readme" not in found

    def test_reports_a_reason_for_rejection(self, repo):
        commits = mine.iter_commits(repo, "10 years ago", 50)
        sha, date, subject = next(c for c in commits if c[2] == "Add readme")
        cand, reason = mine.evaluate(repo, sha, date, subject, mine_config())
        assert cand is None
        assert reason


class TestValidation:
    def run_all(self, repo):
        cfg = argparse.Namespace(test_cmd="python -m pytest {tests} -q", timeout=120)
        verdicts = {}
        for sha, date, subject in mine.iter_commits(repo, "10 years ago", 50):
            cand, _ = mine.evaluate(repo, sha, date, subject, mine_config())
            if cand is None:
                continue
            v = validate.validate_one(repo, json.loads(json.dumps(cand.__dict__)), cfg, None)
            verdicts[subject] = v
        return verdicts

    def test_keeps_the_real_fix(self, repo):
        assert self.run_all(repo)["Add div with zero check"]["status"] == "valid"

    def test_rejects_the_refactor(self, repo):
        verdict = self.run_all(repo)["Tidy div internals"]
        assert verdict["status"] == "rejected"
        assert "already pass" in verdict["reason"]


class TestSourceRoots:
    def test_prefers_src_layout(self, tmp_path):
        (tmp_path / "src").mkdir()
        roots = validate.source_roots(tmp_path)
        assert roots[0] == tmp_path / "src"
        assert tmp_path in roots

    def test_flat_layout_is_just_the_tree(self, tmp_path):
        assert validate.source_roots(tmp_path) == [tmp_path]


class TestRunSpendLimit:
    def test_stops_after_known_spend_reaches_limit_and_reports_skipped(
        self, tmp_path, monkeypatch, capsys
    ):
        tasks = tmp_path / "tasks.jsonl"
        tasks.write_text("".join(
            json.dumps({
                "sha": f"{i + 1:012x}" + "0" * 28,
                "subject": f"task {i}",
                "test_files": [],
            }) + "\n"
            for i in range(3)
        ))
        out = tmp_path / "results.jsonl"
        calls = []

        def fake_attempt(repo, task, model, cfg, run_index):
            calls.append(task["subject"])
            return run_command.Attempt(
                repo="sample", task_id=task["sha"][:12], subject=task["subject"],
                model=model, cost_usd=0.60,
            )

        monkeypatch.setattr(run_command, "attempt", fake_attempt)
        monkeypatch.setattr(sys, "argv", [
            "groundhog run", str(tmp_path), "--tasks", str(tasks), "--out", str(out),
            "--models", "openai:gpt-5.2", "--max-spend", "0.50", "--no-auto-env",
        ])

        assert run_command.main() == 0
        assert calls == ["task 0"]
        assert len(out.read_text().splitlines()) == 1
        assert "skipped 2 attempts" in capsys.readouterr().err

    def test_recorded_spend_ignores_unknown_costs_and_bad_lines(self, tmp_path):
        results = tmp_path / "results.jsonl"
        results.write_text(
            '{"cost_usd": 1.25}\n{"cost_usd": null}\n'
            '{"cost_usd": -2}\n{"cost_usd": NaN}\nnot-json\n'
        )

        assert run_command.recorded_spend(results) == 1.25
