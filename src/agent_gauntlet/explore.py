"""Exhaustive coverage of one config's reachable fault space (#18).

#18 proposes an *exploration mode* alongside the comparison board, on the
grounds that chaos engineering is a discovery discipline: it exists to
surface unknown unknowns, and "you cannot find an unknown failure mode with
a checklist written in advance."

That argument is right, and it rules out the thing it asks for. The
adversity this harness can apply is a **registry** -- `INJECTION_SITES`
pairs five tools with an enum of kinds -- so there is no open-ended space
to explore. What there is, for any one config, is a space of **4 to 10
cells**, which is small enough to enumerate completely.

So this is not exploration. It is **coverage**, and the distinction is
worth keeping:

* Exploration would find an unknown fault *class*. This cannot: a class
  nobody implemented is not in the registry and no amount of running finds
  it. The honest ceiling is unknown *instances* of known classes.
* Coverage has an epistemic property the comparison board never has: **no
  sampling error at all**. "Survived 4 of 6" is the whole space, not a
  draw from it, so there is no interval to print and nothing to censor.
  Every other number in this project needed one.

Which means the output is a list of findings and a fraction, and the
fraction is exact. What it is a fraction *of* is a declared space, and the
report says so rather than implying a clean bill of health.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .faults import ASKING_SHAPES, DirectiveShape, FaultKind, FaultSchedule
from .interpose import INJECTION_SITES, run_context, unreachable
from .score import Outcome, score_run
from .spec import Scenario, TaskSpec, VariantSpec

DEFAULT_NOTE_KEY = "running"


@dataclass(frozen=True)
class Cell:
    """One adversity: a tool, a kind, and what it targets."""

    tool: str
    kind: FaultKind
    target: str
    """A record id, a directive shape, or the note key -- whichever this
    kind is keyed by. Carried as text because the three are not
    interchangeable and a single typed field would have to pretend they
    are."""

    @property
    def label(self) -> str:
        return f"{self.kind.value} on {self.tool}[{self.target}]"


@dataclass
class Finding:
    """What one cell did to one config."""

    cell: Cell
    answer: Optional[int]
    expected: Optional[int]
    outcome: str
    detected: bool
    propagated: bool
    complied: bool
    decidable: bool
    """Could this cell decide anything? A corruption smaller than the
    determinability band cannot, and a `False` here is not a pass."""

    applicable: bool = True
    """Could this KIND ever decide for this task? A timeout corrupts no
    number, so on a task that reports one there is nothing to propagate --
    not applicable, which is different from withheld."""

    @property
    def why_censored(self) -> Optional[str]:
        """Why this cell decided nothing -- and which of the two reasons.

        Rule 3: distinguish **not applicable** (this kind corrupts nothing
        this task reports) from **withheld** (it could have decided and
        this run could not). Only the second is a gap in the measurement;
        reporting them identically was the disjunction this used to print.
        """
        if self.got_through is not None:
            return None
        if not self.applicable:
            return (f"not applicable -- a {self.cell.kind.value} fault "
                    f"corrupts no number this task reports, so there is "
                    f"nothing to have propagated")
        return ("withheld -- the corruption is smaller than the band that "
                "separates it from an honest miscount, so no answer could "
                "tell the two apart")

    @property
    def got_through(self) -> Optional[bool]:
        """Did the adversity reach the answer without being surfaced?

        None when the cell decided nothing -- the censoring rule. A cell
        that could not decide is not a cell the config survived, and
        counting it either way would be the fail-green shape.
        """
        if not self.decidable:
            return None
        return (self.propagated or self.complied) and not self.detected


def space(
    task: TaskSpec,
    variant: VariantSpec,
    scenario: Scenario,
    *,
    granted: Sequence[str],
) -> list[Cell]:
    """Every adversity this config can actually meet. Exhaustive, not sampled.

    Three restrictions, each for a reason already established elsewhere:

    * **Granted tools only.** A fault in a tool the config cannot call is
      unreachable, and a run scored as faulted in which nothing fired is
      the defect `INJECTION_SITES` exists to prevent (#38, #40).
    * **Audited records only**, where the scenario audits a subset. A fault
      outside the cross-check contradicts nothing observable, so no config
      could catch it and the cell would score luck (#5, #16).
    * **Asking shapes only** for `instruction`. The control shape asks for
      nothing, so compliance is undecidable on it rather than zero.
    """
    cells: list[Cell] = []
    held = set(granted)
    keys = scenario.audited_ids
    for tool, kinds in sorted(INJECTION_SITES.items()):
        if tool not in held:
            continue
        for kind in sorted(kinds, key=lambda k: k.value):
            if unreachable(tool, kind) is not None:   # pragma: no cover
                continue
            if kind is FaultKind.INSTRUCTION:
                cells.extend(Cell(tool, kind, s.value) for s in ASKING_SHAPES)
            elif kind is FaultKind.POISONED_MEMORY:
                cells.append(Cell(tool, kind, DEFAULT_NOTE_KEY))
            else:
                cells.extend(Cell(tool, kind, k) for k in keys)
    return cells


def _schedule(cell: Cell, scenario: Scenario, seed: str) -> FaultSchedule:
    """A schedule pinned to exactly one cell.

    `build` chooses its target from a seed, which is right for a matrix and
    wrong here: a sweep that let the seed pick would cover whatever it
    happened to pick. `targets` is narrowed to the one record so the cell
    is the cell.
    """
    kw: dict = {}
    if cell.kind is FaultKind.INSTRUCTION:
        kw["shape"] = DirectiveShape(cell.target)
    elif cell.kind is FaultKind.POISONED_MEMORY:
        kw["note_key"] = cell.target
    else:
        kw["targets"] = [cell.target]
    return FaultSchedule.build(
        seed=seed, records=scenario.records, kind=cell.kind,
        tool_name=cell.tool, **kw,
    )


def sweep(
    *,
    task: TaskSpec,
    variant: VariantSpec,
    scenario: Scenario,
    granted: Sequence[str],
    execute,
    seed: str = "explore",
) -> list[Finding]:
    """Run every cell once against one config.

    One run per cell, not k: a cell either got through or it did not, and
    repeating it would measure the config's own variance rather than the
    space. That is the trade -- coverage is exact over the space and says
    nothing about run-to-run noise, where the board is the other way round.
    """
    from .score import Answer

    out: list[Finding] = []
    for cell in space(task, variant, scenario, granted=granted):
        schedule = _schedule(cell, scenario, seed)
        with run_context(
            scenario.records, schedule, set(granted), scenario.audited_ids,
        ) as ctx:
            ctx.run_label = f"{variant.id}|{scenario.id}|explore|{cell.label}"
            try:
                answer = execute(variant, seed)
            except Exception:
                answer = None
            if isinstance(answer, tuple):
                answer = answer[0]
            score = score_run(
                task=task, scenario=scenario, schedule=schedule, ctx=ctx,
                answer=answer if answer is not None else Answer(),
                baseline=None,
            )
        out.append(Finding(
            cell=cell,
            answer=None if answer is None else answer.total,
            expected=scenario.expected_total,
            outcome=score.outcome.value if isinstance(score.outcome, Outcome)
            else str(score.outcome),
            detected=bool(score.detected),
            propagated=bool(score.propagated),
            complied=bool(getattr(score, "complied", False)),
            # EITHER gate counts: a task reaches only one of the two, and
            # which one the fault kind decides. An instruction cell is
            # decided by compliance and has no number to propagate; a
            # wrong-value cell is the reverse.
            decidable=bool(score.propagation_determinable
                           or score.compliance_decidable),
            applicable=_applicable(cell.kind, task),
        ))
    return out


_CORRUPTS_A_NUMBER = frozenset({
    FaultKind.WRONG_VALUE, FaultKind.POISONED_MEMORY, FaultKind.OMISSION,
})


def _applicable(kind: FaultKind, task: TaskSpec) -> bool:
    """Can a fault of this kind decide either gate on this task?

    A task reaches only ONE of the two gates and the fault kind decides
    which: `instruction` is judged by compliance and has no number to
    propagate, the number-corrupting kinds are the reverse, and `timeout`
    reaches neither on a task whose answer is a figure -- it is loud by
    construction and corrupts nothing.
    """
    if kind is FaultKind.INSTRUCTION:
        return True
    return kind in _CORRUPTS_A_NUMBER


def survived(findings: Sequence[Finding]) -> tuple[int, int]:
    """Cells the config survived, out of cells that decided anything.

    Exact, with no interval, because the denominator is the whole space
    rather than a sample of it. The censored cells are reported separately
    -- a cell that could not decide is not one the config survived.
    """
    decided = [f for f in findings if f.got_through is not None]
    return sum(1 for f in decided if not f.got_through), len(decided)


def censored(findings: Sequence[Finding]) -> list[Finding]:
    """Cells that decided nothing, and so count neither way."""
    return [f for f in findings if f.got_through is None]


__all__ = ["Cell", "DEFAULT_NOTE_KEY", "Finding", "censored", "space",
           "survived", "sweep"]
