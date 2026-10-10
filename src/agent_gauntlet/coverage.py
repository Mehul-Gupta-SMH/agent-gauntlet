"""How much of this task's quality is mechanical — before spending (#2).

#2 owns the noise floor: every point of score that comes from a judge
inherits the judge's variance, so the mechanical share is the difference
between a measurement instrument and an opinion poll with extra steps. The
issue asks for the ratio and suspects it is a property of the task domain.

Its comment thread found something better, and this module is that finding
made computable. Quality splits three ways:

- **output** — is the answer right? Needs a label, and is the part that is
  genuinely domain-bound.
- **process** — did it notice, did it repair, did it propagate, did it take
  orders from its data, what did surviving cost in work? Mechanically
  observable on *every* task, including one with no ground truth at all.
- **relations** — does the answer change when it must not? Needs no label
  either, and attacks exactly the cases where a judge would have dominated.

So the ratio is not one number per domain. It is a property of a specific
task and grid, and it is knowable **statically** — from the declared
oracle, the fault kind, and which tool sets the grid grants — before any
model is called.

Two things this is careful about.

**Reachable is not measured.** Everything here predicts whether a property
*could* produce a number, never that it will. A reachable propagation rate
still reads `n/a` when every corruption fell inside the noise band, and a
reachable detection rate needs runs that actually happened. The report says
so, and `audit` exists to catch the prediction being wrong: a property
predicted reachable whose board column came back `None` is a defect in one
of the two, and silently believing the prediction is how a coverage figure
becomes the same kind of comfortable lie as an uncensored zero.

**A low share is reported, not refused.** #30's rule: gate on binary safety
properties, report on statistical quality ones. Coverage is neither -- it is
a statement about what the run can see, and refusing to run a task because
it has no oracle would throw away the process family, which is the whole
point of the split.
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional, Sequence

from pydantic import BaseModel, Field

from .faults import FaultKind
from .spec import Oracle, TaskSpec


class Family(str, Enum):
    OUTPUT = "output"
    PROCESS = "process"
    RELATIONS = "relations"


class Property(BaseModel):
    """One scored property, and whether this task can produce it."""

    name: str
    family: Family
    reachable: bool
    why_not: Optional[str] = None
    """Empty when reachable. Never a bare False: "this task cannot measure
    detection" and "this task measured detection as zero" are the same
    distinction the board makes between `n/a` and `0%`."""

    gates: bool = False
    """Whether a failure here removes a configuration from the board.

    Printed because an unreachable gate metric is the serious case: the bar
    is *shown not to propagate*, not *not shown to*, so a task that cannot
    measure propagation cannot clear it either.
    """


class CoverageReport(BaseModel):
    task_id: str
    fingerprint: str
    oracle: str
    fault_kind: str
    properties: list[Property] = Field(default_factory=list)

    def of(self, family: Family) -> list[Property]:
        return [p for p in self.properties if p.family is family]

    def share(self, family: Optional[Family] = None) -> Optional[float]:
        pool = self.properties if family is None else self.of(family)
        if not pool:
            return None
        return sum(1 for p in pool if p.reachable) / len(pool)

    @property
    def unreachable_gates(self) -> list[Property]:
        """Gate metrics this task cannot measure.

        The reason a coverage report is worth printing before a run rather
        than after: a gate that cannot be measured cannot be cleared, and
        the matrix would otherwise produce a full leaderboard whose gate
        column was censored on every row.
        """
        return [p for p in self.properties if p.gates and not p.reachable]


def assess(
    task: TaskSpec,
    *,
    toolsets: Optional[Iterable[str]] = None,
    live: bool = False,
) -> CoverageReport:
    """What this task and grid can measure, computed without running it."""
    from . import metamorphic
    from .architect import TOOLSETS

    kind = FaultKind(task.fault_kind)
    labelled = task.oracle in (Oracle.FULL, Oracle.PARTIAL)

    declared = list(toolsets) if toolsets is not None else (
        list(task.grid.toolsets) if task.grid and task.grid.toolsets
        else ["records", "records+summary"]
    )
    # The cross-check rule the matrix uses, asked of the grid rather than of
    # one variant: any granted source other than the corrupted one can
    # contradict it.
    with_evidence = [
        t for t in declared
        if any(tool != task.fault_tool for tool in TOOLSETS.get(t, []))
        and "get_summary" in TOOLSETS.get(t, [])
    ]
    no_label = (
        f"oracle is {task.oracle.value!r}, so no run has a right answer to be "
        "graded against"
    )
    no_evidence = (
        f"no tool set in {declared} grants a cross-check that could contradict "
        f"{task.fault_tool!r}, so noticing was impossible in principle"
    )
    # "a instruction fault" reads as a typo, and this text is printed.
    article = "an" if kind.value[0] in "aeiou" else "a"
    wrong_kind = (
        f"{article} {kind.value} fault corrupts no number, so there is nothing "
        "for an answer to have propagated"
    )
    not_directive = (
        f"{article} {kind.value} fault gives no instruction, so there is nothing "
        "for a config to have obeyed"
    )

    props = [
        Property(name="quality", family=Family.OUTPUT, reachable=labelled,
                 why_not=None if labelled else no_label),
        Property(name="accuracy", family=Family.OUTPUT, reachable=labelled,
                 why_not=None if labelled else no_label),
        Property(name="clean_quality", family=Family.OUTPUT, reachable=labelled,
                 why_not=None if labelled else no_label),
        Property(name="faulted_quality", family=Family.OUTPUT, reachable=labelled,
                 why_not=None if labelled else no_label),

        Property(name="propagation_rate", family=Family.PROCESS, gates=True,
                 reachable=kind in _CORRUPTS_A_NUMBER,
                 why_not=None if kind in _CORRUPTS_A_NUMBER else wrong_kind),
        Property(name="compliance_rate", family=Family.PROCESS, gates=True,
                 reachable=kind is FaultKind.INSTRUCTION,
                 why_not=None if kind is FaultKind.INSTRUCTION else not_directive),
        Property(name="detection_rate", family=Family.PROCESS,
                 reachable=bool(with_evidence),
                 why_not=None if with_evidence else no_evidence),
        Property(name="repair_rate", family=Family.PROCESS,
                 reachable=bool(with_evidence),
                 why_not=None if with_evidence else no_evidence),
        Property(name="median_detect_latency", family=Family.PROCESS,
                 reachable=bool(with_evidence),
                 why_not=None if with_evidence else no_evidence),
        # These need neither a label nor a cross-check: a clean run either
        # cried wolf or did not, and every run has a step count.
        Property(name="false_alarm_rate", family=Family.PROCESS, reachable=True),
        Property(name="effort_ratio", family=Family.PROCESS, reachable=True),
        # Needs BOTH a stated confidence and a label to score it against.
        # The elicitation is uniform across variants, so the first half is
        # always there; the second is the caveat #21 names -- a confidence
        # has nothing to be scored against where there is no right answer.
        Property(name="brier", family=Family.PROCESS, reachable=labelled,
                 why_not=None if labelled else
                 "a stated confidence has nothing to be scored against "
                 "without a label, so calibration disappears with the oracle"),
        Property(name="cost_per_run", family=Family.PROCESS, reachable=live,
                 why_not=None if live else
                 "an offline run spends nothing, and zero is not a price"),
    ]

    relations = metamorphic.relations_for(task)
    props.append(Property(
        name="metamorphic_relations", family=Family.RELATIONS,
        reachable=bool(relations),
        why_not=None if relations else (
            "this task sets its own `expected`, so its answer is not the sum "
            "of the world and no built-in relation is provable for it -- "
            "declare the ones that hold"
        ),
    ))
    props.append(Property(
        name="steady_state_hypotheses", family=Family.RELATIONS,
        reachable=bool(task.hypotheses),
        why_not=None if task.hypotheses else "this task pre-registers no bounds",
    ))

    return CoverageReport(
        task_id=task.id, fingerprint=task.fingerprint(),
        oracle=task.oracle.value, fault_kind=kind.value, properties=props,
    )


_CORRUPTS_A_NUMBER = frozenset({
    FaultKind.WRONG_VALUE, FaultKind.POISONED_MEMORY, FaultKind.OMISSION,
})
"""Fault kinds that put a false FIGURE in front of the agent.

An instruction fault corrupts no number, so propagation is not applicable
rather than zero -- the same distinction `Score.propagation_applicable`
draws per run, asked of the task before any run exists.
"""


def audit(report: CoverageReport, results: Sequence) -> list[str]:
    """Where the prediction and the finished board disagree.

    The check that stops this module becoming a comfortable lie. A property
    predicted reachable whose column came back `None` on every contender
    means either the prediction is wrong or the run is, and both are worth
    a loud line rather than a coverage figure nobody reconciled.

    The reverse -- unreachable but populated -- is the worse direction, and
    is checked too: it means a number was printed for something this task
    was not supposed to be able to measure.
    """
    contenders = [r for r in results if not getattr(r, "is_sentinel", False)]
    if not contenders:
        return []

    problems: list[str] = []
    for prop in report.properties:
        if prop.family is Family.RELATIONS:
            continue        # not board columns
        if not any(hasattr(r, prop.name) for r in contenders):
            continue
        got = [getattr(r, prop.name, None) for r in contenders]
        populated = any(v is not None for v in got)
        if prop.reachable and not populated:
            problems.append(
                f"{prop.name}: predicted reachable, but no contender reported "
                "it. Either the prediction is wrong or the run could not "
                "produce it after all"
            )
        elif not prop.reachable and populated:
            problems.append(
                f"{prop.name}: predicted unreachable ({prop.why_not}), but a "
                "contender reported a number for it"
            )
    return problems


__all__ = ["CoverageReport", "Family", "Property", "assess", "audit"]


# --- separability: can this scenario decide propagation at all? -----------
#
# Reachability says propagation is measurable for this fault KIND. It can
# still be undecidable for this scenario's ARITHMETIC, and that is decided
# by which record the seed happens to pick. Two live `claude` runs on
# `discontinued.yaml` reported the credulous figure to the unit and scored
# with the verdict censored, because a 97 shift on a 668 total does not
# clear twice the 66-wide band (experiment 014).
#
# The draw is a fixed set of multiples over a known set of records, so the
# share of draws that would clear the threshold is computable before the
# run -- exactly, by enumeration, with no interval. That makes it a
# pre-flight fact rather than an after-the-fact disappointment.


class ScenarioSeparability(BaseModel):
    """What fraction of this scenario's possible corruptions is decidable."""

    scenario_id: str
    expected_total: int
    threshold: int
    """The shift a corruption must exceed: `separability_band`."""
    decidable_draws: int
    total_draws: int
    largest_shift: int
    """The biggest shift any single draw on any eligible record could make."""

    @property
    def share(self) -> float:
        return self.decidable_draws / self.total_draws if self.total_draws else 0.0

    @property
    def hopeless(self) -> bool:
        """No draw on any eligible record could clear the threshold.

        A scenario whose records are all small relative to its total:
        corrupting one of them cannot move the aggregate far enough to tell
        a believed lie from a miscount, whatever the seed.
        """
        return self.total_draws > 0 and self.decidable_draws == 0


class Separability(BaseModel):
    """The pre-flight separability report for one task."""

    task_id: str
    applicable: bool
    """False when the fault kind corrupts no number, or pins its own shift.

    `OMISSION` withholds a whole record, so its shift is the record's value
    and not a draw -- there is nothing to predict. `TIMEOUT` and
    `INSTRUCTION` corrupt no number at all.
    """
    guaranteed: bool
    """The task set `separable_faults`, so the injector pushes every
    corruption past the threshold and every run can decide."""
    scenarios: list[ScenarioSeparability] = Field(default_factory=list)

    @property
    def at_risk(self) -> list[ScenarioSeparability]:
        """Scenarios where some draw would land undecidable."""
        if self.guaranteed or not self.applicable:
            return []
        return [s for s in self.scenarios if s.share < 1.0]

    @property
    def hopeless(self) -> list[ScenarioSeparability]:
        if self.guaranteed or not self.applicable:
            return []
        return [s for s in self.scenarios if s.hopeless]


def separability(task) -> Separability:
    """Enumerate the draws, before any money is spent.

    Covered, not sampled: the corruption comes from a fixed set of
    multiples applied to one of a known set of records, so this is an exact
    fraction and carries no interval (the coverage-is-not-discovery rule).

    The enumeration mirrors `_plausible_corruption` and shares its factor
    table rather than restating it. It does not model the forced branch --
    the one that pushes a value past a band it was given -- because that
    branch only fires when `decidable_band` is set, which is the
    `guaranteed` case this function reports separately.
    """
    from .faults import PLAUSIBLE_FACTORS
    from .score import separability_band

    kind = FaultKind(task.fault_kind)
    rows: list[ScenarioSeparability] = []
    applicable = kind in (FaultKind.WRONG_VALUE, FaultKind.POISONED_MEMORY)

    if applicable:
        for scenario in task.scenarios:
            total = scenario.expected_total
            threshold = separability_band(total, task.tolerance)
            if kind is FaultKind.POISONED_MEMORY:
                # The note holds the running total, so the corruption is
                # drawn against the aggregate itself -- one target, not a
                # record each.
                values = [total]
            else:
                eligible = set(scenario.audited_ids or scenario.records)
                values = [v for k, v in scenario.records.items() if k in eligible]
            shifts = [
                abs(max(1, int(round(v * f))) - v)
                for v in values for f in PLAUSIBLE_FACTORS
                if max(1, int(round(v * f))) != v
            ]
            rows.append(ScenarioSeparability(
                scenario_id=scenario.id,
                expected_total=total,
                threshold=threshold,
                decidable_draws=sum(1 for d in shifts if d > threshold),
                total_draws=len(shifts),
                largest_shift=max(shifts, default=0),
            ))

    return Separability(
        task_id=task.id,
        applicable=applicable,
        guaranteed=bool(getattr(task, "separable_faults", False)),
        scenarios=rows,
    )
