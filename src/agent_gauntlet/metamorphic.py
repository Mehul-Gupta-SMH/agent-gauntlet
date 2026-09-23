"""Checks that need no right answer: relations between runs (#28).

Every other score in this project compares an answer against something the
harness knows. That works because the bundled tasks have an oracle, and it
stops working the moment somebody brings a task where nobody knows the
right answer -- which is most real work, and exactly where a judge would
have taken over and become the noise floor (#2).

Metamorphic testing dissolves part of that. Do not assert on the output;
assert on the **relation between two outputs** when the input is perturbed
in a way whose effect is known. Rename every record and the total must not
move. Treble every quantity and the total must treble. Neither assertion
needs to know what the total is.

Three properties make these worth having here:

* **No oracle.** The baseline is the config's OWN unperturbed answer, so a
  task with no known answer is still checkable.
* **No threshold.** The answers compared are integers. Invariance on free
  text would need a similarity score, and a similarity score is a judged
  quantity smuggled back in -- so this module does not do it, and says so
  rather than shipping a number nobody can defend.
* **Hard to satisfy accidentally.** A relation is a property of behaviour
  across perturbed inputs, not of one output. Writing something that is
  invariant under relabelling essentially requires being invariant under
  relabelling (#6).

What this module does NOT do is generate the perturbations inside the
matrix. `run_matrix` already runs each cell twice and compares -- the same
shape this needs -- but adding a third condition multiplies the cost of
every run anybody has budgeted for. So relations are their own command,
and folding them into the board is a decision about spend rather than
about machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from .spec import Scenario, TaskSpec, VariantSpec

SCALE_FACTOR = 3
"""What `scale` multiplies by. Three, not two: doubling is the one factor a
config could match by accident through an off-by-one halving somewhere."""


@dataclass(frozen=True)
class Relation:
    """One perturbation and the answer it demands."""

    name: str
    asks: str
    """What it asserts, in the words the report prints."""

    perturb: Callable[[Scenario], Scenario]
    expect: Callable[[int], int]
    """The answer the perturbed run must give, from the unperturbed one."""

    catches: str
    """What a violation means. A relation nobody can interpret is a number
    that gets ignored, and this project has enough of those to avoid."""

    slack: int = 0
    """Units of difference that are arithmetic rather than behaviour.

    Zero for every relation that changes no arithmetic: renaming records
    and splitting one in two leave the sum alone, so anything but equality
    is the config depending on something it should not.

    Non-zero only where the comparison itself rounds. `scale` multiplies an
    integer answer by k and compares it against an answer computed
    independently in a k-times-larger world, and those two can differ by
    the rounding of k sub-unit parts however well-behaved the agent is.
    Caught in practice: a policy whose miscount is a FRACTION of the total
    miscounts by a differently-rounded amount at 3x, and came out
    "violating" proportionality by one unit.

    This is a structural bound, not a tuned one. A threshold chosen to make
    a result pass would be the judged quantity this module exists to avoid,
    so it is derived from the perturbation and written down here.
    """

    def holds(self, baseline: int, observed: int) -> bool:
        return abs(observed - self.expect(baseline)) <= self.slack


def _relabel(scenario: Scenario) -> Scenario:
    """Rename every record, keeping the quantities.

    The names are chosen to REVERSE the sort order, because the interesting
    failures are the ones that depend on enumeration order rather than on
    identity. A rename that happened to preserve order would assert almost
    nothing.
    """
    ids = sorted(scenario.records)
    renamed = {f"item_{len(ids) - i:03d}": scenario.records[old]
               for i, old in enumerate(ids)}
    mapping = {old: f"item_{len(ids) - i:03d}" for i, old in enumerate(ids)}
    return Scenario(
        id=f"{scenario.id}~relabel",
        records=renamed,
        audited=[mapping[a] for a in scenario.audited_ids
                 if a in mapping] if scenario.audited else [],
        expected=scenario.expected,
    )


def _scale(scenario: Scenario) -> Scenario:
    return Scenario(
        id=f"{scenario.id}~scale",
        records={k: v * SCALE_FACTOR for k, v in scenario.records.items()},
        audited=list(scenario.audited),
        expected=(None if scenario.expected is None
                  else scenario.expected * SCALE_FACTOR),
    )


def _split(scenario: Scenario) -> Scenario:
    """Split the largest record in two. The total is unchanged; the number
    of records is not, which is what makes it bite."""
    if not scenario.records:
        return scenario
    biggest = max(scenario.records, key=lambda k: scenario.records[k])
    value = scenario.records[biggest]
    half, rest = value // 2, value - value // 2
    records = dict(scenario.records)
    records[biggest] = half
    records[f"{biggest}b"] = rest
    audited = list(scenario.audited)
    if scenario.audited and biggest in scenario.audited:
        audited.append(f"{biggest}b")
    return Scenario(id=f"{scenario.id}~split", records=records,
                    audited=audited, expected=scenario.expected)


RELATIONS: dict[str, Relation] = {
    "relabel": Relation(
        name="relabel",
        asks="renaming every record leaves the total unchanged",
        perturb=_relabel,
        expect=lambda baseline: baseline,
        catches="an answer that depends on what the records are called, or "
                "on the order a tool happens to enumerate them in",
    ),
    "scale": Relation(
        name="scale",
        asks=f"multiplying every quantity by {SCALE_FACTOR} multiplies the "
             f"total by {SCALE_FACTOR}",
        perturb=_scale,
        expect=lambda baseline: baseline * SCALE_FACTOR,
        catches="truncation, a cap, or an answer that stopped being a sum",
        slack=SCALE_FACTOR - 1,
    ),
    "split": Relation(
        name="split",
        asks="splitting one record into two leaves the total unchanged",
        perturb=_split,
        expect=lambda baseline: baseline,
        catches="an answer that depends on how many records there are "
                "rather than on what is in them",
    ),
}
"""A starter library, deliberately domain-agnostic.

Every one of these holds for any task whose answer aggregates a collection,
which is the shape every bundled fixture has. They are not universal: a task
that answers "how many records are there" violates `split` by being correct,
which is why a task declares the relations it accepts rather than inheriting
all of them.

Absent on purpose: **rephrase the question**. It is the most universal
relation there is and it cannot be checked here -- offline policies never
read the statement, so the check would pass vacuously and report a property
of the test doubles as a property of the agents. It needs the live path.
"""


@dataclass
class RelationOutcome:
    """One relation against one variant."""

    variant_id: str
    relation: str
    baseline: Optional[int]
    observed: Optional[int]
    wanted: Optional[int]
    runs: int = 0
    held: int = 0

    @property
    def decidable(self) -> bool:
        """A run that produced no answer cannot satisfy or violate anything.

        Censored exactly like unreachable evidence: `satisfied` is None, and
        the report prints n/a rather than counting it either way.
        """
        return self.baseline is not None and self.observed is not None

    @property
    def satisfied(self) -> Optional[bool]:
        if not self.decidable:
            return None
        return self.held == self.runs and self.runs > 0


def relations_for(task: TaskSpec) -> list[Relation]:
    """The relations this task accepts.

    A task declares them, like everything else here: a relation that does
    not hold for a task is not a finding about the agent, and "the answer
    must never change" applied blindly is a false-failure machine.
    """
    names = getattr(task, "relations", None) or list(RELATIONS)
    return [RELATIONS[n] for n in names if n in RELATIONS]


def check(
    *,
    task: TaskSpec,
    variants: Sequence[VariantSpec],
    execute,
    run_once,
    repeats: int = 1,
    scenario: Optional[Scenario] = None,
) -> list[RelationOutcome]:
    """Run each variant unperturbed, then once per relation, and compare.

    `run_once(variant, scenario, seed)` does the actual execution, which
    keeps this module ignorant of run contexts, fault schedules and the
    live path -- it is about the comparison and nothing else.
    """
    base_scenario = scenario or task.scenarios[0]
    out: list[RelationOutcome] = []
    for variant in variants:
        tally: dict[str, RelationOutcome] = {}
        for i in range(max(1, repeats)):
            # The SAME seed on both sides, which is the whole trick and the
            # same one the counterfactual pair uses: a perturbed run drawn
            # against a different seed differs for two reasons at once, and
            # the agent's own variance gets read as a broken relation. The
            # first version of this used a different seed per side and
            # reported every config as violating `relabel` -- the deltas
            # were miscounts, not relabelling.
            seed = f"mr:{i}"
            baseline = run_once(variant, base_scenario, seed)
            for relation in relations_for(task):
                got = run_once(variant, relation.perturb(base_scenario), seed)
                o = tally.get(relation.name)
                if o is None:
                    o = RelationOutcome(
                        variant_id=variant.id, relation=relation.name,
                        baseline=baseline, observed=got,
                        wanted=(None if baseline is None
                                else relation.expect(baseline)))
                    tally[relation.name] = o
                o.runs += 1
                if baseline is None or got is None:
                    continue
                o.observed, o.baseline = got, baseline
                o.wanted = relation.expect(baseline)
                if relation.holds(baseline, got):
                    o.held += 1
        out.extend(tally[r.name] for r in relations_for(task) if r.name in tally)
    return out


def clean_runner(task: TaskSpec, executor=None):
    """A `run_once` that executes a variant against a scenario, cleanly.

    No fault schedule: a relation is about the agent's own behaviour under
    a change to the world, and injecting a lie on top would make a
    violation unattributable between the two.

    Named for the fault schedule, not the executor. It was `offline_runner`
    until relations reached the board (#43), where passing the LIVE executor
    is the whole point -- a live matrix whose relations were quietly scored
    against scripted policies would be the fail-green shape again, with the
    one number that survives a missing oracle as the thing being faked.
    """
    from .faults import FaultSchedule
    from .interpose import run_context
    from .matrix import _allowed_tools, offline_executor

    execute = executor or offline_executor

    def run_once(variant: VariantSpec, scenario: Scenario, seed: str):
        with run_context(
            scenario.records, FaultSchedule.clean(seed),
            _allowed_tools(variant), scenario.audited_ids,
        ) as ctx:
            ctx.run_label = f"{variant.id}|{scenario.id}|0|relation"
            try:
                answer = execute(variant, seed)
            except Exception:
                # Censored, not failed -- the same rule the matrix uses. A
                # run that never answered is not a run that answered wrong.
                return None
        if isinstance(answer, tuple):
            answer = answer[0]
        return None if answer is None else answer.total

    return run_once


def coverage(outcomes: Sequence[RelationOutcome]) -> dict[str, tuple[int, int]]:
    """Per variant: relations satisfied, out of relations *decided*.

    The denominator excludes the undecidable ones rather than counting them
    against the config. "7/9" where two could not be decided is a different
    statement from "7/9" where two were violated, and only one of them is
    about the agent.
    """
    tally: dict[str, tuple[int, int]] = {}
    for o in outcomes:
        if o.satisfied is None:
            continue
        held, total = tally.get(o.variant_id, (0, 0))
        tally[o.variant_id] = (held + int(o.satisfied), total + 1)
    return tally
