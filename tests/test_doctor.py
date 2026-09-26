"""doctor: one screen that says what to install."""

import subprocess
import sys

from groundhog import doctor


def test_rows_cover_the_things_that_go_wrong(monkeypatch):
    monkeypatch.setenv("GROUNDHOG_OLLAMA_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    rows, fatal = doctor.checks(colour=False)
    labels = {r[0]: r for r in rows}
    assert not fatal
    assert labels["git"][1] == "ok"
    assert labels["python"][1] == "ok"
    assert labels["ollama"][1] == "not running" and "ollama serve" in labels["ollama"][2]
    assert labels["GROQ_API_KEY"][1] == "set"
    assert labels["XAI_API_KEY"][1] == "not set"
    assert labels["agent aider"][2].startswith(("pip install", "/"))
    assert "cache" in labels


def test_missing_git_is_fatal(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    rows, fatal = doctor.checks(colour=False)
    assert fatal
    assert dict((r[0], r[1]) for r in rows)["git"] == "missing"


def test_cli_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUNDHOG_CACHE", str(tmp_path))
    proc = subprocess.run([sys.executable, "-m", "groundhog", "doctor", "--no-color"],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "git" in proc.stdout and "ollama" in proc.stdout
    assert "\033[" not in proc.stdout
