"""Python repos built on unittest, with no pytest anywhere."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import environment as env  # noqa: E402
from groundhog import mine, validate  # noqa: E402

OUTPUT_311 = """\
test_add (tests.test_calc.TestCalc.test_add) ... ok
test_div (tests.test_calc.TestCalc.test_div) ... FAIL
test_skip (tests.test_calc.TestCalc.test_skip) ... skipped 'not today'
test_boom (tests.test_calc.TestOther.test_boom) ... ERROR

======================================================================
FAIL: test_div (tests.test_calc.TestCalc.test_div)
"""
OUTPUT_310 = "test_add (tests.test_calc.TestCalc) ... ok\ntest_div (tests.test_calc.TestCalc) ... FAIL\n"


class TestParser:
    def test_311_format(self):
        out = validate.unittest_outcomes(OUTPUT_311)
        assert out["tests.test_calc.TestCalc::test_add"] == "PASSED"
        assert out["tests.test_calc.TestCalc::test_div"] == "FAILED"
        assert out["tests.test_calc.TestCalc::test_skip"] == "SKIPPED"
        assert out["tests.test_calc.TestOther::test_boom"] == "ERROR"

    def test_310_format(self):
        out = validate.unittest_outcomes(OUTPUT_310)
        assert out == {"tests.test_calc.TestCalc::test_add": "PASSED",
                       "tests.test_calc.TestCalc::test_div": "FAILED"}

    def test_python_parser_falls_back_to_unittest(self):
        assert validate.passing(OUTPUT_311, "python") == {"tests.test_calc.TestCalc::test_add"}
        pytest_out = "tests/test_x.py::test_a PASSED\n"
        assert validate.passing(pytest_out, "python") == {"tests/test_x.py::test_a"}


class TestTargetsAndForm:
    def test_files_become_modules_for_unittest(self):
        cmd = "python -m unittest -v {tests}"
        assert validate.test_targets("python", ["tests/test_calc.py"], cmd) == "tests.test_calc"
        assert validate.test_targets("python", ["src/pkg/tests/test_a.py"], cmd) == "pkg.tests.test_a"

    def test_pytest_targets_unchanged(self):
        assert validate.test_targets("python", ["tests/test_calc.py"], "python -m pytest {tests} -q") == \
            "tests/test_calc.py"

    def test_verbose_form_leaves_unittest_alone(self):
        assert validate.verbose_form("python -m unittest -v tests.test_calc", "python") == \
            "python -m unittest -v tests.test_calc"


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    root = tmp_path / "ut"
    root.mkdir()
    sh(root, "git", "init", "-q")
    sh(root, "git", "config", "user.email", "t@example.com")
    sh(root, "git", "config", "user.name", "T")
    (root / "setup.py").write_text("from setuptools import setup\nsetup(name='ut', py_modules=['calc'])\n")
    (root / "tests").mkdir()
    (root / "tests" / "__init__.py").write_text("")
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (root / "tests" / "test_calc.py").write_text(
        "import unittest\nfrom calc import add\n\n\nclass TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "initial")
    (root / "calc.py").write_text("def add(a, b):\n    return a + b\n\n\ndef double(x):\n    return 2 * x\n")
    (root / "tests" / "test_calc.py").write_text(
        "import unittest\nfrom calc import add, double\n\n\nclass TestCalc(unittest.TestCase):\n"
        "    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n\n"
        "    def test_double(self):\n        self.assertEqual(double(4), 8)\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "Add double")
    return root


class TestEndToEnd:
    def test_detection_picks_unittest(self, repo):
        plan = env.detect(repo)
        assert plan.language == "python"
        assert plan.test_cmd == "python -m unittest -v {tests}"
        assert not any("pytest" in cmd for cmd in plan.install)
        assert any("unittest" in n for n in plan.notes)

    def test_validate_produces_f2p_and_p2p(self, repo):
        sha, date, subject = mine.iter_commits(repo, "10 years ago", 5)[0]
        cand, why = mine.evaluate(repo, sha, date, subject, argparse.Namespace(
            max_source_lines=200, min_source_lines=1, max_source_files=5))
        assert cand, why
        cfg = argparse.Namespace(test_cmd=f"{sys.executable} -m unittest -v {{tests}}",
                                 timeout=120, language="python")
        verdict = validate.validate_one(repo, json.loads(json.dumps(cand.__dict__)), cfg, None)
        assert verdict["status"] == "valid", verdict
        # The new test file imports `double`, which does not exist before the
        # fix, so the whole module fails to import and both tests flip. pytest
        # behaves the same way on a collection error.
        assert verdict["fail_to_pass"] == ["tests.test_calc.TestCalc::test_add",
                                           "tests.test_calc.TestCalc::test_double"]
        assert verdict["pass_to_pass"] == []
        assert "unittest" in verdict["test_cmd"] and "tests.test_calc" in verdict["test_cmd"]
