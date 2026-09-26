"""Agent presets, and grading an agent that commits its work."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import agents, run  # noqa: E402


class TestPresets:
    def test_unknown_name_lists_the_choices(self):
        with pytest.raises(agents.AgentMissing) as info:
            agents.preset("clippy")
        assert "claude-code" in str(info.value)
        assert "--agent-cmd" in str(info.value)

    def test_missing_binary_says_how_to_install(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(agents.AgentMissing) as info:
            agents.preset("aider")
        assert "pip install aider-chat" in str(info.value)

    def test_present_binary_returns_the_command(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)
        assert "{prompt_file}" in agents.preset("aider").command

    def test_every_preset_uses_a_known_placeholder(self):
        for p in agents.PRESETS.values():
            assert any(tag in p.command for tag in ("{prompt}", "{prompt_file}")), p.name
            assert p.install, p.name

    def test_table_marks_unverified_presets(self):
        text = agents.describe_presets()
        assert "VERIFIED" in text
        assert "Unverified presets" in text
        for name in agents.PRESETS:
            assert name in text

    def test_prompt_tells_the_agent_not_to_commit(self):
        prompt = agents.build_prompt({"test_files": ["tests/t.py"]}, "boom", "pytest")
        assert "Do not commit" in prompt


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def fixed_repo(tmp_path) -> tuple[Path, dict]:
    """One validated task, hand-assembled so no environment build is needed."""
    root = tmp_path / "repo"
    root.mkdir()
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
    parent = sh(root, "git", "rev-parse", "HEAD").strip()

    (root / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef double(x):\n    return 2 * x\n"
    )
    (root / "tests" / "test_calc.py").write_text(
        "from calc import add, double\n\ndef test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_double():\n    assert double(4) == 8\n"
    )
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "Add double")
    sha = sh(root, "git", "rev-parse", "HEAD").strip()

    task = {
        "sha": sha, "parent": parent, "subject": "Add double", "date": "2026-01-01",
        "test_files": ["tests/test_calc.py"], "source_files": ["calc.py"],
        "fail_to_pass": ["tests/test_calc.py::test_double"],
        "pass_to_pass": ["tests/test_calc.py::test_add"],
        "language": "python", "p2p_scope": "file",
    }
    return root, task


def config(out: Path, **over) -> argparse.Namespace:
    base = dict(
        venv=None, test_cmd=f"{sys.executable} -m pytest {{tests}} -x -q", timeout=120,
        max_turns=1, max_nudges=0, agent_cmd=None, agent=None, agent_timeout=120,
        no_patches=False, out=out,
    )
    base.update(over)
    return argparse.Namespace(**base)


class TestCommittingAgent:
    def test_committed_fix_is_graded_and_its_patch_kept(self, fixed_repo, tmp_path):
        """aider commits by default. A diff against HEAD would show nothing."""
        repo, task = fixed_repo
        out = tmp_path / "results.jsonl"
        agent = (
            f"git checkout {task['sha']} -- calc.py && git -c user.email=a@b -c user.name=a "
            f"commit -qam 'agent fix'"
        )
        cfg = config(out, agent_cmd=agent, agent="committer")
        record = run.attempt(repo, task, "committer", cfg)

        assert record.solved, record.error
        assert record.agent == "committer"
        assert "calc.py" in record.files_touched
        assert record.patch_file, "the committed change must still be saved as a patch"
        assert "double" in Path(record.patch_file).read_text()

    def test_preset_name_labels_the_row(self, fixed_repo, tmp_path):
        repo, task = fixed_repo
        cfg = config(tmp_path / "r.jsonl", agent_cmd="true", agent="idle-preset")
        record = run.attempt(repo, task, "idle-preset", cfg)
        assert record.agent == "idle-preset"
        assert not record.solved


class TestSampleAndPrompt:
    def test_same_seed_same_tasks(self, tmp_path):
        import random
        tasks = [{"sha": f"{i:040x}"} for i in range(20)]
        a = random.Random(7).sample(sorted(tasks, key=lambda t: t["sha"]), 5)
        b = random.Random(7).sample(sorted(reversed(tasks), key=lambda t: t["sha"]), 5)
        assert a == b

    def test_prompt_hash_recorded_and_differs_by_prompt(self, fixed_repo, tmp_path):
        repo, task = fixed_repo
        default = run.attempt(repo, task, "idle", config(tmp_path / "a.jsonl", agent_cmd="true"))
        cfg = config(tmp_path / "b.jsonl", agent_cmd="true")
        cfg.system_prompt_text = "Be terse."
        custom = run.attempt(repo, task, "idle", cfg)
        assert len(default.prompt_hash) == 12
        assert default.prompt_hash != custom.prompt_hash

    def test_cli_sample_and_prompt_flags(self, fixed_repo, tmp_path):
        import json, os, subprocess
        repo, task = fixed_repo
        tasks = tmp_path / "t.jsonl"
        # Three copies of one real task: enough to sample from, all checkable-out.
        tasks.write_text("".join(json.dumps({**task, "subject": f"Add double {c}"}) + "\n" for c in "abc"))
        prompt = tmp_path / "p.md"
        prompt.write_text("custom")
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        # --system-prompt with an external agent is refused
        proc = subprocess.run([sys.executable, "-m", "groundhog", "run", str(repo), "--tasks", str(tasks),
                               "--out", str(tmp_path / "o.jsonl"), "--agent-cmd", "true",
                               "--system-prompt", str(prompt), "--no-auto-env",
                               "--test-cmd", f"{sys.executable} -m pytest {{tests}} -q"],
                              capture_output=True, text=True, env=env)
        assert proc.returncode == 2 and "built-in loop" in proc.stderr
        # --sample picks a subset
        proc = subprocess.run([sys.executable, "-m", "groundhog", "run", str(repo), "--tasks", str(tasks),
                               "--out", str(tmp_path / "o.jsonl"), "--agent-cmd", "true", "--no-auto-env",
                               "--sample", "2", "--seed", "3",
                               "--test-cmd", f"{sys.executable} -m pytest {{tests}} -q"],
                              capture_output=True, text=True, env=env)
        assert proc.returncode == 0, proc.stderr
        assert "sampled 2 tasks with seed 3" in proc.stderr
        assert len((tmp_path / "o.jsonl").read_text().splitlines()) == 2
