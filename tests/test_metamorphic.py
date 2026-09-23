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
        run_once=metamorphic.offline_runner(task), repeats=repeats)


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
    real = metamorphic.offline_runner(task)

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
