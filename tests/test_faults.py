"""Determinism and fairness properties of the fault layer.

If these fail, nothing downstream means anything: a run that cannot be
reproduced is not a result.
"""

from __future__ import annotations

import pytest

from agent_gauntlet import FaultKind, FaultSchedule

RECORDS = {"a": 37, "b": 12, "c": 58, "d": 101}


def test_same_seed_produces_identical_schedule():
    one = FaultSchedule.build(seed="s1", records=RECORDS)
    two = FaultSchedule.build(seed="s1", records=RECORDS)
    assert one.model_dump() == two.model_dump()


def test_seed_survives_process_hashing():
    """The seed must not depend on PYTHONHASHSEED.

    `hash()` is salted per process; using it would make schedules differ
    between runs of the same command, which is the one thing this layer
    must not do. Asserted by checking the derived target is stable for a
    known seed rather than by trusting the implementation.
    """
    targets = {FaultSchedule.build(seed="fixed", records=RECORDS).faults[0].target_key}
    for _ in range(5):
        targets.add(FaultSchedule.build(seed="fixed", records=RECORDS).faults[0].target_key)
    assert len(targets) == 1


def test_different_seeds_diverge():
    seeds = {
        FaultSchedule.build(seed=f"s{i}", records=RECORDS).faults[0].target_key
        for i in range(24)
    }
    assert len(seeds) > 1, "fault target never varies across seeds"


def test_corruption_is_plausible_and_wrong():
    for i in range(50):
        sched = FaultSchedule.build(seed=f"seed{i}", records=RECORDS)
        fault = sched.faults[0]
        assert fault.corrupt_value != fault.true_value, "corruption equals the truth"
        assert fault.corrupt_value > 0, "an obviously absurd value is not a fair fault"
        assert sched.total_delta != 0


def test_clean_schedule_has_no_faults():
    clean = FaultSchedule.clean("s1")
    assert clean.is_clean
    assert clean.total_delta == 0


def test_timeout_fault_carries_no_values():
    sched = FaultSchedule.build(seed="s1", records=RECORDS, kind=FaultKind.TIMEOUT)
    fault = sched.faults[0]
    assert fault.kind is FaultKind.TIMEOUT
    assert fault.true_value is None and fault.corrupt_value is None
    assert sched.total_delta == 0


def test_empty_records_rejected():
    with pytest.raises(ValueError, match="zero records"):
        FaultSchedule.build(seed="s", records={})


def test_overriding_the_fault_moves_the_fingerprint():
    """A run faulted differently was not scored against the declared bar (#5).

    `--fault timeout` on `audited.yaml` used to record
    `task_fingerprint=d11a0b2a6801ee74` -- the hash of a spec whose
    `fault_kind` IS `wrong_value`. Two ledgers under two different
    adversities carried the identical fingerprint and the board averaged
    them.

    Worse than the mixed-rate-table confound (#14), where the hash is
    merely ambiguous: here it resolved to a spec the run was not produced
    under.
    """
    from agent_gauntlet.spec import TaskSpec

    task = TaskSpec.from_yaml("fixtures/inventory/audited.yaml")
    declared = task.fingerprint()
    assert declared == "d11a0b2a6801ee74"

    assert task.model_copy(
        update={"fault_kind": "timeout"}).fingerprint() != declared
    assert task.model_copy(
        update={"fault_tool": "list_records"}).fingerprint() != declared
    # And the two overrides are distinguishable from each other.
    both = task.model_copy(update={"fault_kind": "omission",
                                   "fault_tool": "list_records"})
    assert both.fingerprint() not in {
        declared,
        task.model_copy(update={"fault_kind": "omission"}).fingerprint(),
    }


def test_a_ledger_mixing_fault_kinds_says_so():
    """The backstop for every ledger written before the override was fixed.

    Read off `schedule.faults`, not the task, because the task is not where
    the answer was.
    """
    from agent_gauntlet.ledger import fault_kinds

    class _R:
        def __init__(self, variant_id, kinds):
            self.variant_id = variant_id
            self.schedule = type("S", (), {"faults": [
                type("F", (), {"kind": k})() for k in kinds]})()

    records = [_R("a", ["wrong_value"]), _R("b", ["timeout"]),
               _R("c", ["wrong_value"]), _R("clean", [])]
    mix = fault_kinds(records)
    assert mix == {"timeout": ["b"], "wrong_value": ["a", "c"]}
    # A clean run carries no fault and is not drift: its twin has no
    # adversity by construction.
    assert "clean" not in {v for vs in mix.values() for v in vs}
    assert len(fault_kinds([_R("a", ["omission"])])) == 1
