"""groundhog show assembles one task's story from tasks, results and patches."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(tmp_path: Path) -> None:
    (tmp_path / "tasks").mkdir()
    (tmp_path / "results" / "patches").mkdir(parents=True)
    task = {"sha": "abcdef123456789", "subject": "Add count_words()", "date": "2026-01-02T00:00:00",
            "language": "python", "shape": "single-file", "size": "small",
            "source_files": ["lib/text.py"], "test_files": ["tests/test_text.py"],
            "fail_to_pass": ["tests/test_text.py::test_count_words"],
            "pass_to_pass": ["tests/test_text.py::test_clamp"], "p2p_scope": "file",
            "failure_excerpt": "FAILED tests/test_text.py::test_count_words - AttributeError"}
    (tmp_path / "tasks" / "t.jsonl").write_text(json.dumps(task) + "\n")
    sloppy = {"task_id": "abcdef123456", "subject": "Add count_words()", "model": "ollama:sloppy",
              "run_index": 0, "solved": False, "fail_to_pass_passed": True, "broke_pass_to_pass": 1,
              "tampered_with_tests": False, "turns": 1, "seconds": 2.0, "files_touched": ["lib/text.py"],
              "tools": {"calls": 1, "by_name": {"write_file": 1}, "recovered_from_text": 1}, "error": ""}
    good = {**sloppy, "model": "ollama:good", "solved": True, "broke_pass_to_pass": 0}
    (tmp_path / "results" / "main.jsonl").write_text(json.dumps(sloppy) + "\n" + json.dumps(good) + "\n")
    (tmp_path / "results" / "patches" / "abcdef123456.ollama-sloppy.0.diff").write_text(
        "--- a/lib/text.py\n+++ b/lib/text.py\n-def clamp(): ...\n+def count_words(t): return len(t.split())\n")
    (tmp_path / "results" / "patches" / "abcdef123456.human.diff").write_text(
        "--- a/lib/text.py\n+++ b/lib/text.py\n+def count_words(t):\n+    return len(t.split())\n")


def show(tmp_path, *args):
    proc = subprocess.run([sys.executable, "-m", "groundhog", "show", *args, "--color", "never"],
                          cwd=tmp_path, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})
    return proc.returncode, proc.stdout + proc.stderr


def test_assembles_task_attempts_and_patches(tmp_path):
    build(tmp_path)
    code, out = show(tmp_path, "abcdef12")
    assert code == 0, out
    assert "Add count_words()" in out
    assert "FAIL_TO_PASS (1)" in out and "test_count_words" in out
    assert "PASS_TO_PASS (1, scope file)" in out
    assert "AttributeError" in out
    assert "ollama:sloppy  not solved  target tests passed, but broke 1 passing test(s)" in out
    assert "ollama:good  solved" in out
    assert "text-recovered 1" in out
    assert "-def clamp(): ..." in out
    assert "THE HUMAN'S FIX" in out
    assert "\033[" not in out


def test_model_filter_and_no_diff(tmp_path):
    build(tmp_path)
    code, out = show(tmp_path, "abcdef12", "--model", "good", "--no-diff")
    assert code == 0
    assert "ollama:good" in out and "ollama:sloppy" not in out
    assert "THE HUMAN'S FIX" not in out


def test_unknown_task_is_an_error(tmp_path):
    build(tmp_path)
    code, out = show(tmp_path, "ffffff")
    assert code == 1
    assert "no task or attempt matching" in out


def test_same_model_in_two_experiments_shows_both(tmp_path):
    build(tmp_path)
    src = (tmp_path / "results" / "main.jsonl").read_text()
    (tmp_path / "results" / "ablation.jsonl").write_text(src.splitlines()[0] + "\n")
    code, out = show(tmp_path, "abcdef12", "--model", "sloppy", "--no-diff")
    assert out.count("ollama:sloppy  not solved") == 2
    assert "from results/ablation.jsonl" in out and "from results/main.jsonl" in out
