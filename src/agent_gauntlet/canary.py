"""A behavioural fingerprint cheap enough to repeat (#19).

`Certificate` records what a configuration scored and when it expires. What
it cannot do is notice that the world moved underneath it, and #19's own
comment thread names why: a provider can change a model's behaviour
**without changing the model string**. There is no version to diff, no
changelog to watch, no event to trigger on. Polling version strings is not
merely weak, it is structurally incapable of seeing the common case.

Which leaves a periodic canary: a tiny, pinned probe set whose output
distribution moving is the signal to go and run the real suite.

The whole difficulty is in what "moving" means, and this module exists to
stop three obvious versions of it from shipping.

**A fingerprint is not a hash.** Hashing the outputs and comparing for
equality makes any run-to-run variance read as a change, which on a
stochastic agent means every night. The comparison here is between
intervals, and `moved` requires that they do not overlap -- non-overlap
implies a real difference, while overlap does not imply sameness. That is
the conservative direction, and for a trigger whose failure mode is crying
wolf it is the right one.

**"Unchanged" is a claim about sensitivity, not about the world.** A canary
of eight runs cannot see a twenty-point shift. Reporting UNCHANGED without
saying so is the fail-green shape this project keeps finding in itself,
arriving in the one place that is supposed to catch it. Every comparison
carries `blind_below`: the smallest shift these two fingerprints could have
separated at all.

**A canary that cannot see the regression you care about is not a canary.**
`sensitivity` compares `blind_below` against a `RegressionBudget`'s
`needed_resolution()` and says, in runs, what it would take. A cheap
nightly canary is arithmetically incapable of detecting a 10% quality drop,
and the honest thing is to print the number rather than a green tick.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from statistics import mean
from typing import Optional, Sequence

from pydantic import BaseModel, Field

from .attribution import METRICS
from .certify import RegressionBudget
from .ledger import RunRecord
from .stats import Interval, bootstrap, runs_needed, wilson

CANARY_METRICS = ("clean_quality", "faulted_quality", "propagation_rate")
"""What a canary watches.

Correctness on each side of the counterfactual pair, and the one gate
metric. Deliberately not the whole board: a canary that watches everything
fires on something every night, and a trigger nobody believes is worse
than no trigger.
"""

BEHAVIOURAL = "mean_steps"
"""The non-correctness signal, and the reason this is a *behavioural*
fingerprint rather than a score check.

A provider can change a model's behaviour without changing what it gets
right -- more tool calls, a different order, an extra round of
reconsideration. Steps move first and correctness moves later, so a canary
watching only correctness finds out last.
"""


class Shift(str, Enum):
    MOVED = "moved"
    UNCHANGED = "unchanged"
    NOT_COMPARABLE = "not_comparable"
    """One side has no interval for this metric. Never 'unchanged'."""


class Canary(BaseModel):
    """One configuration's behaviour, at one moment, with its own error bars."""

    variant_id: str
    task_fingerprint: str
    variant_fingerprint: Optional[str] = None
    taken_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    base_seed: str = ""
    repeats: int = 0
    """Pinned so the next take re-runs the same cells. A canary drawn
    against fresh seeds differs for two reasons at once, and the agent's own
    variance gets read as the world moving -- the same trap the
    counterfactual pair and the metamorphic relations both avoid by sharing
    a seed."""

    n_runs: int = 0
    metrics: dict[str, float] = Field(default_factory=dict)
    intervals: dict[str, Interval] = Field(default_factory=dict)


class MetricShift(BaseModel):
    metric: str
    shift: Shift
    before: Optional[float] = None
    after: Optional[float] = None
    blind_below: Optional[float] = None
    """The smallest change these two fingerprints could have separated.

    The sum of the two half-widths: below it the intervals overlap whatever
    the true difference is. An UNCHANGED verdict says nothing about shifts
    smaller than this, and the report refuses to let that go unsaid.
    """

    @property
    def delta(self) -> Optional[float]:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before


class CanaryReport(BaseModel):
    refusal: Optional[str] = None
    variant_id: str = ""
    shifts: list[MetricShift] = Field(default_factory=list)
    n_before: int = 0
    n_after: int = 0

    @property
    def moved(self) -> list[MetricShift]:
        return [s for s in self.shifts if s.shift is Shift.MOVED]

    @property
    def verdict(self) -> Shift:
        if self.moved:
            return Shift.MOVED
        if any(s.shift is Shift.NOT_COMPARABLE for s in self.shifts):
            return Shift.NOT_COMPARABLE
        return Shift.UNCHANGED

    @property
    def blind_below(self) -> Optional[float]:
        """The worst blind spot across the RATE metrics watched.

        A max rather than a mean: the canary as a whole is only as
        sensitive as its least sensitive signal, and quoting the average
        would advertise a sensitivity no single metric has.

        `mean_steps` is excluded on purpose. Its width is in steps and the
        rates' widths are in proportion points, so folding them together
        would produce a number in no unit at all -- and then compare it
        against a quality budget expressed in points.
        """
        widths = [s.blind_below for s in self.shifts
                  if s.blind_below is not None and s.metric != BEHAVIOURAL]
        return max(widths) if widths else None

    @property
    def blind_below_steps(self) -> Optional[float]:
        """The behavioural signal's blind spot, in steps."""
        for s in self.shifts:
            if s.metric == BEHAVIOURAL:
                return s.blind_below
        return None


def take(
    records: Sequence[RunRecord], *, variant_id: str, task_fingerprint: str,
    base_seed: str = "", repeats: int = 0,
    variant_fingerprint: Optional[str] = None,
) -> Canary:
    """Fingerprint one configuration from the runs just made."""
    mine = [r for r in records if r.variant_id == variant_id]
    metrics: dict[str, float] = {}
    intervals: dict[str, Interval] = {}

    for name in CANARY_METRICS:
        spec = METRICS[name]
        values = [spec.value(r) for r in mine if spec.include(r)]
        if not values:
            continue          # censored, and absent rather than zero
        metrics[name] = mean(values)
        # Wilson, not bootstrap, and the same choice the board makes for
        # these three. They are proportions of a per-run boolean, and a
        # bootstrap over n identical observations returns a ZERO-WIDTH
        # interval -- which this module would then read as infinite
        # sensitivity and print "blind below 0%". A canary claiming it can
        # see any change at all, because every run agreed, is the exact
        # fail-green shape it exists to catch.
        ci = wilson(sum(1 for v in values if v), len(values))
        if ci:
            intervals[name] = ci

    steps = [float(r.score.steps) for r in mine]
    if steps:
        metrics[BEHAVIOURAL] = mean(steps)
        ci = bootstrap(steps, seed=f"canary:{variant_id}:{BEHAVIOURAL}")
        if ci:
            intervals[BEHAVIOURAL] = ci

    return Canary(
        variant_id=variant_id, task_fingerprint=task_fingerprint,
        variant_fingerprint=variant_fingerprint,
        base_seed=base_seed, repeats=repeats, n_runs=len(mine),
        metrics=metrics, intervals=intervals,
    )


def compare(before: Canary, after: Canary) -> CanaryReport:
    """Has anything underneath this configuration moved?

    Refuses outright when the two fingerprints are not about the same
    thing. A canary compared against a different task, or against a
    configuration whose prompt was edited, would report the edit as the
    world moving -- which is the one conclusion an operator must not draw
    from this.
    """
    if before.task_fingerprint != after.task_fingerprint:
        return CanaryReport(
            variant_id=after.variant_id,
            refusal=(f"different tasks: baseline is {before.task_fingerprint}, "
                     f"this run is {after.task_fingerprint}. A canary only "
                     "compares like with like"),
        )
    if before.variant_id != after.variant_id:
        return CanaryReport(
            variant_id=after.variant_id,
            refusal=f"different configs: {before.variant_id} vs {after.variant_id}",
        )
    if (before.variant_fingerprint and after.variant_fingerprint
            and before.variant_fingerprint != after.variant_fingerprint):
        return CanaryReport(
            variant_id=after.variant_id,
            refusal=(f"the config itself changed ({before.variant_fingerprint} "
                     f"-> {after.variant_fingerprint}). That is an edit, not "
                     "the world moving -- re-certify rather than canary"),
        )

    shifts: list[MetricShift] = []
    for name in (*CANARY_METRICS, BEHAVIOURAL):
        a, b = before.intervals.get(name), after.intervals.get(name)
        if a is None or b is None:
            shifts.append(MetricShift(
                metric=name, shift=Shift.NOT_COMPARABLE,
                before=before.metrics.get(name), after=after.metrics.get(name),
            ))
            continue
        shifts.append(MetricShift(
            metric=name,
            shift=Shift.MOVED if a.excludes(b) else Shift.UNCHANGED,
            before=a.value, after=b.value,
            # Non-overlap needs the gap to clear both half-widths. Below
            # that the intervals touch whatever the truth is.
            blind_below=(a.width + b.width) / 2,
        ))

    return CanaryReport(variant_id=after.variant_id, shifts=shifts,
                        n_before=before.n_runs, n_after=after.n_runs)


def sensitivity(
    report: CanaryReport, budget: Optional[RegressionBudget] = None
) -> dict[str, object]:
    """Can this canary see the regression the budget cares about?

    The question the issue's "what is the minimum viable re-run?" reduces
    to, and it has an arithmetic answer rather than a judgement. A canary
    blind below 45 points is not a trigger for a 10-point budget, however
    often it runs.
    """
    budget = budget or RegressionBudget()
    needed = budget.needed_resolution()
    blind = report.blind_below
    if blind is None:
        return {"needed": needed, "blind_below": None, "adequate": None,
                "runs_needed": runs_needed(needed)}
    return {
        "needed": needed,
        "blind_below": blind,
        "adequate": blind <= needed,
        "runs_needed": runs_needed(needed),
    }


def save(canaries: Sequence[Canary], path) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([c.model_dump(mode="json") for c in
                    sorted(canaries, key=lambda c: c.variant_id)],
                   indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load(path) -> list[Canary]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Canary.model_validate(item) for item in raw]


__all__ = ["BEHAVIOURAL", "CANARY_METRICS", "Canary", "CanaryReport",
           "MetricShift", "Shift", "compare", "load", "save", "sensitivity",
           "take"]
