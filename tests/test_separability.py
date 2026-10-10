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


# --- what it costs the fixtures that exist --------------------------------


@pytest.mark.parametrize("fixture", [
    "fixtures/inventory/task.yaml",
    "fixtures/inventory/audited.yaml",
])
def test_the_flag_empties_the_withheld_bucket(fixture, tmp_path):
    """The measurement that gives this feature its size.

    Pins the DIRECTION, not the count: the counts are deterministic for a
    given fixture and seed, and a test that pins them breaks on any fixture
    edit for reasons that have nothing to do with this claim.

    What the counts were when this was written, offline, 81 faulted runs
    each: `task.yaml` 59 undecidable of which 57 withheld, `audited.yaml`
    29 of which 28. Both go to zero with the flag. The default fixture had
    been dropping three quarters of its faulted runs from the gating
    metric's denominator, and nine tenths of those for a reason that was
    fixable all along (experiment 014).
    """
    from agent_gauntlet import architect
    from agent_gauntlet.matrix import run_matrix

    base = TaskSpec.from_yaml(fixture)
    counts = {}
    for sep in (False, True):
        task = base.model_copy(update={"separable_faults": sep})
        out = tmp_path / f"v{sep}"
        variants = architect.generate(
            out_dir=out, task=task, models={"cheap": "openai/gpt-4o-mini"})
        ledger = Ledger(tmp_path / f"runs-{sep}.jsonl")
        run_matrix(task=task, variants=variants, ledger=ledger,
                   base_seed="s", repeats=3, offline=True)
        faulted = [r for r in ledger.records()
                   if r.condition != "clean" and r.score is not None]
        assert faulted
        counts[sep] = sum(1 for r in faulted if r.score.propagation_withheld)

    assert counts[False] > 0, "this fixture has nothing to demonstrate"
    assert counts[True] == 0, "the flag must empty the withheld bucket"


def test_a_fixture_can_be_censored_for_cause_and_the_flag_cannot_help(tmp_path):
    """`applicant.yaml` loses runs too, and none of them are withheld.

    Its 27 undecidable faulted runs are variants that were never granted
    the faulted tool, so no answer they gave could be evidence either way.
    That is censoring for cause -- rule 3's *not applicable*, not
    *withheld* -- and forcing a bigger corruption does not and must not
    change it.
    """
    from agent_gauntlet import architect
    from agent_gauntlet.matrix import run_matrix

    base = TaskSpec.from_yaml("fixtures/lending/applicant.yaml")
    seen = {}
    for sep in (False, True):
        task = base.model_copy(update={"separable_faults": sep})
        variants = architect.generate(
            out_dir=tmp_path / f"a{sep}", task=task,
            models={"cheap": "openai/gpt-4o-mini"})
        ledger = Ledger(tmp_path / f"a-runs-{sep}.jsonl")
        run_matrix(task=task, variants=variants, ledger=ledger,
                   base_seed="s", repeats=3, offline=True)
        faulted = [r for r in ledger.records()
                   if r.condition != "clean" and r.score is not None]
        seen[sep] = (
            sum(1 for r in faulted if not r.score.propagation_determinable),
            sum(1 for r in faulted if r.score.propagation_withheld),
        )

    assert seen[False][1] == 0 and seen[True][1] == 0, "none are withheld"
    assert seen[False][0] == seen[True][0] > 0, (
        "censored for cause, and the flag changes nothing about it")


def test_the_offline_clean_spread_is_one_noise_rate_not_discrimination():
    """Guards the retraction in experiment 014.

    The offline clean leaderboard shows several distinct values, and they
    are draws from a single Bernoulli shared by every policy -- `_slip` at
    `SLIP_RATE`, declared in `offline.py` as the variance the stability
    machinery needs. A spread with one parameter for all configurations is
    not a measurement of how configurations differ.

    Pinned as a test because the claim "offline the clean board is not
    tied" is true, reads like discrimination, and is not. Anyone reaching
    for it to validate a candidate fixture for #45 should fail here first.
    """
    import inspect

    from agent_gauntlet import offline

    assert offline.SLIP_RATE > 0, "no slip, no spread, nothing to retract"
    for name in ("naive", "verifying", "filtering", "reconciling",
                 "summary_only", "bureau_repull", "bureau_deliberate"):
        sig = inspect.signature(getattr(offline, name))
        assert sig.parameters["rate"].default is offline.SLIP_RATE, (
            f"{name} must share the one slip rate, or the offline clean "
            f"spread would be a config property and the retraction wrong")
