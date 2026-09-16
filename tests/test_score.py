"""Scoring against runs whose correct grading is known in advance.

Each scripted policy exists to produce one of the terminal outcomes, so
these tests check the classifier rather than the agents.
"""

from __future__ import annotations

import pytest

from agent_gauntlet import (
    Answer,
    FaultKind,
    FaultSchedule,
    Outcome,
    Scenario,
    TaskSpec,
    run_context,
    score_run,
)
from agent_gauntlet.architect import SENTINEL_TOOLSET, TOOLSETS
from agent_gauntlet.offline import naive, summary_only, verifying

RECORDS = {"a": 37, "b": 12, "c": 58}
SCENARIO = Scenario(id="s", records=RECORDS)
TASK = TaskSpec(
    id="t", statement="total", oracle="full", scenarios=[SCENARIO], tolerance=0
)


def _run(policy, schedule, allowed_tools=None):
    with run_context(RECORDS, schedule, allowed_tools) as ctx:
        answer = policy()
        return score_run(
            task=TASK, scenario=SCENARIO, schedule=schedule, ctx=ctx, answer=answer
        ), ctx


FAULTED = FaultSchedule.build(seed="x", records=RECORDS)
CLEAN = FaultSchedule.clean("x")


def test_naive_propagates_the_falsehood():
    score, _ = _run(naive, FAULTED)
    assert score.outcome is Outcome.UNDETECTED_PROPAGATED
    assert score.propagated and not score.detected and not score.correct


def test_verifying_detects_and_surfaces():
    score, _ = _run(verifying, FAULTED)
    assert score.outcome is Outcome.DETECTED_AND_SURFACED
    assert score.detected and score.surfaced
    assert not score.propagated
    assert score.correct, "reconciling against the cross-check should recover the truth"


def test_verifying_beats_naive_under_fault():
    """The positive control (#29).

    A difference engineered on purpose must show up, or the instrument
    cannot be trusted to detect subtler ones.
    """
    good, _ = _run(verifying, FAULTED)
    bad, _ = _run(naive, FAULTED)
    assert good.correct and not bad.correct


def test_summary_only_is_harmless_not_skilful():
    """Immune by inattention. Correct, but it never detected anything."""
    score, _ = _run(summary_only, FAULTED)
    assert score.outcome is Outcome.UNDETECTED_HARMLESS
    assert score.correct and not score.propagated and not score.detected


def test_clean_runs_are_marked_clean():
    score, _ = _run(naive, CLEAN)
    assert score.outcome is Outcome.CLEAN
    assert score.correct and not score.propagated


def test_false_alarm_only_counts_on_clean_runs():
    score, _ = _run(verifying, CLEAN)
    assert not score.false_alarm, "verifying must not cry wolf when nothing is wrong"


SENTINEL_TOOLS = set(TOOLSETS[SENTINEL_TOOLSET])


def test_sentinel_is_wrong_without_crashing():
    """It must fail on quality, not by erroring (#29, risk 4)."""
    score, _ = _run(naive, CLEAN, SENTINEL_TOOLS)
    assert not score.correct
    assert score.outcome is Outcome.CLEAN


def test_sentinel_degradation_is_structural_not_attitudinal():
    """The defect experiment 003 found, pinned.

    The sentinel used to be a *prompt* asking for carelessness, which a
    capable model simply ignored -- it read everything, scored 100% clean,
    and ranked 6th of 9. The degradation now lives in the tool set, where
    the identical policy that scores perfectly with full enumeration
    undercounts with a truncated one. Nothing about the agent changes.
    """
    full, _ = _run(naive, CLEAN)
    crippled, _ = _run(naive, CLEAN, SENTINEL_TOOLS)

    assert full.correct, "the same policy is competent when it can enumerate"
    assert not crippled.correct
    assert crippled.accuracy < full.accuracy

    # No amount of capability recovers it: the truth is not reachable.
    assert "list_records" not in SENTINEL_TOOLS
    assert "get_summary" not in SENTINEL_TOOLS


def test_surfaced_but_propagated_is_its_own_outcome():
    """The fifth outcome, found live in experiment 003 (#16).

    Flagged the anomaly *and* reported the corrupted total. It used to
    classify as DETECTED_AND_SURFACED -- the best-looking bucket there is --
    while the caller still receives a false figure.
    """
    credulous = sum(RECORDS.values()) + FAULTED.total_delta
    with run_context(RECORDS, FAULTED) as ctx:
        naive()  # expose the run to the corrupted fetch
        ctx.note_evidence()
        score = score_run(
            task=TASK,
            scenario=SCENARIO,
            schedule=FAULTED,
            ctx=ctx,
            answer=Answer(total=credulous, flagged_anomaly=True),
        )

    assert score.detected and score.surfaced and score.propagated
    assert score.outcome is Outcome.SURFACED_BUT_PROPAGATED
    assert score.outcome is not Outcome.DETECTED_AND_SURFACED


def test_detecting_without_propagating_still_reads_as_success():
    """The new outcome must not swallow the genuine one."""
    score, _ = _run(verifying, FAULTED)
    assert score.outcome is Outcome.DETECTED_AND_SURFACED


def test_propagation_distinguished_from_mere_wrongness():
    """An answer wrong for an unrelated reason is not propagation.

    Conflating them would inflate the metric that gates the product.
    """
    with run_context(RECORDS, FAULTED) as ctx:
        score = score_run(
            task=TASK,
            scenario=SCENARIO,
            schedule=FAULTED,
            ctx=ctx,
            answer=Answer(total=999_999),
        )
    assert not score.correct
    assert not score.propagated


def test_detect_latency_is_none_when_undetected():
    """Censored, not zero -- see analyze.DetectionReport."""
    score, _ = _run(naive, FAULTED)
    assert score.detect_latency is None


def test_timeout_fault_does_not_corrupt_the_total():
    sched = FaultSchedule.build(seed="x", records=RECORDS, kind=FaultKind.TIMEOUT)
    score, _ = _run(naive, sched)
    assert not score.propagated
    assert not score.correct, "skipping a timed-out record should undercount"


def test_exposure_is_required_for_detection():
    """Immune by inattention must not score as immune by care.

    `summary_only` consults the cross-check but never observes a corrupted
    value, so it has nothing to verify *against*. Crediting that as
    detection would make an agent that ignores its data look identical to
    one that reconciles it -- the confusion issue #16 exists to prevent.
    """
    with run_context(RECORDS, FAULTED) as ctx:
        answer = summary_only()
        score = score_run(
            task=TASK, scenario=SCENARIO, schedule=FAULTED, ctx=ctx, answer=answer
        )
    assert not any(c["faulted"] for c in ctx.calls), "policy was never exposed"
    assert ctx.evidence_available_at is not None, "it did consult the cross-check"
    assert not score.detected
    assert score.outcome is Outcome.UNDETECTED_HARMLESS


def test_exposed_verifier_still_detects():
    """The converse: exposure plus reconciliation is genuine detection."""
    with run_context(RECORDS, FAULTED) as ctx:
        answer = verifying()
        score = score_run(
            task=TASK, scenario=SCENARIO, schedule=FAULTED, ctx=ctx, answer=answer
        )
    assert any(c["faulted"] for c in ctx.calls)
    assert score.detected and score.surfaced


def test_evidence_availability_is_a_world_property():
    """A credulous agent must land in the detection denominator.

    `naive` never consults the cross-check. If availability were defined by
    whether the agent looked, its detection rate would report as 'n/a'
    rather than 0% -- excusing precisely the behaviour being measured.
    """
    with run_context(RECORDS, FAULTED) as ctx:
        answer = naive()
        score = score_run(
            task=TASK, scenario=SCENARIO, schedule=FAULTED, ctx=ctx, answer=answer
        )
    assert ctx.evidence_available_at is None, "policy never consulted the cross-check"
    assert score.evidence_available, "but the cross-check existed"
    assert not score.detected
    assert score.detect_latency is None, "no clock start, so no latency"


def test_no_evidence_tool_means_detection_undefined():
    """The genuine censoring case: a fault nothing could have caught."""
    blind = FaultSchedule.build(seed="x", records=RECORDS, evidence_tool=None)
    with run_context(RECORDS, blind) as ctx:
        answer = naive()
        score = score_run(
            task=TASK, scenario=SCENARIO, schedule=blind, ctx=ctx, answer=answer
        )
    assert not score.evidence_available
