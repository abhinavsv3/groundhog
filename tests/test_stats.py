"""Statistics — the guards against reporting noise as a finding."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from groundhog import stats  # noqa: E402


def run(pattern: list[bool]) -> list[dict]:
    return [{"task_id": f"t{i}", "solved": s} for i, s in enumerate(pattern)]


class TestWilson:
    def test_brackets_the_estimate(self):
        lo, hi = stats.wilson(6, 8)
        assert lo < 0.75 < hi

    def test_stays_in_range_at_zero(self):
        lo, hi = stats.wilson(0, 8)
        assert lo == 0.0 and 0 < hi < 1

    def test_stays_in_range_at_one(self):
        lo, hi = stats.wilson(8, 8)
        assert 0 < lo < 1 and hi == 1.0

    def test_narrows_with_more_data(self):
        narrow = stats.wilson(50, 100)
        wide = stats.wilson(5, 10)
        assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])

    def test_no_trials(self):
        assert stats.wilson(0, 0) == (0.0, 1.0)


class TestOutcomes:
    def test_majority_vote_over_repeats(self):
        records = [
            {"task_id": "t1", "solved": True},
            {"task_id": "t1", "solved": True},
            {"task_id": "t1", "solved": False},
        ]
        assert stats.outcomes(records) == {"t1": True}

    def test_minority_success_does_not_count(self):
        records = [
            {"task_id": "t1", "solved": True},
            {"task_id": "t1", "solved": False},
            {"task_id": "t1", "solved": False},
        ]
        assert stats.outcomes(records) == {"t1": False}

    def test_any_mode_rewards_a_single_success(self):
        records = [{"task_id": "t1", "solved": True}, {"task_id": "t1", "solved": False}]
        assert stats.outcomes(records, majority=False) == {"t1": True}


class TestMcNemar:
    def test_detects_a_large_difference(self):
        a = run([True] * 16 + [False] * 4)
        b = run([False] * 20)
        result = stats.mcnemar(a, b)
        assert result.significant and result.a_only == 16

    def test_ignores_a_tiny_difference(self):
        a = run([True] * 10 + [False] * 10)
        b = run([True] * 9 + [False] * 11)
        assert not stats.mcnemar(a, b).significant

    def test_identical_runs(self):
        a = run([True, False, True])
        result = stats.mcnemar(a, run([True, False, True]))
        assert result.discordant == 0 and result.p_value == 1.0
        assert "nothing to test" in result.verdict()

    def test_only_pairs_shared_tasks(self):
        a = [{"task_id": "t1", "solved": True}, {"task_id": "t2", "solved": True}]
        b = [{"task_id": "t1", "solved": False}, {"task_id": "t9", "solved": True}]
        assert stats.mcnemar(a, b).shared_tasks == 1

    def test_verdict_uses_labels(self):
        a = run([True] * 16 + [False] * 4)
        result = stats.mcnemar(a, run([False] * 20))
        assert "before" in result.verdict("before", "after")

    def test_warns_when_a_null_is_uninformative(self):
        a = run([True] * 10 + [False] * 10)
        b = run([True] * 9 + [False] * 11)
        assert "do not know" in stats.mcnemar(a, b).power_note()

    def test_no_warning_when_significant(self):
        a = run([True] * 16 + [False] * 4)
        assert stats.mcnemar(a, run([False] * 20)).power_note() is None


class TestPower:
    def test_smaller_effects_need_more_tasks(self):
        assert stats.tasks_needed(0.4, 0.05) > stats.tasks_needed(0.4, 0.20)

    def test_known_ballpark(self):
        # 40% -> 50% at 80% power is a few hundred per arm
        assert 300 < stats.tasks_needed(0.40, 0.10) < 500
