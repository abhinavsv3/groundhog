"""The report layer -- summary maths and page generation."""

from __future__ import annotations

import json
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import report  # noqa: E402
from groundhog.models import Usage, price_of  # noqa: E402


def attempt(model, task_id, solved, cost=0.1, seconds=10.0, calls=3):
    return {
        "model": model, "task_id": task_id, "subject": f"task {task_id}",
        "solved": solved, "cost_usd": cost, "seconds": seconds, "error": "",
        "tools": {"calls": calls},
    }


class TestSummary:
    def test_ranks_by_pass_rate(self):
        rows = report.summarise([
            attempt("a", "t1", True), attempt("a", "t2", True),
            attempt("b", "t1", True), attempt("b", "t2", False),
        ])
        assert [r["model"] for r in rows] == ["a", "b"]
        assert rows[0]["pass_rate"] == 1.0
        assert rows[1]["pass_rate"] == 0.5

    def test_cost_per_solve_ignores_failures(self):
        rows = report.summarise([attempt("a", "t1", True, cost=1.0), attempt("a", "t2", False, cost=1.0)])
        assert rows[0]["cost"] == 2.0
        assert rows[0]["cost_per_solve"] == 2.0

    def test_no_solves_leaves_cost_per_solve_unset(self):
        assert report.summarise([attempt("a", "t1", False)])[0]["cost_per_solve"] is None

    def test_counts_errors(self):
        bad = attempt("a", "t1", False)
        bad["error"] = "HTTP 500"
        assert report.summarise([bad])[0]["errors"] == 1


class TestSite:
    def test_fills_the_placeholder(self, tmp_path):
        template = tmp_path / "t.html"
        template.write_text("<body>__DATA__</body>")
        out = tmp_path / "out.html"
        records = [attempt("a", "t1", True)]
        report.write_site(report.summarise(records), records, "demo/repo", out, template)
        html = out.read_text()
        assert "__DATA__" not in html
        assert json.loads(html[len("<body>"): -len("</body>\n")] if html.endswith("\n") else html[6:-7])["repo"] == "demo/repo"


class TestPricing:
    def test_known_model_is_priced(self):
        assert price_of("claude-opus-5", Usage(1_000_000, 0)) == 15.0

    def test_unknown_model_has_no_price(self):
        assert price_of("some-local-model", Usage(1_000_000, 0)) is None


class TestTextToolCalls:
    """Local models often emit tool calls as text; we must recover them."""

    KNOWN = {"read_file", "write_file", "run_tests"}

    def test_bare_json(self):
        from groundhog.models import parse_text_tool_calls
        calls = parse_text_tool_calls('{"name": "read_file", "arguments": {"path": "a.py"}}', self.KNOWN)
        assert len(calls) == 1 and calls[0].name == "read_file" and calls[0].args == {"path": "a.py"}

    def test_fenced_json(self):
        from groundhog.models import parse_text_tool_calls
        text = 'Sure.\n```json\n{"name":"run_tests","arguments":{}}\n```'
        assert parse_text_tool_calls(text, self.KNOWN)[0].name == "run_tests"

    def test_parameters_alias(self):
        from groundhog.models import parse_text_tool_calls
        assert parse_text_tool_calls('{"name":"read_file","parameters":{"path":"b.py"}}', self.KNOWN)[0].args == {"path": "b.py"}

    def test_arguments_as_string(self):
        from groundhog.models import parse_text_tool_calls
        calls = parse_text_tool_calls('{"name":"read_file","arguments":"{\\"path\\":\\"c.py\\"}"}', self.KNOWN)
        assert calls[0].args == {"path": "c.py"}

    def test_ignores_prose(self):
        from groundhog.models import parse_text_tool_calls
        assert parse_text_tool_calls("I think we should edit the file.", self.KNOWN) == []

    def test_ignores_unknown_tool(self):
        from groundhog.models import parse_text_tool_calls
        assert parse_text_tool_calls('{"name":"rm_rf","arguments":{}}', self.KNOWN) == []

    def test_ignores_malformed_json(self):
        from groundhog.models import parse_text_tool_calls
        assert parse_text_tool_calls('{"name": "read_file", "arguments": {', self.KNOWN) == []


class TestUnmeasuredModels:
    """A model whose output the harness could not read has no score.

    See study/mistral-artifact.md: mistral emitted correct fixes as markdown
    code fences rather than tool calls, so every call was discarded and the run
    recorded a clean-looking 0% with no errors. A percentage there states the
    parser's failure as the model's capability.
    """

    def silent_run(self, model, n=10, silent=9):
        return [attempt(model, f"t{i}", False, calls=0 if i < silent else 2)
                for i in range(n)]

    def test_mostly_silent_run_is_flagged(self):
        rows = report.summarise(self.silent_run("quiet"))
        assert rows[0]["unmeasured"] is True
        assert rows[0]["silent"] == 9

    def test_a_model_that_called_tools_and_failed_is_not_flagged(self):
        rows = report.summarise([attempt("tries", f"t{i}", False) for i in range(10)])
        assert rows[0]["unmeasured"] is False
        assert rows[0]["pass_rate"] == 0.0

    def test_unmeasured_never_outranks_a_measured_model(self):
        rows = report.summarise(
            self.silent_run("quiet")
            + [attempt("works", f"t{i}", False) for i in range(10)]
        )
        assert [r["model"] for r in rows] == ["works", "quiet"]

    def test_table_refuses_the_number_and_says_why(self, capsys):
        records = self.silent_run("quiet")
        report.print_table(report.summarise(records), records)
        out = capsys.readouterr().out
        assert "not measured" in out
        assert "9 of 10 attempts parsed no tool call" in out
        # The model's own row must carry no percentage at all.
        row = next(l for l in out.splitlines() if l.startswith("\033[2mquiet"))
        assert "%" not in row

    def test_unmeasured_tasks_are_not_marked_as_failures(self, capsys):
        records = self.silent_run("quiet")
        report.print_table(report.summarise(records), records)
        grid = capsys.readouterr().out.split("PER TASK")[1]
        assert "fail" not in grid

    def test_a_task_a_model_never_ran_is_not_a_failure(self, capsys):
        records = ([attempt("a", "t1", True), attempt("a", "t2", False)]
                   + [attempt("b", "t1", True)])
        report.print_table(report.summarise(records), records)
        grid = capsys.readouterr().out.split("PER TASK")[1]
        assert grid.count("fail") == 1

    def test_same_family_columns_stay_distinguishable(self):
        a = report.fit(report.label("openai:qwen2.5-coder:7b"), 12)
        b = report.fit(report.label("openai:qwen2.5-coder:14b"), 12)
        assert a != b
        assert a.endswith("7b") and b.endswith("14b")

    def test_latest_models_get_distinct_labels(self):
        assert report.label("openai:mistral:latest") == "mistral"
        assert report.label("openai:llama3.1:latest") == "llama3.1"
        assert report.label("openai:qwen3:8b") == "qwen3:8b"

    def test_page_payload_carries_the_flag(self, tmp_path):
        records = self.silent_run("quiet")
        template = tmp_path / "t.html"
        template.write_text("var data = __DATA__;")
        out = tmp_path / "page.html"
        report.write_site(report.summarise(records), records, "repo", out, template)
        payload = json.loads(out.read_text()[len("var data = "):-1])
        assert payload["models"][0]["unmeasured"] is True
        assert payload["models"][0]["silent"] == 9


class TestOverstatement:
    """The F2P-only view next to the true score: the study's central finding."""

    @staticmethod
    def attempt(model, solved, f2p, broke=0, tampered=False):
        return {
            "model": model, "task_id": f"t{solved}{f2p}{broke}", "subject": "s",
            "solved": solved, "fail_to_pass_passed": f2p, "broke_pass_to_pass": broke,
            "tampered_with_tests": tampered, "seconds": 1.0, "cost_usd": None,
            "tools": {"calls": 3},
        }

    def test_rows_carry_the_f2p_only_view(self):
        from groundhog.report import summarise
        rows = summarise([
            self.attempt("m", True, True),
            self.attempt("m", False, True, broke=9),
            self.attempt("m", False, True, broke=4),
            self.attempt("m", False, False),
        ])
        row = rows[0]
        assert row["solved"] == 1
        assert row["f2p_only_solved"] == 3
        assert row["collateral_attempts"] == 2
        assert row["collateral_tests_broken"] == 13

    def test_test_editing_is_counted_separately_from_collateral(self):
        from groundhog.report import summarise
        rows = summarise([self.attempt("m", False, True, tampered=True)])
        assert rows[0]["f2p_only_solved"] == 1
        assert rows[0]["collateral_attempts"] == 0
        assert rows[0]["tampered"] == 1

    def test_section_prints_only_when_there_is_a_gap(self, capsys):
        from groundhog.report import overstatement, summarise
        overstatement(summarise([self.attempt("clean", True, True), self.attempt("clean", False, False)]))
        assert capsys.readouterr().out == ""
        overstatement(summarise([self.attempt("sloppy", False, True, broke=2)]))
        out = capsys.readouterr().out
        assert "FAIL_TO_PASS-ONLY vs TRUE" in out
        assert "1 broke 2" in out
        assert "+100pts" in out

    def test_reproduces_the_study(self):
        """study/collateral.md: qwen2.5-coder:7b, 6/38 by F2P alone, 3/38 true, 14 tests broken."""
        import json
        from pathlib import Path
        from groundhog.report import summarise
        path = Path(__file__).resolve().parents[1] / "results" / "study-all.jsonl"
        if not path.exists():
            pytest.skip("study results not checked out")
        rows = {r["model"]: r for r in summarise([json.loads(l) for l in path.read_text().splitlines() if l.strip()])}
        row = rows["openai:qwen2.5-coder:7b"]
        assert (row["f2p_only_solved"], row["solved"]) == (6, 3)
        assert (row["collateral_attempts"], row["collateral_tests_broken"]) == (3, 14)
        assert rows["openai:qwen3:8b"]["collateral_attempts"] == 0


class TestMarkdownAndBadge:
    @staticmethod
    def records():
        mk = TestOverstatement.attempt
        rows = [mk("ollama:good", True, True), mk("ollama:good", False, False),
                mk("ollama:sloppy", False, True, broke=3), mk("ollama:sloppy", False, False)]
        for i, r in enumerate(rows):
            r["task_id"] = f"t{i % 2}"
            r["subject"] = "Fix | a thing" if i % 2 else "Add x"
        return rows

    def test_markdown_has_no_escape_codes_and_escapes_pipes(self):
        from groundhog.report import markdown, summarise
        text = markdown(summarise(self.records()), self.records(), title="Nightly")
        assert "\033[" not in text
        assert text.startswith("### Nightly")
        assert "| ollama:good | 1/2 | 50% |" in text
        assert "1 broke 3" in text
        assert "Fix \\| a thing" in text
        assert "✅" in text and "❌" in text
        assert "F2P-only** is what" in text

    def test_markdown_marks_unmeasured_models(self):
        from groundhog.report import markdown, summarise
        recs = self.records()
        for r in recs:
            r["tools"] = {"calls": 0}
        text = markdown(summarise(recs), recs)
        assert "_not measured_" in text
        assert "failed measurement" in text

    def test_badge_uses_best_measured_model(self):
        from groundhog.badge import badge
        from groundhog.report import summarise
        out = badge(summarise(self.records()), None, "agent pass rate")
        assert out["schemaVersion"] == 1
        assert out["message"].startswith("50% (1/2)")
        assert "good" in out["message"]
        assert out["color"] == "green"

    def test_badge_for_one_model_by_short_name(self):
        from groundhog.badge import badge
        from groundhog.report import summarise
        out = badge(summarise(self.records()), "sloppy", "rate")
        assert out["message"] == "0% (0/2)"
        assert out["color"] == "red"

    def test_badge_refuses_an_unmeasured_model(self):
        from groundhog.badge import badge
        from groundhog.report import summarise
        recs = self.records()
        for r in recs:
            r["tools"] = {"calls": 0}
        out = badge(summarise(recs), None, "rate")
        assert out["message"] == "not measured"
        assert out["isError"]

    def test_cli_writes_both_files(self, tmp_path):
        import json as _json
        import subprocess, sys
        results = tmp_path / "r.jsonl"
        results.write_text("\n".join(_json.dumps(r) for r in self.records()) + "\n")
        subprocess.run([sys.executable, "-m", "groundhog", "report", "--results", str(results),
                        "--markdown", str(tmp_path / "r.md"), "--json", str(tmp_path / "r.json")],
                       check=True, capture_output=True)
        assert "| Model |" in (tmp_path / "r.md").read_text()
        assert _json.loads((tmp_path / "r.json").read_text())[0]["model"] == "ollama:good"
        subprocess.run([sys.executable, "-m", "groundhog", "badge", "--results", str(results),
                        "--out", str(tmp_path / "b.json")], check=True, capture_output=True)
        assert _json.loads((tmp_path / "b.json").read_text())["schemaVersion"] == 1


class TestExternalAgentsAreMeasured:
    def test_no_tool_record_is_not_silence_for_an_external_agent(self):
        from groundhog.report import summarise
        mk = TestOverstatement.attempt
        ext = [dict(mk("claude-code", s, s), agent="claude-code", tools={}, task_id=f"t{i}")
               for i, s in enumerate([True, False, False])]
        row = summarise(ext)[0]
        assert row["silent"] == 0
        assert not row["unmeasured"]
        assert row["pass_rate"] == pytest.approx(1 / 3)

    def test_builtin_loop_with_no_calls_is_still_unmeasured(self):
        from groundhog.report import summarise
        mk = TestOverstatement.attempt
        rows = summarise([dict(mk("ollama:m", False, False), tools={"calls": 0}, task_id=f"t{i}")
                          for i in range(3)])
        assert rows[0]["unmeasured"]
