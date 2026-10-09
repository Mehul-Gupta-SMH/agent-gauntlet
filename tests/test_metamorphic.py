"""Scoring a task nobody knows the answer to (#28).

Every other number this project prints compares an answer against
something the harness knows. That works on the bundled fixtures and stops
working on most real work -- which is exactly where a judge would take
over and become the noise floor (#2).

Metamorphic relations dissolve part of that. Do not assert on the output;
assert on the relation between two outputs when the input is perturbed in
a way whose effect is known. Rename every record and the total must not
move. None of it needs the right answer, and -- because the answers
compared here are integers -- none of it needs a similarity threshold,
which would be a judged quantity smuggled back in.

The headline claim, and the one worth protecting: the structurally
degraded sentinel is caught by these checks **with no oracle at all**.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_gauntlet import TaskSpec, architect, metamorphic
from agent_gauntlet.metamorphic import RELATIONS, SCALE_FACTOR
from agent_gauntlet.spec import Scenario

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "task.yaml"


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


@pytest.fixture
def variants(task, tmp_path):
    return architect.generate(out_dir=tmp_path / "v", task=task,
                              models={"cheap": "m", "smart": "s"})


def _check(task, variants, repeats=3):
    return metamorphic.check(
        task=task, variants=variants, execute=None,
        run_once=metamorphic.clean_runner(task), repeats=repeats)


# --- the perturbations are what they say --------------------------------


def test_relabel_keeps_every_quantity_and_changes_every_name():
    s = Scenario(id="s", records={"a": 5, "b": 9, "c": 2}, audited=["a"])
    p = RELATIONS["relabel"].perturb(s)
    assert sorted(p.records.values()) == sorted(s.records.values())
    assert set(p.records) & set(s.records) == set()
    assert p.expected_total == s.expected_total
    # The audit coverage travels with the record it covered, or the
    # perturbation would be changing two things at once.
    assert len(p.audited) == len(s.audited)


def test_relabel_reverses_the_sort_order():
    """The failures worth catching depend on enumeration order, and a
    rename that preserved order would assert almost nothing."""
    s = Scenario(id="s", records={"a": 1, "b": 2, "c": 3})
    p = RELATIONS["relabel"].perturb(s)
    before = [s.records[k] for k in sorted(s.records)]
    after = [p.records[k] for k in sorted(p.records)]
    assert after == list(reversed(before))


def test_scale_multiplies_the_world_and_the_expectation():
    s = Scenario(id="s", records={"a": 5, "b": 9})
    p = RELATIONS["scale"].perturb(s)
    assert p.expected_total == s.expected_total * SCALE_FACTOR
    assert RELATIONS["scale"].expect(14) == 14 * SCALE_FACTOR


def test_split_keeps_the_total_and_changes_the_count():
    s = Scenario(id="s", records={"a": 5, "b": 10})
    p = RELATIONS["split"].perturb(s)
    assert p.expected_total == s.expected_total
    assert len(p.records) == len(s.records) + 1


# --- the headline: caught without an oracle -----------------------------


def test_the_sentinel_is_caught_with_no_known_answer(task, variants):
    """`records-partial` can enumerate half the records, so its answer
    depends on which half -- and both `relabel` and `split` change that.

    Nothing here consulted `expected`. This is the whole argument of #28:
    a config can be shown to be wrong on a task where nobody knows what
    right is.
    """
    outcomes = _check(task, variants)
    by = {(o.variant_id, o.relation): o for o in outcomes}
    sentinel = next(v.id for v in variants if v.is_sentinel)

    assert by[(sentinel, "relabel")].satisfied is False
    assert by[(sentinel, "split")].satisfied is False
    # And `scale` does NOT fire on it, correctly: the sentinel sees the same
    # half of the records in both worlds, so its undercount is proportional.
    # A relation that flagged everything would be worth nothing.
    assert by[(sentinel, "scale")].satisfied is True

    held, total = metamorphic.coverage(outcomes)[sentinel]
    assert held < total

    for v in variants:
        if v.is_sentinel:
            continue
        assert metamorphic.coverage(outcomes)[v.id][0] == \
            metamorphic.coverage(outcomes)[v.id][1], v.id


def test_the_pair_shares_a_seed(task, variants):
    """The defect the first version shipped with.

    Base and perturbed runs drawn against different seeds differ for two
    reasons at once, and the policy's own miscount gets reported as a
    broken relation -- every config came out violating `relabel`. The
    counterfactual pair shares a seed for exactly this reason, and so does
    this.
    """
    seeds: list[str] = []
    real = metamorphic.clean_runner(task)

    def spy(variant, scenario, seed):
        seeds.append(seed)
        return real(variant, scenario, seed)

    metamorphic.check(task=task, variants=variants[:1], execute=None,
                      run_once=spy, repeats=2)
    # One base plus one per relation, per repeat -- all on the same seed.
    per_repeat = 1 + len(metamorphic.relations_for(task))
    assert seeds[:per_repeat] == [seeds[0]] * per_repeat
    assert seeds[per_repeat] != seeds[0], "a second repeat is a second seed"


def test_a_slip_prone_policy_still_satisfies_invariance(task, variants):
    """The same point from the other side. `cheap` slips far more often
    than `smart`; sharing the seed means the slip lands identically on both
    sides of the pair and cancels, so noise does not read as a violation.
    """
    cheap = [v for v in variants
             if v.factors.get("model") == "cheap" and not v.is_sentinel]
    outcomes = _check(task, cheap, repeats=6)
    assert all(o.satisfied for o in outcomes), \
        [(o.variant_id, o.relation, o.baseline, o.observed) for o in outcomes
         if not o.satisfied]


# --- censoring, as everywhere else --------------------------------------


def test_a_run_that_never_answered_decides_nothing(task, variants):
    """Undecidable, not violated, and not satisfied. A relation compared
    against a missing answer is the same censoring case as unreachable
    evidence."""
    outcomes = metamorphic.check(
        task=task, variants=variants[:1], execute=None,
        run_once=lambda v, s, seed: None, repeats=2)
    assert outcomes
    for o in outcomes:
        assert o.satisfied is None
        assert not o.decidable


def test_undecidable_relations_leave_the_denominator(task, variants):
    """"7/9 with two undecided" and "7/9 with two violated" are different
    statements, and only one of them is about the agent."""
    outcomes = metamorphic.check(
        task=task, variants=variants[:1], execute=None,
        run_once=lambda v, s, seed: None, repeats=1)
    assert metamorphic.coverage(outcomes) == {}


# --- the task declares what holds ---------------------------------------


def test_a_task_declares_which_relations_apply(task):
    """A relation that does not hold for a task is not a finding about the
    agent: a task answering "how many records are there" violates `split`
    by being correct."""
    assert [r.name for r in metamorphic.relations_for(task)] == sorted(RELATIONS)

    narrowed = task.model_copy(update={"relations": ["scale"]})
    assert [r.name for r in metamorphic.relations_for(narrowed)] == ["scale"]


def test_declaring_relations_moves_the_fingerprint_only_when_set(task):
    """Pre-registration, rule 4: the field joins the payload only when set,
    so every existing hash stands."""
    assert task.fingerprint() == "5c9848747223eaa4"
    narrowed = task.model_copy(update={"relations": ["scale"]})
    assert narrowed.fingerprint() != task.fingerprint()


def test_rephrase_invariance_is_absent_on_purpose():
    """The most universal relation there is, and unavailable here: offline
    policies never read the statement, so the check would pass vacuously
    and report a property of the test doubles as one of the agents."""
    assert "rephrase" not in RELATIONS
    source = Path(metamorphic.__file__).read_text(encoding="utf-8")
    assert "rephrase the question" in source.lower()
    assert "vacuously" in source


def test_slack_exists_only_where_the_comparison_rounds():
    """A threshold chosen to make a result pass would be the judged
    quantity this whole module avoids. `scale` multiplies an integer answer
    and compares it against one computed in a larger world; nothing else
    here changes any arithmetic at all."""
    assert RELATIONS["relabel"].slack == 0
    assert RELATIONS["split"].slack == 0
    assert RELATIONS["scale"].slack == SCALE_FACTOR - 1
    assert RELATIONS["relabel"].holds(100, 100)
    assert not RELATIONS["relabel"].holds(100, 101)
    assert RELATIONS["scale"].holds(100, 300 - (SCALE_FACTOR - 1))
    assert not RELATIONS["scale"].holds(100, 300 - SCALE_FACTOR)


# --- on the board (#43) --------------------------------------------------


def test_the_block_says_n_a_when_the_relations_were_not_run(task, variants, capsys):
    """An opt-in cost, and the censoring rule applied to the obvious place
    it would be broken: a relation nobody ran is not a relation that held."""
    from agent_gauntlet.cli import _print_relations

    _print_relations(task, variants, None, enabled=False, live=False)
    out = capsys.readouterr().out
    assert "n/a for every config" in out
    assert "--with-relations" in out
    assert "VIOLATED" not in out
    assert not [l for l in out.splitlines() if l.split()[-1:] == ["ok"]], \
        "an unrun relation must not render as one that held"


def test_the_block_names_the_cost_before_it_is_spent(task, variants, capsys):
    """k relations is a second full run of every variant, k+1 times over.
    An operator deciding whether to pay needs the number, not the ratio."""
    from agent_gauntlet.cli import _print_relations

    _print_relations(task, variants, None, enabled=False, live=True)
    out = capsys.readouterr().out
    expected = (len(metamorphic.relations_for(task)) + 1) * len(variants)
    assert f"{expected} extra runs" in out
    assert "costs money" in out


def test_the_board_block_catches_the_sentinel_with_no_oracle(task, variants, capsys):
    """The headline claim from #28, now where people actually look."""
    from agent_gauntlet.cli import _print_relations

    _print_relations(task, variants, None, enabled=True, live=False)
    out = capsys.readouterr().out
    assert "VIOLATED" in out
    sentinel = next(v for v in variants if v.is_sentinel)
    label = " ".join(str(sentinel.factors[k]) for k in sorted(sentinel.factors))
    breaking = [l for l in out.splitlines() if "VIOLATED" in l]
    assert all(label[:20] in l for l in breaking), breaking


def test_the_board_block_never_averages_the_relations(task, variants, capsys):
    """`7/9` across relations that measure different properties is the
    composite-scalar mistake (#37 part 4). Which relation broke is the
    finding; how many did is not."""
    from agent_gauntlet.cli import _print_relations

    _print_relations(task, variants, None, enabled=True, live=False)
    out = capsys.readouterr().out
    assert "kept" not in out
    for n in range(1, 5):
        assert f"{n}/3" not in out


def test_a_live_run_scores_its_relations_live(task, variants):
    """The fail-green this feature could most easily have shipped: a live
    matrix whose relations were quietly scored against scripted policies,
    with the one number that survives a missing oracle as the thing being
    faked. The executor handed in has to be the executor used.
    """
    from agent_gauntlet.cli import _print_relations
    from agent_gauntlet.score import Answer

    calls: list = []

    def fake_executor(variant, seed):
        calls.append((variant.id, seed))
        return Answer(total=1, steps=1)

    _print_relations(task, variants[:1], fake_executor, enabled=True, live=True)
    assert calls, "the supplied executor was never called"
    assert {c[0] for c in calls} == {variants[0].id}


# --- a relation is only defaulted where it is provable (#15) --------------


def test_the_default_applies_only_to_a_task_whose_answer_is_the_sum():
    """`relations_for`'s docstring said a task declares its relations, and
    the code under it defaulted to all three for any task that declared
    none -- which was every bundled fixture. So every relation result this
    project reported came from a set no task had claimed.

    It now defaults only where the default is provable: when no scenario
    sets `expected`, the answer IS `sum(records.values())` by definition and
    all three built-ins follow from that.
    """
    summing = TaskSpec.from_yaml(FIXTURE)
    assert all(s.expected is None for s in summing.scenarios)
    assert metamorphic.defaultable(summing)
    assert not metamorphic.declared(summing)
    assert len(metamorphic.relations_for(summing)) == 3

    # One scenario supplying its own answer is enough: the harness no longer
    # knows what the answer is a function of.
    other = summing.model_copy(update={
        "scenarios": [summing.scenarios[0].model_copy(update={"expected": 1})]
                     + list(summing.scenarios[1:])})
    assert not metamorphic.defaultable(other)
    assert metamorphic.relations_for(other) == []


def test_a_counting_task_is_not_failed_by_relations_it_never_declared():
    """The demonstration, not the deduction.

    A task whose answer is a COUNT of records is answered correctly by an
    agent that counts them -- and under the old default `scale` demanded
    the count triple when every quantity tripled, and `split` demanded it
    stay put when a record was split in two. Two confident false failures
    against a correct agent.
    """
    base = TaskSpec.from_yaml(FIXTURE)
    counting = base.model_copy(update={
        "id": "record_count",
        "scenarios": [s.model_copy(update={"expected": len(s.records)})
                      for s in base.scenarios],
    })
    assert metamorphic.relations_for(counting) == [], (
        "the harness cannot prove a relation about a count, so it must claim "
        "none"
    )


def test_relabel_is_excluded_from_the_subset_fixture():
    """It destroys the marker the answer depends on.

    `discontinued.yaml`'s answer is the stocked subset, keyed by an id
    suffix. `relabel` renames every record, so the suffix goes and the
    stocked total of `mixed` moves 128 -> 172. A config correctly reporting
    172 for the relabelled world was being marked VIOLATED for it.
    """
    from agent_gauntlet.offline import DISCONTINUED

    task = TaskSpec.from_yaml(
        FIXTURE.parent / "discontinued.yaml")
    assert metamorphic.declared(task)
    assert [r.name for r in metamorphic.relations_for(task)] == ["scale", "split"]

    scenario = task.scenarios[0]
    relabelled = RELATIONS["relabel"].perturb(scenario)
    stocked = sum(v for k, v in relabelled.records.items()
                  if not k.endswith(DISCONTINUED))
    assert stocked != scenario.expected_total, (
        "if relabel ever preserves the marker this exclusion can be revisited"
    )


def test_the_two_declared_relations_really_hold(tmp_path):
    """Declaring a relation that does not hold is the same defect pointing
    the other way, so the two on the subset fixture are checked rather than
    asserted."""
    task = TaskSpec.from_yaml(FIXTURE.parent / "discontinued.yaml")
    variants = architect.generate(out_dir=tmp_path / "v", task=task,
                                  models={"cheap": "m", "smart": "s"})
    best = next(v for v in variants
                if v.factors.get("prompt") == "reconciling"
                and v.factors.get("toolset") == "records+summary")
    outcomes = metamorphic.check(
        task=task, variants=[best], execute=None,
        run_once=metamorphic.clean_runner(task), repeats=3)
    assert outcomes
    for outcome in outcomes:
        assert outcome.satisfied is True, f"{outcome.relation} did not hold"


# --- source dependence: the relation that perturbs the grant (#41) ----------


def _grid(task, tmp_path, **kw):
    from agent_gauntlet import architect

    return architect.generate(
        out_dir=tmp_path / "v", task=task,
        models={"cheap": "anthropic/claude-haiku-4-5"}, **kw)


def test_a_policy_that_cannot_enumerate_declines_instead_of_reporting_zero(
        tmp_path):
    """The defect #41's check found on its way in, and the whole payoff.

    `_enumerate` returned `[]` both for "there are no records" and for "I
    was never granted a way to look". The policies summed over nothing and
    answered `total=0`.

    Reachable with no contrivance: `--toolsets records summary` passes the
    reachability guard because `records` grants the faulted tool, and the
    `summary` variant then holds `get_summary` and no enumeration at all.
    It reported 0 against a truth of 128, and the board scored that as a
    WRONG ANSWER -- merging two outcomes the taxonomy deliberately splits,
    a config that answered incorrectly and one that could not answer.

    This project's first rule, broken inside its own test doubles, and
    presenting as a well-formed confident figure.
    """
    from agent_gauntlet import offline
    from agent_gauntlet.faults import FaultSchedule
    from agent_gauntlet.interpose import run_context
    from agent_gauntlet.matrix import _allowed_tools
    from agent_gauntlet.spec import TaskSpec

    task = TaskSpec.from_yaml("fixtures/inventory/audited.yaml")
    scenario = task.scenarios[0]
    variants = _grid(task, tmp_path, toolsets=["records", "summary"],
                     prompts=["naive"], include_sentinel=False)
    answers = {}
    for v in variants:
        with run_context(scenario.records, FaultSchedule.clean("s"),
                         _allowed_tools(v), scenario.audited_ids):
            answers[v.factors["toolset"]] = offline.naive("s").total

    assert answers["records"] is not None, "it can enumerate, so it answers"
    assert answers["summary"] is None, (
        "a policy with no enumeration tool reported a number it could not "
        "have computed"
    )


def test_an_empty_world_is_still_a_total_of_zero(tmp_path):
    """The other half of the distinction, which the fix must not break.

    `[]` from a granted enumeration means the world is empty, and 0 is then
    the right answer rather than a censored one. Only an ABSENT enumeration
    tool declines.
    """
    from agent_gauntlet import offline
    from agent_gauntlet.faults import FaultSchedule
    from agent_gauntlet.interpose import run_context
    from agent_gauntlet.spec import TaskSpec

    task = TaskSpec.from_yaml("fixtures/inventory/audited.yaml")
    with run_context({}, FaultSchedule.clean("s"),
                     {"list_records", "fetch_record"}, []):
        assert offline.naive("s").total == 0


def test_only_a_load_bearing_tool_is_withheld(tmp_path):
    """#41's assertion, applied to any tool the agent called, false-fails.

    `get_summary` is a cross-check: the clean total was always obtainable
    from `fetch_record`, and the summary covers only the audited subset. A
    config whose answer is unchanged without it is not defective, it is
    holding a redundant source -- so the summary is never withheld.
    """
    from agent_gauntlet.spec import TaskSpec

    task = TaskSpec.from_yaml("fixtures/inventory/audited.yaml")
    variants = _grid(task, tmp_path, toolsets=["records+summary"],
                     prompts=["verifying"], include_sentinel=False)
    outcomes = metamorphic.check_sources(
        task=task, variants=variants,
        run_once=metamorphic.source_runner(task))

    withheld = {o.tool for o in outcomes}
    assert "get_summary" not in withheld
    assert "list_audited_records" not in withheld
    assert withheld <= set(metamorphic.LOAD_BEARING)
    assert withheld, "and the necessary ones ARE withheld"
    for tool in withheld:
        assert metamorphic.LOAD_BEARING[tool], "each one states why"


def test_dependence_cannot_fail_offline_and_the_report_says_so(tmp_path):
    """Pinned as a fact, not an aspiration.

    Every scripted policy reads its tools honestly, so withholding a
    necessary one always moves the number. A block reporting 17/17 without
    saying it could not have reported anything else is the degenerate pass
    #41's own table warns about for the statement-perturbing relations.
    """
    from agent_gauntlet.spec import TaskSpec

    task = TaskSpec.from_yaml("fixtures/inventory/audited.yaml")
    variants = _grid(task, tmp_path)
    outcomes = metamorphic.check_sources(
        task=task, variants=variants,
        run_once=metamorphic.source_runner(task))

    decided = [o for o in outcomes if o.decidable]
    assert decided
    assert all(o.depended for o in decided), (
        "if this ever fails offline, a scripted policy has stopped reading "
        "its tools honestly -- which is a harness defect, not a finding"
    )
    assert not any(o.substituted for o in decided)


def test_substitution_is_distinguished_from_mere_change():
    """Declining and answering-wrongly are not the same outcome.

    Dependence is satisfied by both, which is why it is the weak property.
    The one that discriminates is whether a config answered anyway from a
    source that could not support the answer.
    """
    declined = metamorphic.SourceOutcome(
        variant_id="v", tool="fetch_record", baseline=128, without=None,
        declined=True, expected=128)
    answered_wrong = metamorphic.SourceOutcome(
        variant_id="v", tool="list_records", baseline=128, without=95,
        declined=False, expected=128)
    answered_right = metamorphic.SourceOutcome(
        variant_id="v", tool="list_records", baseline=128, without=128,
        declined=False, expected=128)

    assert declined.depended and not declined.substituted
    assert answered_wrong.depended and answered_wrong.substituted
    # The one real violation of dependence: same number, no decline.
    assert answered_right.depended is False

    # No oracle means "changed" and "changed to something wrong" are the
    # same observation, so substitution is not decided at all.
    blind = metamorphic.SourceOutcome(
        variant_id="v", tool="list_records", baseline=128, without=95,
        declined=False, expected=None)
    assert blind.substituted is None
    assert blind.depended is True


# --- does a relation gate? #43's open point 3 -------------------------------


def _outcome(**kw):
    base = dict(variant_id="v", relation="relabel", baseline=100,
                observed=100, wanted=100, runs=1, held=1)
    base.update(kw)
    return metamorphic.RelationOutcome(**base)


def test_a_violation_seen_once_does_not_gate():
    """#43 point 3, settled by measurement rather than by nerve.

    #27's rule makes a relation gate-shaped: it is a binary property, so a
    violation is like propagation rather than like a quality drop. The rule
    is necessary and not sufficient -- the board runs ONE pair per
    relation, and one violated pair on a live model cannot be told from
    variance. #37's whole point.

    The exit code used to be `3 if violated else 0`, which gated on exactly
    that single observation.
    """
    once = _outcome(runs=1, held=0)
    assert once.satisfied is False, "it is a violation"
    assert not once.gates, "and it is not a property yet"
    assert metamorphic.gating([once]) == {}
    assert metamorphic.ungated_violations([once]) == {"v": ["relabel"]}


def test_a_violation_reproduced_gates():
    twice = _outcome(runs=2, held=0)
    assert twice.gates
    assert metamorphic.gating([twice]) == {"v": ["relabel"]}
    assert metamorphic.ungated_violations([twice]) == {}


def test_a_violation_that_sometimes_holds_is_variance_not_a_property():
    """Held once in three is the flake case, and it must not gate.

    This is where #27's inversion bites: CI retries a flaky test until
    green, and here flakiness IS the measurement. A config that breaks
    `relabel` one time in three has a number, not a property, and gating
    on it would delete the number.
    """
    flaky = _outcome(runs=3, held=1, observed=90)
    assert flaky.satisfied is False
    assert not flaky.gates
    assert metamorphic.ungated_violations([flaky]) == {"v": ["relabel"]}


def test_an_undecidable_relation_never_gates():
    """A run that produced no answer satisfies and violates nothing."""
    censored = _outcome(runs=2, held=0, observed=None)
    assert censored.decidable is False
    assert censored.satisfied is None
    assert not censored.gates
    assert metamorphic.gating([censored]) == {}
    # And it is not an ungated violation either -- it is not a violation.
    assert metamorphic.ungated_violations([censored]) == {}


def test_gating_and_ungated_are_disjoint_and_cover_the_violations():
    outcomes = [
        _outcome(variant_id="gated", runs=3, held=0),
        _outcome(variant_id="flaky", runs=3, held=2),
        _outcome(variant_id="once", runs=1, held=0),
        _outcome(variant_id="clean", runs=3, held=3),
    ]
    gated = metamorphic.gating(outcomes)
    ungated = metamorphic.ungated_violations(outcomes)
    assert set(gated) == {"gated"}
    assert set(ungated) == {"flaky", "once"}
    assert not set(gated) & set(ungated)
    violations = {o.variant_id for o in outcomes if o.satisfied is False}
    assert violations == set(gated) | set(ungated)
    assert "clean" not in violations


def test_the_sentinel_is_what_a_relation_catches(tmp_path):
    """An instrument check that needs no oracle at all.

    `records-partial` enumerates only a sample, so relabelling the records
    changes which half it sees. It violates `relabel` on every pair --
    which is the structural sentinel being caught by a check that never
    consults a known answer, and the strongest case for relations on an
    unlabelled task (#43).
    """
    from agent_gauntlet import architect
    from agent_gauntlet.spec import TaskSpec

    task = TaskSpec.from_yaml("fixtures/inventory/audited.yaml")
    variants = architect.generate(
        out_dir=tmp_path / "v", task=task,
        models={"cheap": "anthropic/claude-haiku-4-5"})
    outcomes = metamorphic.check(
        task=task, variants=variants, execute=None,
        run_once=metamorphic.clean_runner(task), repeats=3)

    gated = metamorphic.gating(outcomes)
    sentinels = [v.id for v in variants if v.is_sentinel]
    assert sentinels
    assert set(gated) == set(sentinels), (
        "a relation caught something other than the sentinel, or missed it"
    )
    assert "relabel" in gated[sentinels[0]]
