"""Proper scoring: honest uncertainty as the dominant strategy (#21).

The point of a proper rule is a mathematical property -- expected score is
optimised by reporting your true belief -- so the first test verifies it
numerically rather than citing it. The rest are about the three ways a
calibration figure lies: a raw Brier with no reference, a skill score where
no skill was measurable, and a missing confidence read as half.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board, calibration, run_matrix
from agent_gauntlet.calibration import assess, brier, log_score
from agent_gauntlet.live import parse_answer

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "inventory" / "audited.yaml"


# --- the property the whole issue rests on --------------------------------


@pytest.mark.parametrize("truth", [0.05, 0.3, 0.5, 0.75, 0.95])
def test_brier_is_proper(truth):
    """Expected score is minimised by stating your actual belief.

    Verified rather than cited: if this were not true, every number this
    module reports would reward a strategy other than honesty, which is the
    one thing #21 is buying.
    """
    def expected(stated):
        return truth * brier(stated, True) + (1 - truth) * brier(stated, False)

    best = min((expected(s / 1000) for s in range(1001)))
    assert expected(truth) == pytest.approx(best, abs=1e-9)
    # And strictly worse on both sides, so honesty is the unique optimum.
    assert expected(min(truth + 0.1, 1.0)) > expected(truth)
    assert expected(max(truth - 0.1, 0.0)) > expected(truth)


@pytest.mark.parametrize("truth", [0.2, 0.5, 0.8])
def test_the_log_score_is_proper_too(truth):
    def expected(stated):
        return truth * log_score(stated, True) + (1 - truth) * log_score(stated, False)

    assert expected(truth) < expected(truth + 0.15)
    assert expected(truth) < expected(truth - 0.15)


def test_the_log_score_punishes_a_confident_error_far_harder():
    """Why it is implemented and not used for ranking: one p=0 on a correct
    answer would decide a leaderboard by itself."""
    assert brier(0.0, True) == 1.0
    assert log_score(0.0, True) > 10
    assert math.isfinite(log_score(0.0, True)), "a report cannot carry an infinity"


def test_a_confidence_outside_zero_to_one_is_refused():
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError, match="probability"):
            brier(bad, True)


# --- calibrated is not the same as informative ----------------------------


def test_a_flat_confidence_has_no_resolution_however_calibrated():
    """#21's "knows which 80%" property, isolated. A config that says 0.9 and
    is right 90% of the time is perfectly calibrated and has told you
    nothing about which runs."""
    flat = assess([(0.9, True)] * 9 + [(0.9, False)])
    assert flat.reliability == pytest.approx(0.0)
    assert flat.resolution == pytest.approx(0.0)
    assert flat.skill == pytest.approx(0.0), "it exactly matches the base rate"


def test_a_confidence_that_knows_which_ones_scores_high():
    sharp = assess([(0.99, True)] * 9 + [(0.01, False)])
    assert sharp.resolution > 0.08
    assert sharp.skill > 0.99


def test_the_murphy_decomposition_adds_up():
    """brier == reliability - resolution + uncertainty. An identity, so a
    drift in any one term shows up here rather than in a printed row."""
    report = assess([(0.8, True), (0.8, True), (0.3, False), (0.6, True),
                     (0.6, False), (0.1, False)])
    assert report.brier == pytest.approx(
        report.reliability - report.resolution + report.uncertainty)


# --- the three ways it would lie ------------------------------------------


def test_a_missing_confidence_is_censored_not_defaulted():
    """A config that ignored the elicitation has expressed no belief, and
    half is a belief."""
    report = assess([(None, True), (0.9, True), (0.9, False), (0.5, None)])
    assert report.n == 2
    assert report.n_censored == 2


def test_nothing_usable_reports_nothing_rather_than_zero():
    report = assess([(None, True), (0.4, None)])
    assert report.n == 0
    assert report.brier is None and report.skill is None
    assert report.beats_the_base_rate is None


def test_skill_is_none_when_no_confidence_could_have_helped():
    """The ceiling effect arriving in a new metric. A config wrong on every
    single run has a base-rate reference of zero, so no stated confidence
    could have added information -- and that must read n/a, never as perfect
    skill."""
    always_wrong = assess([(0.8, False)] * 10)
    assert always_wrong.reference == pytest.approx(0.0)
    assert always_wrong.skill is None
    assert always_wrong.brier is not None, "the Brier itself is still real"


def test_a_raw_brier_is_reported_with_its_reference():
    """0.18 is excellent on a task nobody gets right and terrible on one
    everybody does, so the reference travels with it."""
    hard = assess([(0.5, True)] * 5 + [(0.5, False)] * 5)
    assert hard.brier is not None and hard.reference is not None


# --- parsing --------------------------------------------------------------


@pytest.mark.parametrize("line,expected", [
    ("CONFIDENCE: 0.9", 0.9),
    ("CONFIDENCE: 85%", 0.85),
    ("CONFIDENCE: 85", 0.85),
    ("CONFIDENCE: 1", 1.0),
    ("CONFIDENCE: 0", 0.0),
    ("CONFIDENCE: high", None),
    ("CONFIDENCE: 400", None),
    ("", None),
])
def test_the_confidence_line_is_read_or_refused(line, expected):
    """A percentage is accepted because models write one whatever the format
    says. A bare number above 1 is never clamped into range -- clamping
    would turn nonsense into a plausible figure."""
    answer = parse_answer(f"TOTAL: 128\nANOMALY: no\n{line}")
    assert answer.total == 128
    assert answer.confidence == expected


# --- on the board ---------------------------------------------------------


def test_the_board_carries_calibration_and_censors_the_sentinel(tmp_path):
    """The sentinel is wrong on every run, so its skill is n/a rather than
    perfect -- the censoring rule holding in a metric added months after it
    was written."""
    task = TaskSpec.from_yaml(FIXTURE)
    variants = architect.generate(out_dir=tmp_path / "v", task=task,
                                 models={"cheap": "m", "smart": "s"})
    records = run_matrix(task=task, variants=variants,
                        ledger=Ledger(tmp_path / "runs.jsonl"),
                        base_seed="s", repeats=10)
    rows = {r.variant_id: r for r in board.summarize(records)}

    sentinel = next(v for v in variants if v.is_sentinel)
    assert rows[sentinel.id].brier is not None
    assert rows[sentinel.id].brier_skill is None

    contenders = [r for vid, r in rows.items() if vid != sentinel.id]
    assert all(r.n_confident > 0 for r in contenders)
    assert all(r.brier is not None for r in contenders)


def test_every_scripted_policy_is_overconfident(tmp_path):
    """Recorded as a limit, not celebrated. The offline policies state ~0.9
    on a fixture where half the runs are faulted, so the rule punishes all
    of them -- which means nothing here demonstrates it can REWARD
    calibration. That needs a real model (#21, experiment 012).
    """
    task = TaskSpec.from_yaml(FIXTURE)
    variants = architect.generate(out_dir=tmp_path / "v", task=task,
                                 models={"cheap": "m", "smart": "s"})
    records = run_matrix(task=task, variants=variants,
                        ledger=Ledger(tmp_path / "runs.jsonl"),
                        base_seed="s", repeats=10)
    skills = [r.brier_skill for r in board.summarize(records)
              if r.brier_skill is not None]
    assert skills
    assert max(skills) <= 0.0, (
        "a scripted policy scored positive skill -- either the policies were "
        "tuned to the metric, or this limit no longer holds"
    )


def test_calibration_is_a_bindable_bound(tmp_path):
    """#21 asks whether this composes with #17's falsifiable bounds. It
    does: `brier` is in the shared metric set, so a task can pre-register
    "Brier below X" and have it scored on the board's own denominator."""
    from agent_gauntlet.attribution import METRICS
    from agent_gauntlet.hypotheses import Verdict, evaluate
    from agent_gauntlet.spec import Bound, Hypothesis

    assert "brier" in METRICS
    task = TaskSpec.from_yaml(FIXTURE)
    variants = architect.generate(out_dir=tmp_path / "v", task=task,
                                 models={"cheap": "m", "smart": "s"})
    records = run_matrix(task=task, variants=variants,
                        ledger=Ledger(tmp_path / "runs.jsonl"),
                        base_seed="s", repeats=6)

    impossible = Hypothesis(id="calibrated", says="Brier below 0.01",
                            bounds=[Bound(metric="brier", max=0.01)])
    result = evaluate(impossible, records, variant_id="all")
    assert result.verdict is Verdict.FALSIFIED
    assert result.bounds[0].n > 0
