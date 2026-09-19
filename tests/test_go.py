"""Go support. A compiled language breaks assumptions pytest let us make."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import environment as env, validate  # noqa: E402


class TestDetection:
    def test_finds_a_go_module(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n")
        plan = env.detect(tmp_path)
        assert plan.language == "go"
        assert plan.install == ["go mod download"]
        assert "-json" in plan.test_cmd, "bare -v cannot disambiguate same-named tests"

    def test_go_wins_over_a_stray_pyproject(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        assert env.detect(tmp_path).language == "go"

    def test_failure_message_names_both_languages(self, tmp_path):
        (tmp_path / "main.rs").write_text("fn main() {}")
        try:
            env.detect(tmp_path)
        except env.DetectionFailed as exc:
            assert "go.mod" in str(exc) and "pyproject.toml" in str(exc)
        else:
            raise AssertionError("should have failed")


class TestTargets:
    def test_go_runs_packages_not_files(self):
        assert validate.test_targets("go", ["strutil/strutil_test.go"]) == "./strutil"

    def test_go_deduplicates_packages(self):
        out = validate.test_targets("go", ["a/x_test.go", "a/y_test.go", "b/z_test.go"])
        assert out.split() == ["./a", "./b"]

    def test_go_handles_a_root_package(self):
        assert validate.test_targets("go", ["main_test.go"]) == "."

    def test_python_still_runs_files(self):
        assert validate.test_targets("python", ["tests/t.py"]) == "tests/t.py"


class TestOutcomeParsing:
    EVENTS = "\n".join([
        '{"Action":"run","Package":"example.com/x/strutil","Test":"TestClamp"}',
        '{"Action":"pass","Package":"example.com/x/strutil","Test":"TestClamp"}',
        '{"Action":"fail","Package":"example.com/x/strutil","Test":"TestPad"}',
        '{"Action":"pass","Package":"example.com/x/numutil","Test":"TestClamp"}',
        'plain text that is not json',
        '{"Action":"pass","Package":"example.com/x/strutil"}',
    ])

    def test_reads_pass_and_fail(self):
        out = validate.go_outcomes(self.EVENTS)
        assert out["example.com/x/strutil::TestClamp"] == "PASSED"
        assert out["example.com/x/strutil::TestPad"] == "FAIL"

    def test_qualifies_by_package(self):
        """Two packages may each define TestClamp; FAIL_TO_PASS must not collide."""
        passing = validate.passing(self.EVENTS, "go")
        assert "example.com/x/strutil::TestClamp" in passing
        assert "example.com/x/numutil::TestClamp" in passing

    def test_ignores_package_level_events(self):
        assert not any(k.endswith("::None") for k in validate.go_outcomes(self.EVENTS))

    def test_tolerates_non_json_lines(self):
        assert validate.go_outcomes("go: downloading x\n") == {}


class TestVerboseForm:
    def test_pytest_gains_v_and_loses_x(self):
        out = validate.verbose_form("python -m pytest t.py -x -q", "python")
        assert "-v" in out and " -x " not in out

    def test_go_command_is_left_alone(self):
        """go test -json is already per-test; pytest flag rewriting would
        produce nonsense."""
        cmd = "go test -json -count=1 ./strutil"
        assert validate.verbose_form(cmd, "go") == cmd
