"""Is the gating metric decidable *before* the money is spent?

`coverage.assess` answers "is propagation measurable for this fault kind".
That is not the same question as "can this scenario's arithmetic decide
it". The second is settled by which record the seed happens to pick, and
until experiment 014 nothing asked it until after the run.

The draws are enumerable -- a fixed table of multiples over a known set of
records -- so this is an exact fraction with no interval attached, and
covering it costs nothing.
"""

from __future__ import annotations

import pytest

from agent_gauntlet import FaultKind, FaultSchedule, Ledger, Scenario, TaskSpec
from agent_gauntlet.coverage import separability
from agent_gauntlet.score import separability_band


def _task(records, *, separable=False, tolerance=0, kind="wrong_value"):
    return TaskSpec(
        id="t", statement="total", oracle="full", tolerance=tolerance,
        fault_kind=kind, separable_faults=separable,
        scenarios=[Scenario(id="s", records=records)],
    )


# --- the threshold --------------------------------------------------------


def test_the_threshold_is_twice_the_noise_band():
    """One band is not enough, and passing one was the original bug.

    The answer has to miss the band around the truth AND the band around
    the lie, or the two overlap and no reported figure separates them.
    """
    assert separability_band(668, 0) == 132
    assert separability_band(668, 200) == 400, "tolerance dominates when larger"


# --- the enumeration ------------------------------------------------------


def test_a_lopsided_scenario_is_mostly_decidable():
    """One record carrying most of the total: corrupting it moves the
    aggregate far enough on almost any draw."""
    row = separability(_task({"big": 3000, "small": 10})).scenarios[0]
    assert row.share > 0.4
    assert not row.hopeless


def test_a_flat_scenario_can_be_structurally_hopeless():
    """Twenty equal records: the largest shift a single draw can make is
    one record's own value, which cannot be a fifth of twenty of them.

    No seed decides this, and no amount of spending finds that out. It is
    arithmetic, available for free.
    """
    row = separability(_task({f"r{i}": 100 for i in range(20)})).scenarios[0]
    assert row.largest_shift < row.threshold
    assert row.hopeless
    assert row.share == 0.0


def test_the_fraction_is_exact_not_sampled():
    """`total_draws` is every factor on every eligible record."""
    from agent_gauntlet.faults import PLAUSIBLE_FACTORS

    row = separability(_task({"a": 300, "b": 200, "c": 168})).scenarios[0]
    assert row.total_draws == 3 * len(PLAUSIBLE_FACTORS)
    assert 0 <= row.decidable_draws <= row.total_draws


def test_the_audited_restriction_is_honoured():
    """Injection is confined to the cross-checked records (#5, #16), so the
    draws over the others were never possible and must not be counted."""
    task = TaskSpec(
        id="t", statement="total", oracle="full", tolerance=0,
        scenarios=[Scenario(id="s", records={"a": 300, "b": 200, "c": 168},
                            audited=["a"])],
    )
    from agent_gauntlet.faults import PLAUSIBLE_FACTORS
    row = separability(task).scenarios[0]
    assert row.total_draws == len(PLAUSIBLE_FACTORS)


# --- what it is not applicable to -----------------------------------------


@pytest.mark.parametrize("kind", ["timeout", "instruction", "omission"])
def test_kinds_whose_shift_is_not_a_draw(kind):
    """`TIMEOUT` and `INSTRUCTION` corrupt no number. `OMISSION` withholds
    a whole record, so its shift is that record's value and there is
    nothing to predict -- reporting a fraction for any of them would be a
    number standing in for a question that was never asked."""
    report = separability(_task({"a": 300, "b": 200}, kind=kind))
    assert not report.applicable
    assert report.scenarios == []
    assert report.at_risk == [] and report.hopeless == []


# --- the spec field -------------------------------------------------------


def test_separable_faults_removes_the_risk_and_says_so():
    report = separability(_task({"a": 300, "b": 200, "c": 168}, separable=True))
    assert report.guaranteed
    assert report.at_risk == [], "nothing is at risk when every draw is forced"


def test_separable_faults_joins_the_fingerprint_only_when_set():
    """The rule that keeps historical run records resolving (#003)."""
    records = {"a": 300, "b": 200, "c": 168}
    assert _task(records).fingerprint() == _task(records, separable=False).fingerprint()
    assert _task(records).fingerprint() != _task(records, separable=True).fingerprint()


def test_a_declared_separable_task_actually_decides_it(tmp_path):
    """End to end: the flag reaches the injector, not just the report.

    A run-time argument could have done the forcing, and one already does.
    It is a spec field because a call argument leaves no trace in the
    fingerprint, and "what the agent was shown" belongs in the hash.
    """
    from agent_gauntlet.matrix import run_matrix
    from agent_gauntlet import architect

    records = {"a": 40, "b": 35, "c": 32}
    forced = _task(records, separable=True)
    loose = _task(records, separable=False)
    variants = architect.generate(
        out_dir=tmp_path / "v", task=forced,
        models={"cheap": "openai/gpt-4o-mini"})[:2]

    decided = {}
    for task in (forced, loose):
        ledger = Ledger(tmp_path / f"runs-{task.separable_faults}.jsonl")
        run_matrix(task=task, variants=variants, ledger=ledger,
                   base_seed="s", repeats=3, offline=True)
        faulted = [r for r in ledger.records()
                   if r.condition != "clean" and r.score is not None]
        assert faulted
        decided[task.separable_faults] = all(
            r.score.propagation_determinable for r in faulted)

    assert decided[True], "the flag must make every faulted run decidable"
    # Not asserting the converse: whether an unforced draw happens to clear
    # the threshold is the seed's business, and pinning it here would pin a
    # coincidence.
