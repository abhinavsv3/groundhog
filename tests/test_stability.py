"""--stability: a task whose tests flip between runs is rejected as flaky."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import mine, validate  # noqa: E402


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


# Passes on every run except the third one in a given tree. Deterministic
# flakiness, so the test of the flakiness detector is itself not flaky.
FLAKY_TEST = '''
def test_third_time_unlucky():
    from pathlib import Path
    marker = Path(__file__).parent / ".count"
    n = int(marker.read_text()) if marker.exists() else 0
    marker.write_text(str(n + 1))
    assert n != 2
'''


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "flaky"
    root.mkdir()
    sh(root, "git", "init", "-q")
    sh(root, "git", "config", "user.email", "t@example.com")
    sh(root, "git", "config", "user.name", "T")
    (root / "tests").mkdir()
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "initial")
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef double(x):\n    return 2 * x\n")
    (root / "tests" / "test_calc.py").write_text(
        "from calc import add, double\n\ndef test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_double():\n    assert double(2) == 4\n" + FLAKY_TEST
    )
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "Add double, and a flaky test")
    return root


def candidate(repo: Path) -> dict:
    sha, date, subject = mine.iter_commits(repo, "10 years ago", 5)[0]
    cand, why = mine.evaluate(repo, sha, date, subject, argparse.Namespace(
        max_source_lines=200, min_source_lines=1, max_source_files=5))
    assert cand, why
    return json.loads(json.dumps(cand.__dict__))


def cfg(**over) -> argparse.Namespace:
    base = dict(test_cmd=f"{sys.executable} -m pytest {{tests}} -q", timeout=120, language="python")
    base.update(over)
    return argparse.Namespace(**base)


def test_default_single_check_accepts_the_flaky_task(repo):
    verdict = validate.validate_one(repo, candidate(repo), cfg(), None)
    assert verdict["status"] == "valid", verdict
    assert verdict["stability_runs"] == 1


def test_stability_three_rejects_it_and_names_the_test(repo):
    verdict = validate.validate_one(repo, candidate(repo), cfg(stability=3), None)
    assert verdict["status"] == "rejected"
    assert verdict["reason"].startswith("flaky: 1 test(s)")
    assert "test_third_time_unlucky" in verdict["detail"]


def test_manifest_records_the_setting(repo):
    stamp = validate.manifest(repo, [], cfg(stability=2))
    assert stamp["stability_runs"] == 2
