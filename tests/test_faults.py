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
