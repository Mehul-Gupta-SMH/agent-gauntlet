"""The behavioural fingerprint that triggers a re-run (#19).

A provider can change a model's behaviour without changing the model
string, so there is no version to poll and a periodic canary is the only
reliable detector. That makes cost the binding constraint -- and a cheap
canary reporting "unchanged" about a shift it could never have resolved
the binding risk. It would be this project's own fail-green shape,
arriving in the one place built to catch it.

So the tests below are mostly about what the canary admits it cannot see.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, VariantSpec, canary, offline, run_matrix
from agent_gauntlet.canary import BEHAVIOURAL, Shift
from agent_gauntlet.certify import RegressionBudget

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "inventory" / "audited.yaml"
WINNER = VariantSpec(
    id="winner",
    factors={"model": "smart", "prompt": "verifying", "toolset": "records+summary"},
)


@pytest.fixture(scope="module")
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


def shot(task, k=8, seed="canary-0"):
    with tempfile.TemporaryDirectory() as d:
        records = run_matrix(task=task, variants=[WINNER],
                             ledger=Ledger(Path(d) / "runs.jsonl"),
                             base_seed=seed, repeats=k)
    return canary.take(records, variant_id="winner",
                       task_fingerprint=task.fingerprint(),
                       base_seed=seed, repeats=k)


@pytest.fixture
def slip():
    """Change the world without changing the config -- the case that has no
    version string to poll."""
    original = dict(offline.MODEL_SLIP)
    yield lambda **kw: (offline.MODEL_SLIP.clear(),
                        offline.MODEL_SLIP.update({**original, **kw}))
    offline.MODEL_SLIP.clear()
    offline.MODEL_SLIP.update(original)


# --- the zero-width trap -------------------------------------------------


def test_an_all_agreeing_sample_does_not_claim_infinite_sensitivity(task):
    """The defect this module would most easily have shipped.

    A bootstrap over n identical observations returns a ZERO-width
    interval, which `compare` would read as "any change at all is
    detectable" and print as "blind below 0%". These three metrics are
    proportions of a per-run boolean, so they get Wilson -- the same choice
    the board makes for exactly the same reason.
    """
    fingerprint = shot(task, k=2)
    rates = [m for m in canary.CANARY_METRICS if m in fingerprint.intervals]
    assert rates, "nothing to check"
    for name in rates:
        interval = fingerprint.intervals[name]
        assert interval.width > 0, f"{name} claims a zero-width interval"
        assert interval.method == "wilson"


def test_the_blind_spot_shrinks_as_the_canary_gets_less_cheap(task):
    cheap = canary.compare(shot(task, k=2), shot(task, k=2)).blind_below
    dearer = canary.compare(shot(task, k=30), shot(task, k=30)).blind_below
    assert dearer < cheap


# --- calibration: it misses what it says it misses ------------------------


def test_it_catches_a_shift_above_its_blind_spot(task, slip):
    """The world moved and the config did not -- no version string changed,
    which is the whole case this exists for."""
    before = shot(task, k=8)
    slip(smart=0.70)
    after = shot(task, k=8)

    report = canary.compare(before, after)
    assert report.verdict is Shift.MOVED
    for shift in report.moved:
        assert abs(shift.delta) > shift.blind_below, (
            "a shift smaller than the blind spot must not be called MOVED"
        )


def test_it_misses_a_shift_below_its_blind_spot_and_says_so(task, slip):
    """The honest half. A canary this cheap cannot resolve a small drop,
    and the failure mode to avoid is not the miss -- it is reporting the
    miss as 'unchanged' with nothing attached."""
    before = shot(task, k=8)
    slip(smart=0.20)
    after = shot(task, k=8)

    report = canary.compare(before, after)
    assert report.verdict is Shift.UNCHANGED
    assert report.blind_below is not None and report.blind_below > 0
    quality = next(s for s in report.shifts if s.metric == "faulted_quality")
    assert quality.blind_below is not None


def test_a_cheap_canary_is_not_a_regression_check_and_the_arithmetic_says_so(task):
    """#19 asks what the minimum viable re-run is. It has an answer in runs
    rather than in judgement."""
    report = canary.compare(shot(task, k=8), shot(task, k=8))
    sens = canary.sensitivity(report, RegressionBudget(max_quality_drop=0.10))
    assert sens["adequate"] is False
    assert sens["blind_below"] > sens["needed"]
    assert sens["runs_needed"] > report.n_after


# --- units ---------------------------------------------------------------


def test_steps_are_not_folded_into_the_sensitivity_number(task):
    """`mean_steps` is measured in steps and the rates in proportion
    points. Taking a max across both produces a figure in no unit at all,
    and then compares it against a quality budget expressed in points."""
    report = canary.compare(shot(task, k=8), shot(task, k=8))
    steps = next(s for s in report.shifts if s.metric == BEHAVIOURAL)
    assert steps.blind_below is not None
    assert report.blind_below == max(
        s.blind_below for s in report.shifts
        if s.metric != BEHAVIOURAL and s.blind_below is not None
    )
    assert report.blind_below_steps == steps.blind_below


# --- refusals ------------------------------------------------------------


def test_comparing_across_tasks_is_refused(task):
    other = TaskSpec.from_yaml(ROOT / "fixtures" / "inventory" / "task.yaml")
    before = shot(task, k=2)
    after = shot(task, k=2).model_copy(
        update={"task_fingerprint": other.fingerprint()})
    report = canary.compare(before, after)
    assert report.refusal and "different tasks" in report.refusal
    assert not report.shifts, "a refusal must not also hand back a comparison"


def test_an_edited_config_is_refused_rather_than_reported_as_drift(task):
    """The conclusion an operator must not draw from this command. Somebody
    edited the prompt; the world did not move."""
    before = shot(task, k=2).model_copy(update={"variant_fingerprint": "aaaa"})
    after = shot(task, k=2).model_copy(update={"variant_fingerprint": "bbbb"})
    report = canary.compare(before, after)
    assert report.refusal and "the config itself changed" in report.refusal


def test_a_metric_missing_on_one_side_is_not_unchanged(task):
    """Censoring again: absent is not equal."""
    before = shot(task, k=8)
    after = shot(task, k=8)
    stripped = {k: v for k, v in after.intervals.items() if k != "propagation_rate"}
    report = canary.compare(before, after.model_copy(update={"intervals": stripped}))
    prop = next(s for s in report.shifts if s.metric == "propagation_rate")
    assert prop.shift is Shift.NOT_COMPARABLE
    assert report.verdict is Shift.NOT_COMPARABLE


# --- the file ------------------------------------------------------------


def test_a_canary_survives_a_round_trip(task, tmp_path):
    taken = shot(task, k=4)
    canary.save([taken], tmp_path / "canary.json")
    back = canary.load(tmp_path / "canary.json")[0]
    assert back.metrics == taken.metrics
    assert back.base_seed == taken.base_seed == "canary-0"
    assert back.repeats == taken.repeats
    assert canary.compare(taken, back).verdict is Shift.UNCHANGED
