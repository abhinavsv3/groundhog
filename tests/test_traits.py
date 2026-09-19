"""Change-shape classification and the spend ceiling."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import mine, report, run as runner  # noqa: E402


class TestConcurrencyDetection:
    @pytest.mark.parametrize("line", [
        "+async def send(self, request):",
        "+    await self._pool.close()",
        "+import asyncio",
        "+    lock = threading.Lock()",
        "+from concurrent.futures import ThreadPoolExecutor",
        "+    sem = Semaphore(4)",
    ])
    def test_fires_on_concurrency(self, line):
        assert mine.CONCURRENCY.search(line)

    @pytest.mark.parametrize("line", [
        "+def ordinary(value):",
        "+    return max(low, min(value, high))",
        "+# awaiting further review",     # prose, not code
        "+    threaded_discussion = True",  # substring, not the construct
    ])
    def test_quiet_on_ordinary_code(self, line):
        assert not mine.CONCURRENCY.search(line)


class TestSizeBuckets:
    """classify() needs a repo for the diff read; size is checked via evaluate()."""

    @pytest.mark.parametrize("lines,expected", [(3, "small"), (19, "small"),
                                                (20, "medium"), (80, "medium"),
                                                (81, "large")])
    def test_boundaries(self, tmp_path, lines, expected):
        import subprocess
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
        traits = mine.classify(tmp_path, "HEAD", "HEAD~1", [], [], lines)
        assert traits["size"] == expected

    def test_shape_follows_file_count(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
        assert mine.classify(tmp_path, "H", "P", ["a.py"], [], 5)["shape"] == "single-file"
        assert mine.classify(tmp_path, "H", "P", ["a.py", "b.py"], [], 5)["shape"] == "cross-module"


class TestBreakdown:
    def tasks(self, shapes):
        return {f"t{i}": {"sha": f"t{i}", "shape": s, "size": "small", "concurrency": False}
                for i, s in enumerate(shapes)}

    def results(self, outcomes):
        return [{"task_id": f"t{i}", "solved": s, "model": "m", "subject": "x"}
                for i, s in enumerate(outcomes)]

    def test_prints_when_a_trait_varies(self, capsys):
        report.by_trait(self.results([True, False]), self.tasks(["single-file", "cross-module"]))
        assert "BY CHANGE SHAPE" in capsys.readouterr().out

    def test_silent_when_nothing_varies(self, capsys):
        """A uniform task set has no contrast; an empty section would imply
        the breakdown found nothing rather than that there was nothing."""
        report.by_trait(self.results([True, False]), self.tasks(["single-file", "single-file"]))
        assert capsys.readouterr().out == ""

    def test_silent_without_tasks(self, capsys):
        report.by_trait(self.results([True]), {})
        assert capsys.readouterr().out == ""


class TestSpendTracking:
    def test_sums_recorded_cost(self, tmp_path):
        out = tmp_path / "r.jsonl"
        out.write_text("\n".join(json.dumps({"cost_usd": c}) for c in (0.5, 1.25, 0.25)))
        assert runner.spent_so_far(out) == pytest.approx(2.0)

    def test_treats_unpriced_attempts_as_zero(self, tmp_path):
        out = tmp_path / "r.jsonl"
        out.write_text(json.dumps({"cost_usd": None}) + "\n" + json.dumps({"cost_usd": 1.0}))
        assert runner.spent_so_far(out) == pytest.approx(1.0)

    def test_missing_file(self, tmp_path):
        assert runner.spent_so_far(tmp_path / "nope.jsonl") == 0.0

    def test_survives_truncation(self, tmp_path):
        out = tmp_path / "r.jsonl"
        out.write_text(json.dumps({"cost_usd": 2.0}) + '\n{"cost_usd": 1')
        assert runner.spent_so_far(out) == pytest.approx(2.0)
