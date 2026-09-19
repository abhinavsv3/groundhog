"""Deduplication and resume — both protect the integrity of a run."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import mine, run as runner, validate  # noqa: E402


def task(sha, subject, f2p, source=("src/a.py",), date="2026-01-01", reverts=""):
    return {
        "sha": sha, "subject": subject, "fail_to_pass": list(f2p),
        "source_files": list(source), "date": date, "reverts": reverts,
    }


class TestRevertDetection:
    def test_recognises_a_revert_subject(self):
        match = mine.REVERT.match('Revert "Deprecate isolated_filesystem"')
        assert match and match.group("subject") == "Deprecate isolated_filesystem"

    def test_ignores_ordinary_subjects(self):
        assert mine.REVERT.match("Revert the decision informally") is None
        assert mine.REVERT.match("Add reverting support") is None


class TestDeduplicate:
    def test_identical_target_tests_are_one_task(self):
        kept, dropped = validate.deduplicate([
            task("a", "Add thing", ["t::x"], date="2026-01-01"),
            task("b", "Add thing again", ["t::x"], date="2026-02-01"),
        ])
        assert len(kept) == 1 and len(dropped) == 1
        assert kept[0]["sha"] == "b", "the newest should survive"

    def test_revert_chain_collapses_even_when_tests_moved(self):
        """The click case: the same test moved files across the cycle, so the
        F2P sets were disjoint despite being one change."""
        kept, _ = validate.deduplicate([
            task("a", "Deprecate x", ["old/t.py::x"], date="2026-01-01"),
            task("b", 'Revert "Deprecate x"', ["other::y"], date="2026-01-02", reverts="Deprecate x"),
            task("c", "Deprecate x", ["new/t.py::x"], date="2026-01-03"),
        ])
        assert len(kept) == 1 and kept[0]["sha"] == "c"

    def test_same_subject_and_shared_source_merges(self):
        kept, _ = validate.deduplicate([
            task("a", "Fix parser", ["t::a"], source=["src/p.py"]),
            task("b", "Fix parser", ["t::b"], source=["src/p.py", "src/q.py"]),
        ])
        assert len(kept) == 1

    def test_same_subject_without_shared_source_does_not_merge(self):
        """Otherwise every commit called 'fix tests' collapses into one."""
        kept, _ = validate.deduplicate([
            task("a", "fix tests", ["t::a"], source=["src/p.py"]),
            task("b", "fix tests", ["t::b"], source=["src/z.py"]),
        ])
        assert len(kept) == 2

    def test_unrelated_tasks_are_left_alone(self):
        kept, dropped = validate.deduplicate([
            task("a", "Add clamp", ["t::clamp"], source=["src/a.py"]),
            task("b", "Add chunk", ["t::chunk"], source=["src/b.py"]),
        ])
        assert len(kept) == 2 and not dropped

    def test_tasks_without_targets_are_kept_separately(self):
        kept, _ = validate.deduplicate([
            task("a", "Old task", [], source=["src/a.py"]),
            task("b", "Other task", [], source=["src/b.py"]),
        ])
        assert len(kept) == 2

    def test_empty_input(self):
        assert validate.deduplicate([]) == ([], [])


class TestResume:
    def write(self, path, rows):
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    def test_reads_completed_triples(self, tmp_path):
        out = tmp_path / "r.jsonl"
        self.write(out, [
            {"task_id": "aaa", "model": "m", "run_index": 0},
            {"task_id": "aaa", "model": "m", "run_index": 1},
        ])
        assert runner.already_done(out) == {("aaa", "m", 0), ("aaa", "m", 1)}

    def test_distinguishes_repeats_of_the_same_task(self, tmp_path):
        out = tmp_path / "r.jsonl"
        self.write(out, [{"task_id": "aaa", "model": "m", "run_index": 0}])
        done = runner.already_done(out)
        assert ("aaa", "m", 0) in done and ("aaa", "m", 1) not in done

    def test_missing_file_is_empty(self, tmp_path):
        assert runner.already_done(tmp_path / "nope.jsonl") == set()

    def test_survives_a_truncated_final_line(self, tmp_path):
        """Expected after a hard kill mid-write."""
        out = tmp_path / "r.jsonl"
        out.write_text(json.dumps({"task_id": "aaa", "model": "m", "run_index": 0}) + "\n{\"task_id\": \"bb")
        assert runner.already_done(out) == {("aaa", "m", 0)}

    def test_ignores_records_missing_fields(self, tmp_path):
        out = tmp_path / "r.jsonl"
        self.write(out, [{"model": "m"}, {"task_id": "ok", "model": "m", "run_index": 0}])
        assert runner.already_done(out) == {("ok", "m", 0)}
