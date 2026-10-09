"""Where a scenario's world came from, and what the board may say about it (#15).

The defect these tests pin is not a wrong number. It is that the board was
*invariant* to provenance: a propagation rate over three inventories lifted
from a month of production traffic and one over three somebody invented
rendered identically, to the digit, and no field existed to tell them apart.

Worse than absent -- an operator who wrote `provenance: trace` in a spec got
a file the harness accepted without complaint, dropped the field, and
fingerprinted as though nothing had been declared. A declaration that looks
accepted and is discarded is the fail-green shape this project keeps
finding in itself.
"""

from __future__ import annotations

import pytest

from agent_gauntlet import ledger as ledger_mod
from agent_gauntlet import metamorphic
from agent_gauntlet.spec import GENERALISING, Provenance, Scenario, TaskSpec

FIXTURES = {
    "fixtures/inventory/task.yaml": "5c9848747223eaa4",
    "fixtures/inventory/audited.yaml": "d11a0b2a6801ee74",
    "fixtures/lending/applicant.yaml": "2f32ad9fcecc025b",
    "fixtures/poisoning/instruction.yaml": "10347b6eeeba5b0d",
    "fixtures/poisoning/memory.yaml": "8f7d94a1568e6696",
}


def _task(**kw) -> TaskSpec:
    base = dict(
        id="t", statement="total it", oracle="full",
        scenarios=[Scenario(id="a", records={"r1": 1})],
    )
    base.update(kw)
    return TaskSpec(**base)


@pytest.mark.parametrize("path,expected", sorted(FIXTURES.items()))
def test_adding_provenance_moved_no_existing_fingerprint(path, expected):
    """The rule from `fingerprint`'s docstring, applied to a sixth field.

    A new field joins the payload only when set. Five of the six bundled
    fixtures declare no provenance, so five hashes must be byte-identical
    to what their run records were written under -- including experiment
    003's, which is the one record the pre-registration argument rests on.
    """
    assert TaskSpec.from_yaml(path).fingerprint() == expected


def test_declaring_provenance_does_move_the_fingerprint():
    """Because it is part of the claim, not a comment about it.

    Upgrading a result's standing to "measured on production traffic" after
    seeing the numbers is exactly the move pre-registration exists to make
    detectable, so the hash has to notice.
    """
    undeclared = _task()
    declared = _task(provenance=Provenance.TRACE)
    assert undeclared.fingerprint() != declared.fingerprint()
    # And the two provenances are distinguishable from each other, not just
    # from silence.
    assert declared.fingerprint() != _task(provenance=Provenance.SYNTHETIC).fingerprint()


def test_a_scenarios_own_declaration_wins_over_the_task_default():
    task = _task(
        provenance=Provenance.TRACE,
        scenarios=[
            Scenario(id="real", records={"r1": 1}),
            Scenario(id="made_up", records={"r1": 1},
                     provenance=Provenance.SYNTHETIC),
        ],
    )
    assert task.scenario_provenance("real") is Provenance.TRACE
    assert task.scenario_provenance("made_up") is Provenance.SYNTHETIC


def test_undeclared_is_none_and_not_a_member():
    """An absent fact must not render as a value.

    Same rule that makes an unmeasured metric print `n/a` rather than `0`.
    A `Provenance.UNSTATED` member would be a value an operator could set
    to look like a declaration, so there isn't one.
    """
    assert _task().scenario_provenance("a") is None
    assert "unstated" not in {p.value for p in Provenance}
    assert "unknown" not in {p.value for p in Provenance}


def test_only_a_sample_generalises():
    assert _task(provenance=Provenance.TRACE).generalises()
    for p in (Provenance.EXAMPLE, Provenance.SYNTHETIC):
        assert not _task(provenance=p).generalises(), p
    assert not _task().generalises()
    assert set(GENERALISING) == {Provenance.TRACE}


def test_one_invented_world_among_traced_ones_stops_generalising():
    """The mixture argument, same as a mixed price table or agent SDK.

    Each number is truthful about its own run and the mean is about a
    mixture, so the aggregate cannot be read as an estimate.
    """
    task = _task(
        provenance=Provenance.TRACE,
        scenarios=[
            Scenario(id="real_1", records={"r1": 1}),
            Scenario(id="real_2", records={"r1": 2}),
            Scenario(id="constructed", records={"r1": 3},
                     provenance=Provenance.SYNTHETIC),
        ],
    )
    assert not task.generalises()
    assert "mixture" in task.scope()
    mix = task.provenance_mix()
    assert mix[Provenance.TRACE] == ["real_1", "real_2"]
    assert mix[Provenance.SYNTHETIC] == ["constructed"]


def test_scope_never_claims_an_undeclared_world_came_from_anywhere():
    scope = _task().scope()
    assert "undeclared" in scope
    for word in ("trace", "sampled", "production"):
        assert word not in scope, word


def test_a_perturbed_world_is_synthetic_whatever_its_parent_was():
    """The code that invents a world records that it invented it.

    `scale` triples quantities no trace contains and `split` invents a
    record id. Inheriting a `trace` parent's provenance would let a relation
    result be read as production evidence about inputs production never
    produced.
    """
    traced = Scenario(id="a", records={"r1": 10, "r2": 4},
                      provenance=Provenance.TRACE)
    for name, relation in metamorphic.RELATIONS.items():
        assert relation.perturb(traced).provenance is Provenance.SYNTHETIC, name


def test_the_ledger_reports_undeclared_as_its_own_bucket():
    """Not filtered out the way `target_drift` drops offline runs.

    An absent framework means no framework was involved. An absent
    provenance means an unanswered question, and reporting the two the same
    way is the distinction this field exists to keep.
    """
    class _R:
        def __init__(self, scenario_id, provenance):
            self.scenario_id, self.provenance = scenario_id, provenance

    records = [_R("a", "trace"), _R("b", None), _R("c", "trace")]
    mix = ledger_mod.provenance_mix(records)
    assert mix == {"trace": ["a", "c"], None: ["b"]}
    assert not ledger_mod.generalises(records)
    assert ledger_mod.generalises([_R("a", "trace"), _R("c", "trace")])
    assert not ledger_mod.generalises([])


def test_discontinued_declares_what_it_is():
    """The one bundled fixture with no run history, so declaring costs nothing.

    Its hash moved deliberately when it gained this field -- the third such
    re-registration, documented in CLAUDE.md. The other five stay
    undeclared because moving their hashes would detach records.
    """
    task = TaskSpec.from_yaml("fixtures/inventory/discontinued.yaml")
    assert task.provenance is Provenance.SYNTHETIC
    assert not task.generalises()
    assert task.fingerprint() == "61ddcbf035c1b91d"
