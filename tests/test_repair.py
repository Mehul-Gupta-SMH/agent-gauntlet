"""Repair rate, and the detection proxy it replaces (#16).

Detection is not directly observable. The harness infers it from what the
agent did about the fault, and the old inference used "the answer does not
match the credulous figure" as a stand-in for repair. Any wrong answer
satisfies that, so three very different runs scored identically as the
second-best outcome on the board.

Repair is the thing that clause was reaching for, measured directly:
exposed to a lie, and correct anyway.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import (
    Answer, FaultSchedule, Ledger, Outcome, Scenario, TaskSpec, architect,
    board, run_context, run_matrix, score_run,
)
from agent_gauntlet.interpose import fetch_quantity, list_record_ids, summary_total
from agent_gauntlet.ledger import rescore
from agent_gauntlet.offline import naive

RECORDS = {"a": 37, "b": 12, "c": 58}
SCENARIO = Scenario(id="s", records=RECORDS)
TASK = TaskSpec(id="t", statement="total", oracle="full",
                scenarios=[SCENARIO], tolerance=0)
FAULTED = FaultSchedule.build(seed="x", records=RECORDS)
TRUTH = SCENARIO.expected_total
CREDULOUS = TRUTH + FAULTED.total_delta

AUDITED = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "audited.yaml"
MODELS = {"cheap": "openai/gpt-4o-mini", "smart": "anthropic/claude-sonnet-5"}


def _score(total, *, flagged=False, consult=True, schedule=FAULTED, allowed=None):
    with run_context(RECORDS, schedule, allowed) as ctx:
        for rid in list_record_ids():
            fetch_quantity(rid)
        if consult:
            summary_total()
        return score_run(task=TASK, scenario=SCENARIO, schedule=schedule,
                         ctx=ctx, answer=Answer(total=total, flagged_anomaly=flagged))


# --- the defect that motivated this ---------------------------------------


def test_a_garbage_answer_is_no_longer_credited_with_detection():
    """The hole in the old proxy.

    999,999 does not match the credulous figure, so the old rule inferred
    that the agent must have noticed the fault. It noticed nothing; it was
    wrong in a different direction.
    """
    score = _score(999_999)
    assert not score.repaired
    assert not score.detected, "being wrong in another direction is not detecting"
    # Not HARMLESS: nothing false propagated, and the answer is still wrong.
    # That split arrived with #5, from measuring an availability fault --
    # and this run is the same shape, so it moved with it.
    assert score.outcome is Outcome.UNDETECTED_DEGRADED


def test_three_answers_the_old_rule_conflated_now_separate():
    """All three used to score DETECTED_SILENTLY_HANDLED.

    Two of the three now land in `UNDETECTED_DEGRADED` rather than
    `UNDETECTED_HARMLESS` (#5). Both are wrong answers with nothing
    propagated, and calling that harmless put the word next to a figure
    that was never right.
    """
    garbage = _score(999_999)
    correct = _score(TRUTH)
    slightly_wrong = _score(TRUTH - 7)

    assert correct.outcome is Outcome.SILENTLY_REPAIRED
    assert garbage.outcome is Outcome.UNDETECTED_DEGRADED
    assert slightly_wrong.outcome is Outcome.UNDETECTED_DEGRADED
    assert garbage.outcome.severity > correct.outcome.severity
    assert correct.repaired and not garbage.repaired and not slightly_wrong.repaired


# --- the 2x2 --------------------------------------------------------------


@pytest.mark.parametrize("total,flagged,expected", [
    (TRUTH,     True,  Outcome.SURFACED_AND_REPAIRED),
    (TRUTH,     False, Outcome.SILENTLY_REPAIRED),
    (CREDULOUS, True,  Outcome.SURFACED_BUT_PROPAGATED),
    (CREDULOUS, False, Outcome.UNDETECTED_PROPAGATED),
])
def test_every_cell_of_the_cross_product_is_reachable(total, flagged, expected):
    assert _score(total, flagged=flagged).outcome is expected


def test_detection_is_derived_from_surfacing_or_repair():
    """Not a primitive any more -- the two observables imply it."""
    for total, flagged in ((TRUTH, False), (CREDULOUS, True), (TRUTH, True)):
        score = _score(total, flagged=flagged)
        assert score.detected == (score.surfaced or score.repaired)

    silent_and_wrong = _score(CREDULOUS, flagged=False)
    assert not silent_and_wrong.detected, (
        "neither said nor fixed anything -- indistinguishable from never "
        "noticing, and the taxonomy must not pretend otherwise"
    )


def test_a_clean_run_repairs_nothing():
    score = _score(TRUTH, schedule=FaultSchedule.clean("x"))
    assert not score.repaired and not score.detected
    assert score.outcome is Outcome.CLEAN


def test_repair_needs_exposure_not_just_a_right_answer():
    """Immune by inattention is not repair.

    A variant that never touched the corrupted record was never asked the
    question; crediting it would flatter exactly the agent this project
    exists to catch.
    """
    with run_context(RECORDS, FAULTED) as ctx:
        summary_total()  # reads the cross-check and nothing else
        score = score_run(task=TASK, scenario=SCENARIO, schedule=FAULTED,
                          ctx=ctx, answer=Answer(total=TRUTH))
    assert score.correct
    assert not score.repaired, "never exposed, so nothing was repaired"


# --- the denominator ------------------------------------------------------


def _matrix(tmp_path):
    task = TaskSpec.from_yaml(AUDITED)
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="s", repeats=2)
    return task, variants, ledger


def test_no_cross_check_means_repair_is_not_measured(tmp_path):
    """Corruptions are plausible by design, so without evidence repair is
    impossible in principle. Censored, exactly as detection is."""
    _, _, ledger = _matrix(tmp_path)
    rows = {r.variant_id: r for r in board.summarize(ledger.records())}
    no_evidence = [r for r in rows.values() if r.detection_rate is None]
    assert no_evidence, "no censored variants -- fixture problem"
    for r in no_evidence:
        assert r.repair_rate is None, "not 0% -- it was never askable"
        assert r.n_repair_eligible == 0


def test_a_repairing_variant_reports_a_rate_and_its_denominator(tmp_path):
    _, _, ledger = _matrix(tmp_path)
    rows = board.summarize(ledger.records())
    repairing = [r for r in rows if r.repair_rate]
    assert repairing, "nothing repaired -- fixture problem"
    for r in repairing:
        assert 0 < r.repair_rate <= 1
        assert r.n_repair_eligible > 0
        assert r.repair_quality is not None


def test_repair_and_detection_can_disagree(tmp_path):
    """The point of the whole exercise.

    A variant that flags every fault and fixes none sits at det=100%,
    rep=0%. Experiment 005 caught a live model doing exactly that.
    """
    score = _score(CREDULOUS, flagged=True)
    assert score.detected and not score.repaired


# --- replayability --------------------------------------------------------


def test_the_ledger_stores_what_the_agent_answered(tmp_path):
    _, _, ledger = _matrix(tmp_path)
    for record in ledger.records():
        assert record.answer is not None


def test_a_stored_run_can_be_graded_again_without_rerunning(tmp_path):
    """The reason `answer` exists.

    Three revisions of this taxonomy have happened. Without the answer on
    disk each one is a one-way door that needs a fresh paid matrix to
    evaluate against anything.
    """
    task, _, ledger = _matrix(tmp_path)
    for record in ledger.records():
        again = rescore(record, task)
        assert again.outcome is record.score.outcome
        assert again.repaired == record.score.repaired
        assert again.detected == record.score.detected
        assert again.accuracy == pytest.approx(record.score.accuracy)


def test_rescoring_a_record_without_an_answer_refuses(tmp_path):
    """Rather than inventing one. A re-score that guessed would produce
    numbers indistinguishable from measured ones."""
    task, _, ledger = _matrix(tmp_path)
    record = ledger.records()[0]
    record.answer = None
    with pytest.raises(ValueError, match="predates the stored answer"):
        rescore(record, task)


# --- metering reaches the ledger (#14) ------------------------------------


def test_an_executor_that_meters_has_its_rollup_recorded():
    """The $5 that experiment 007 spent was never written down.

    `live_executor` held the Trace -- token counts, cost_usd, durations, all
    verified complete in experiment 002 -- read the text off it and dropped
    the rest. `run_matrix` never set `rollup`, so a full paid matrix
    recorded no spend and the board's cost column read n/a.
    """
    from agent_gauntlet.matrix import _unpack

    priced = {"llm_calls": {"cost_usd": 0.02, "total_tokens": 100,
                            "cost_complete": True}}
    answer, rollup = _unpack((Answer(total=7), priced))
    assert answer.total == 7 and rollup == priced


def test_an_executor_that_meters_nothing_still_works():
    """Offline policies spend nothing and return a bare Answer."""
    from agent_gauntlet.matrix import _unpack

    answer, rollup = _unpack(Answer(total=7))
    assert answer.total == 7 and rollup == {}


def test_the_ledger_carries_what_a_metered_run_cost(tmp_path):
    from agent_gauntlet.matrix import run_matrix as _rm

    task = TaskSpec.from_yaml(AUDITED)
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    priced = {"llm_calls": {"cost_usd": 0.01, "total_tokens": 50,
                            "cost_complete": True}}

    def metered(variant, seed):
        return naive(), priced  # noqa

    _rm(task=task, variants=variants[:1], ledger=ledger, base_seed="s",
        repeats=1, executor=metered, offline=False)

    records = ledger.records()
    assert records and all(r.rollup == priced for r in records)

    row = board.summarize(records)[0]
    assert row.cost_complete is True
    assert row.cost_usd == pytest.approx(0.01 * len(records))
    assert row.cost_per_run == pytest.approx(0.01)


def test_the_probe_accepts_a_metering_executor():
    """Every probe call site must unpack, or the live probe breaks the
    moment the live executor starts metering."""
    import inspect
    from agent_gauntlet import cli

    body = inspect.getsource(cli._probe)
    for call in ('execute(variant, "probe")', 'execute(variant, "probe-faulted")',
                 'execute(guard, "probe-sentinel")'):
        assert f"_unpack({call})" in body, f"{call} does not unpack"
