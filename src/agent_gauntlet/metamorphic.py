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

from .spec import Provenance, Scenario, TaskSpec, VariantSpec

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


# Every derived world below is stamped SYNTHETIC, whatever its parent was
# (#15). A perturbation CONSTRUCTS a world: `scale` triples quantities no
# trace contains, `split` invents a record id. Inheriting a parent's `trace`
# provenance would let a relation result be read as production evidence
# about inputs production never produced -- the code that invents a world is
# the right place to record that it invented it.
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
        provenance=Provenance.SYNTHETIC,
    )


def _scale(scenario: Scenario) -> Scenario:
    return Scenario(
        id=f"{scenario.id}~scale",
        records={k: v * SCALE_FACTOR for k, v in scenario.records.items()},
        audited=list(scenario.audited),
        expected=(None if scenario.expected is None
                  else scenario.expected * SCALE_FACTOR),
        provenance=Provenance.SYNTHETIC,
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
                    audited=audited, expected=scenario.expected,
                    provenance=Provenance.SYNTHETIC)


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


MIN_RUNS_TO_GATE = 2
"""How many pairs a violation needs before it blocks a config (#43).

Two, because one violated pair cannot be distinguished from variance and
the board runs relations at `repeats=1`. Raising it costs a full extra run
of every variant per relation, which is why the board does not pay it by
default and `gauntlet relations --repeats 3` is the gating path.
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

    @property
    def gates(self) -> bool:
        """Does this violation block the config, or only get reported? (#43)

        #27's rule says gate on binary properties and report on statistical
        ones, and a relation is binary -- so by that rule a violation is
        gate-shaped, like propagation rather than like a quality drop.

        The rule is necessary and not sufficient. The board runs relations
        at `repeats=1`, and ONE violated pair on a live model cannot be
        told from variance; #37's whole point is that one measurement is
        not a property. So a violation gates only when it was violated
        EVERY time it was tried, at least twice -- `held == 0` with
        `runs >= MIN_RUNS_TO_GATE`.

        At n=1 it is reported and explicitly not gated. That is not
        timidity: gating there would make `--with-relations` reorder a
        board on a single observation, which is the error this project
        spends most of its code preventing.
        """
        return (self.decidable and self.runs >= MIN_RUNS_TO_GATE
                and self.held == 0)


def declared(task: TaskSpec) -> bool:
    """Did the task name its own relations, or is it taking the default?

    Report sites need the difference. "This config satisfies three
    relations" and "this config satisfies three relations nobody claimed
    hold for this task" are different statements, and only the first is
    evidence.
    """
    return bool(getattr(task, "relations", None))


def defaultable(task: TaskSpec) -> bool:
    """Can the built-in relations be assumed to hold for this task?

    Only when no scenario sets `expected`. Then the answer IS
    `sum(records.values())` by definition -- `Scenario.expected_total` says
    so -- and all three built-ins provably follow from that: renaming keys
    does not change a sum of values, scaling every value scales the sum,
    and splitting one value in two leaves it unchanged.

    The moment a scenario supplies its own `expected`, the answer is
    something else and the harness has no idea what. It cannot prove any
    relation, so it claims none.
    """
    return all(s.expected is None for s in task.scenarios)


def relations_for(task: TaskSpec) -> list[Relation]:
    """The relations this task accepts.

    A task declares them, like everything else here: a relation that does
    not hold for a task is not a finding about the agent, and "the answer
    must never change" applied blindly is a false-failure machine.

    That docstring was true and the code under it was not. It defaulted to
    ALL THREE whenever a task declared none -- which every bundled fixture
    did -- so every relation result this project has ever reported came
    from a set no task had claimed. Demonstrated rather than deduced: a
    correct agent on a task whose answer is a COUNT of records gets
    `scale` and `split` marked VIOLATED, because tripling every quantity
    does not triple a count and splitting a record does change one. And on
    `discontinued.yaml`, whose answer is a subset sum keyed by an id
    suffix, `relabel` renames the keys and destroys the marker: the stocked
    total moves 128 -> 172 and a correct config is failed for it.

    So the default now applies only where it is provable -- see
    `defaultable` -- and otherwise the task gets nothing until it says what
    holds. Conservative in the direction that matters: a missing relation
    measures less, a wrong one reports a failure that did not happen.
    """
    names = getattr(task, "relations", None)
    if not names:
        names = list(RELATIONS) if defaultable(task) else []
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


def gating(outcomes: Sequence[RelationOutcome]) -> dict[str, list[str]]:
    """Variants blocked by a reproduced relation violation -> which relations.

    Empty when nothing was violated, and ALSO empty when every violation
    was seen once. Those are different facts and the report says which;
    this function answers only "what blocks", because a caller that
    conflates them would gate on an observation.
    """
    out: dict[str, list[str]] = {}
    for o in outcomes:
        if o.gates:
            out.setdefault(o.variant_id, []).append(o.relation)
    return {k: sorted(v) for k, v in sorted(out.items())}


def ungated_violations(
    outcomes: Sequence[RelationOutcome],
) -> dict[str, list[str]]:
    """Violations seen, but not enough times to be a property (#37, #43).

    Reported separately rather than merged into `gating`, because "violated
    once in one try" and "violated twice in two" are different claims and
    only the second is a property of the config.
    """
    out: dict[str, list[str]] = {}
    for o in outcomes:
        if o.satisfied is False and not o.gates:
            out.setdefault(o.variant_id, []).append(o.relation)
    return {k: sorted(v) for k, v in sorted(out.items())}


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


# --- source dependence: the relation that perturbs the GRANT (#41) ---------
#
# #41 calls this "the one that is nearly free": a tool-grant perturbation
# rather than a statement one, so it is checkable offline today. Its
# proposed assertion is "the answer either changes or the agent says it
# cannot answer" -- and that, applied to any tool the agent happened to
# call, is a false-failure machine.
#
# A config holding `records+summary` calls `get_summary` as a cross-check.
# Withhold it and the clean answer is unchanged, CORRECTLY: the total was
# always obtainable from `fetch_record` alone, and the summary covers only
# the audited subset by design. Marking that a violation would report
# redundancy in the tool surface as a defect in the agent -- the same
# mistake `relations_for` made by defaulting all three relations to tasks
# that had declared none.
#
# So the same provability rule applies. A withheld tool is only decidable
# where the information it carries is not otherwise reachable, and that is
# a property of the tool surface, declared here with its reason.

LOAD_BEARING: dict[str, str] = {
    "fetch_record": (
        "the only source of a record's quantity -- `list_records` returns "
        "ids and nothing else, and `get_summary` covers only the audited "
        "subset, so no grant can reconstruct the total without it"
    ),
    "list_records": (
        "the only enumeration -- without it there is no set of ids to fetch, "
        "so an answer that still totals the world was not built from the world"
    ),
    "pull_credit_report": (
        "the only source of the figure the lending task reports, and the "
        "material call the task exists to make expensive"
    ),
}
"""Tools whose information no other granted tool can supply.

Declared, not inferred, and deliberately short. Everything absent from
here is either redundant (`get_summary`, which cross-checks a total
`fetch_record` already yields) or unexamined -- and both are reported as
not checked rather than checked and passed.
"""


@dataclass
class SourceOutcome:
    """One withheld tool against one variant (#41)."""

    variant_id: str
    tool: str
    baseline: Optional[int]
    without: Optional[int]
    declined: bool = False
    """The agent produced no answer once the source was gone. The correct
    behaviour, and not the same as producing a different one."""

    expected: Optional[int] = None
    """The right answer, when the task has one. Not used to decide
    dependence -- that needs no oracle -- but to tell apart the two ways an
    answer can change."""

    @property
    def decidable(self) -> bool:
        """A baseline that never answered decides nothing.

        `without` being None is not undecidable -- it is the *interesting*
        outcome, an agent that stopped rather than guessed.
        """
        return self.baseline is not None

    @property
    def depended(self) -> Optional[bool]:
        """Did the answer actually rest on the source the agent called for?

        True when the answer moved or the agent declined. False when the
        same total came back without the only tool that could have produced
        it -- which is not a style preference: the number was not derived
        from the evidence, so it was remembered, assumed, or invented.
        """
        if not self.decidable:
            return None
        return self.declined or self.without != self.baseline

    @property
    def substituted(self) -> Optional[bool]:
        """Did it answer anyway, from a source that could not support the answer?

        Dependence is the weak property and it is nearly always satisfied:
        every scripted policy reads its tools honestly, so withholding a
        load-bearing one always moves the number. On the bundled fixtures
        the dependence check cannot fail, which makes it exactly the
        degenerate pass #41's own table warns about for the
        statement-perturbing relations -- and saying "3/3 held" about a
        suite that could not fail is the shape this project exists to catch.

        This is the property that does discriminate, and it is measured
        rather than hoped for. Withhold `list_records` from
        verifying/records+summary and it does not decline: it reports the
        AUDITED SUBTOTAL as the total -- 95 where the answer is 128 -- a
        confident wrong number from a source that covers part of the world
        by design. A config that declines instead is strictly better
        behaved, and until now nothing on this board could tell them apart.

        None when the task has no oracle: without a right answer, "changed"
        and "changed to something wrong" are the same observation.
        """
        if not self.decidable or self.expected is None:
            return None
        return not self.declined and self.without != self.expected


def source_runner(task: TaskSpec, executor=None):
    """A `run_once` that also reports which tools the run actually called.

    Separate from `clean_runner` because this relation needs two things
    that one does not: the ability to withhold a tool from the grant, and
    the call list -- #41's check only applies to a source the agent reached
    for in the first place.
    """
    from .faults import FaultSchedule
    from .interpose import run_context
    from .matrix import _allowed_tools, offline_executor

    execute = executor or offline_executor

    def run_once(variant: VariantSpec, scenario: Scenario, seed: str, *,
                 withhold: Sequence[str] = ()):
        granted = set(_allowed_tools(variant)) - set(withhold)
        with run_context(
            scenario.records, FaultSchedule.clean(seed),
            granted, scenario.audited_ids,
        ) as ctx:
            ctx.run_label = f"{variant.id}|{scenario.id}|0|source"
            try:
                answer = execute(variant, seed)
            except Exception:
                return None, frozenset()
            called = frozenset(c.get("tool") for c in ctx.calls if c.get("tool"))
        if isinstance(answer, tuple):
            answer = answer[0]
        return (None if answer is None else answer.total), called

    return run_once


def check_sources(
    *,
    task: TaskSpec,
    variants: Sequence[VariantSpec],
    run_once,
    scenario: Optional[Scenario] = None,
) -> list[SourceOutcome]:
    """Withhold each load-bearing tool the variant called, and see if it mattered.

    One baseline run per variant plus one per withheld tool -- cheaper than
    the world-perturbing relations, which need a baseline per relation.

    The same seed on both sides, for the reason `check` gives: a run drawn
    against a different seed differs for two reasons at once and the
    agent's own variance gets read as independence from its evidence.
    """
    base_scenario = scenario or task.scenarios[0]
    out: list[SourceOutcome] = []
    for variant in variants:
        seed = "src:0"
        baseline, called = run_once(variant, base_scenario, seed)
        for tool in sorted(called & set(LOAD_BEARING)):
            without, _ = run_once(variant, base_scenario, seed,
                                  withhold=[tool])
            out.append(SourceOutcome(
                variant_id=variant.id, tool=tool, baseline=baseline,
                without=without, declined=without is None,
                expected=base_scenario.expected_total,
            ))
    return out


# --- rephrase-invariance: declared, costed, and not scored offline (#41) ----


def paraphrase_pairs(task: TaskSpec) -> list[tuple[str, str]]:
    """The (statement, paraphrase) pairs this task declares.

    Empty for every bundled fixture, which is the honest state: #41's first
    pre-condition is that paraphrases are hand-written and declared, and
    nobody has written any.
    """
    return [(task.statement, text) for text in task.paraphrases]


def paraphrase_cost(task: TaskSpec, variants: Sequence[VariantSpec]) -> int:
    """Extra runs a paraphrase suite would cost, beyond the baseline.

    One run per paraphrase per variant. The baseline is the variant's own
    unperturbed answer, which the relations suite already needs, so this is
    the marginal cost rather than the total.

    It is larger than it looks for a reason worth stating: a paraphrase
    replaces the statement, the statement is written into the variant's
    generated `skill.md`, so each pair needs the variant REGENERATED. That
    also moves `VariantSpec.fingerprint`, which is correct -- the two runs
    are genuinely different variants and the ledger should say so -- but it
    means a paraphrase suite cannot reuse a generated grid.
    """
    return len(task.paraphrases) * len(variants)


def paraphrase_is_vacuous_offline() -> str:
    """Why this family is never scored against scripted policies.

    Stated as a function rather than a comment because the report prints
    it, and the one thing this family must not do is report `4/4` on a
    suite that could not fail.
    """
    return (
        "a scripted policy never reads the task statement, so every "
        "paraphrase passes by construction -- which would report a "
        "property of the test doubles as one of the agents"
    )
