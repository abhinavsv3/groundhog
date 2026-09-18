#!/usr/bin/env python3
"""Statistics for comparing two Groundhog runs.

Benchmark results are routinely reported as bare percentages, which invites
reading a three-point difference on thirty tasks as a finding. These functions
exist so Groundhog can refuse to do that.

Stdlib only -- no scipy.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass


def wilson(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion.

    Preferred over the normal approximation, which produces nonsense near 0 and
    1 -- exactly where benchmark results tend to live.
    """
    if trials == 0:
        return (0.0, 1.0)
    p = successes / trials
    d = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / d
    spread = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / d
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def binomial_tail(k: int, n: int, p: float = 0.5) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


@dataclass
class Comparison:
    """A paired comparison between two conditions on the same tasks."""

    shared_tasks: int
    a_only: int      # solved by A, not B
    b_only: int      # solved by B, not A
    both: int
    neither: int
    p_value: float
    a_rate: float
    b_rate: float

    @property
    def discordant(self) -> int:
        return self.a_only + self.b_only

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05

    def verdict(self, label_a: str = "A", label_b: str = "B") -> str:
        if self.discordant == 0:
            return "identical on every task — nothing to test"
        if self.significant:
            better = label_a if self.a_only > self.b_only else label_b
            return f"{better} is better (p = {self.p_value:.4f})"
        return f"no detectable difference (p = {self.p_value:.3f})"

    def power_note(self) -> str | None:
        """Warn when a null result is uninformative rather than reassuring."""
        if self.discordant == 0 or self.significant:
            return None
        if self.discordant < 10:
            return (
                f"Only {self.discordant} tasks differ between the conditions. "
                "That is too few to detect anything but a very large effect — "
                "treat a null result here as 'we do not know', not 'no difference'."
            )
        return None


def outcomes(records: list[dict], majority: bool = True) -> dict[str, bool]:
    """Collapse repeats into one outcome per task.

    With repeats, a task is counted as solved if the model solved it more often
    than not. Using "solved at least once" instead would reward variance, which
    is the wrong thing to reward in a reliability measurement.
    """
    tally: dict[str, list[int]] = defaultdict(list)
    for r in records:
        tally[r["task_id"]].append(bool(r["solved"]))
    if not majority:
        return {t: any(v) for t, v in tally.items()}
    return {t: sum(v) * 2 > len(v) for t, v in tally.items()}


def mcnemar(a: list[dict], b: list[dict]) -> Comparison:
    """Exact McNemar test on two runs over the same task set.

    Pairing on tasks removes task difficulty from the comparison, which is what
    makes a small task set usable at all. Only the tasks where the two
    conditions disagree carry information.
    """
    oa, ob = outcomes(a), outcomes(b)
    shared = sorted(set(oa) & set(ob))

    a_only = sum(1 for t in shared if oa[t] and not ob[t])
    b_only = sum(1 for t in shared if ob[t] and not oa[t])
    both = sum(1 for t in shared if oa[t] and ob[t])
    neither = len(shared) - a_only - b_only - both

    n = a_only + b_only
    if n == 0:
        p = 1.0
    else:
        p = min(1.0, 2 * binomial_tail(min(a_only, b_only), n))

    return Comparison(
        shared_tasks=len(shared),
        a_only=a_only,
        b_only=b_only,
        both=both,
        neither=neither,
        p_value=p,
        a_rate=(both + a_only) / len(shared) if shared else 0.0,
        b_rate=(both + b_only) / len(shared) if shared else 0.0,
    )


def tasks_needed(baseline: float, effect: float, power: float = 0.80) -> int:
    """Roughly how many tasks an unpaired comparison would need.

    Printed alongside results so nobody reports a difference their task set could
    never have detected. Pairing does better than this, but the number is a
    useful floor.
    """
    p1, p2 = baseline, baseline + effect
    if not 0 < p1 < 1 or not 0 < p2 < 1:
        return 0
    z_alpha, z_beta = 1.96, 0.84 if power <= 0.8 else 1.28
    pooled = (p1 + p2) / 2
    numerator = (
        z_alpha * math.sqrt(2 * pooled * (1 - pooled))
        + z_beta * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2
    return math.ceil(numerator / (p2 - p1) ** 2)
