"""The report layer -- summary maths and page generation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import report  # noqa: E402
from groundhog.models import Usage, price_of  # noqa: E402


def attempt(model, task_id, solved, cost=0.1, seconds=10.0):
    return {
        "model": model, "task_id": task_id, "subject": f"task {task_id}",
        "solved": solved, "cost_usd": cost, "seconds": seconds, "error": "",
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
