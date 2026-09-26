"""Rust support: detection, target naming, and the cargo test parser."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import environment as env  # noqa: E402
from groundhog import validate  # noqa: E402


class TestDetect:
    def test_cargo_toml_means_rust(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n')
        plan = env.detect(tmp_path)
        assert plan.language == "rust"
        assert plan.install == ["cargo fetch"]
        assert plan.test_cmd.startswith("cargo test")
        assert "{tests}" in plan.test_cmd

    def test_go_wins_when_both_present(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        (tmp_path / "go.mod").write_text("module x\n")
        assert env.detect(tmp_path).language == "go"


class TestTargets:
    def test_integration_files_become_test_targets(self):
        assert validate.test_targets("rust", ["tests/parse.rs"]) == "--test parse"
        out = validate.test_targets("rust", ["tests/b.rs", "tests/a.rs", "tests/a.rs"])
        assert out == "--test a --test b"

    def test_nested_files_run_everything(self):
        """tests/common/mod.rs is a module of some target we cannot name."""
        assert validate.test_targets("rust", ["tests/common/mod.rs"]) == ""
        assert validate.test_targets("rust", ["tests/a.rs", "crates/x/tests/y.rs"]) == ""

    def test_verbose_form_is_identity(self):
        assert validate.verbose_form("cargo test --no-fail-fast --test a", "rust") == \
            "cargo test --no-fail-fast --test a"


OUTPUT = """\
   Compiling demo v0.1.0 (/tmp/demo)
    Finished `test` profile [unoptimized + debuginfo] target(s) in 1.02s
     Running unittests src/lib.rs (target/debug/deps/demo-1a2b3c)

running 2 tests
test tests::add_works ... ok
test tests::sub_works ... FAILED

failures:
    tests::sub_works

test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured

     Running tests/parse.rs (target/debug/deps/parse-4d5e6f)

running 3 tests
test parses_empty ... ok
test parses_nested ... ignored
test parses_simple ... ok

test result: ok. 2 passed; 0 failed; 1 ignored
"""


class TestParser:
    def test_names_are_qualified_by_target(self):
        out = validate.rust_outcomes(OUTPUT)
        assert out["lib::tests::add_works"] == "PASSED"
        assert out["lib::tests::sub_works"] == "FAILED"
        assert out["parse::parses_empty"] == "PASSED"
        assert out["parse::parses_nested"] == "SKIPPED"

    def test_passing_collects_only_ok(self):
        assert validate.passing(OUTPUT, "rust") == {
            "lib::tests::add_works", "parse::parses_empty", "parse::parses_simple"}

    def test_compile_failure_yields_nothing(self):
        text = "error[E0425]: cannot find function `double` in this scope\nerror: could not compile `demo`\n"
        assert validate.rust_outcomes(text) == {}
        assert validate.passing(text, "rust") == set()
