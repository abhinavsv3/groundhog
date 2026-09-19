"""JavaScript/TypeScript support, and Node's version of the shadowing hazard."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import environment as env, validate  # noqa: E402


def package(tmp_path, deps, lockfile=None, name="demo"):
    (tmp_path / "package.json").write_text(json.dumps({
        "name": name, "version": "1.0.0", "devDependencies": deps,
    }))
    if lockfile:
        (tmp_path / lockfile).write_text("{}")
    return tmp_path


class TestDetection:
    def test_vitest(self, tmp_path):
        plan = env.detect(package(tmp_path, {"vitest": "^2"}, "package-lock.json"))
        assert plan.language == "javascript" and "vitest" in plan.test_cmd
        assert "--reporter=json" in plan.test_cmd

    def test_jest(self, tmp_path):
        plan = env.detect(package(tmp_path, {"jest": "^29"}, "package-lock.json"))
        assert "jest" in plan.test_cmd and "--json" in plan.test_cmd

    def test_lockfile_picks_the_package_manager(self, tmp_path):
        plan = env.detect(package(tmp_path, {"vitest": "^2"}, "pnpm-lock.yaml"))
        assert plan.install == ["pnpm install --frozen-lockfile"]

    def test_npm_lockfile_uses_ci(self, tmp_path):
        assert env.detect(package(tmp_path, {"vitest": "^2"}, "package-lock.json")).install == ["npm ci"]

    def test_unknown_runner_is_not_guessed_at(self, tmp_path):
        """mocha and node:test do not emit the jest result shape. Guessing
        would produce a harness that reports zero tests as zero failures."""
        assert env.detect_js(package(tmp_path, {"mocha": "^10"}, "package-lock.json")) is None

    def test_malformed_package_json_is_not_a_crash(self, tmp_path):
        (tmp_path / "package.json").write_text("{ not json")
        assert env.detect_js(tmp_path) is None


class TestOutcomeParsing:
    REPORT = json.dumps({
        "testResults": [
            {"name": "/repo/test/text.test.js", "assertionResults": [
                {"fullName": "clamps", "status": "passed"},
                {"fullName": "dedupes", "status": "failed"},
                {"fullName": "skipped one", "status": "pending"},
            ]},
        ]
    })

    def test_reads_statuses(self):
        out = validate.js_outcomes(self.REPORT)
        assert out["text.test.js::clamps"] == "PASSED"
        assert out["text.test.js::dedupes"] == "FAILED"

    def test_pending_is_not_passing(self):
        assert "text.test.js::skipped one" not in validate.passing(self.REPORT, "javascript")

    def test_tolerates_leading_runner_noise(self):
        """vitest prints progress before the JSON payload."""
        noisy = "RUN v2.1.0 /repo\n\n" + self.REPORT
        assert validate.passing(noisy, "javascript") == {"text.test.js::clamps"}

    def test_no_json_at_all(self):
        assert validate.js_outcomes("command not found: vitest") == {}


class TestNodeModulesLink:
    def test_links_when_absent(self, tmp_path):
        source, tree = tmp_path / "src", tmp_path / "tree"
        (source / "node_modules" / "left-pad").mkdir(parents=True)
        tree.mkdir()
        (tree / "package.json").write_text(json.dumps({"name": "demo"}))
        validate.link_node_modules(source, tree)
        assert (tree / "node_modules" / "left-pad").exists()

    def test_drops_a_self_reference(self, tmp_path):
        """A package importing itself by name would resolve through
        node_modules to the ORIGINAL clone, making the agent's edits invisible
        and passing every test regardless -- Node's form of the editable-install
        bug that cost this project twenty click tasks."""
        source, tree = tmp_path / "src", tmp_path / "tree"
        (source / "node_modules" / "demo").mkdir(parents=True)
        (source / "node_modules" / "left-pad").mkdir(parents=True)
        tree.mkdir()
        (tree / "package.json").write_text(json.dumps({"name": "demo"}))
        validate.link_node_modules(source, tree)
        assert (tree / "node_modules" / "left-pad").exists(), "deps must survive"
        assert not (tree / "node_modules" / "demo").exists(), "self-link must not"

    def test_noop_without_node_modules(self, tmp_path):
        source, tree = tmp_path / "src", tmp_path / "tree"
        source.mkdir(); tree.mkdir()
        validate.link_node_modules(source, tree)
        assert not (tree / "node_modules").exists()
