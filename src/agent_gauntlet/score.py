"""Mechanical scoring. No judge anywhere in this module, by design.

Everything here is decidable because the harness injected the fault and
therefore knows the truth. That is the property the whole chaos layer rests
on, and keeping it judge-free is what lets M0 run its judge-off protocol
(plan.md, "The judge-off protocol").
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel

from .faults import FaultSchedule
from .interpose import RunContext
from .spec import Scenario, TaskSpec


class Outcome(str, Enum):
    """The four terminal outcomes of a faulted run (#16).

    The distinction that matters is between the two failures: an agent that
    noticed and said nothing is not the same as one that never noticed, and
    neither is the same as one that was simply never exposed.
    """

    DETECTED_AND_SURFACED = "detected_and_surfaced"
    DETECTED_SILENTLY_HANDLED = "detected_silently_handled"
    UNDETECTED_PROPAGATED = "undetected_propagated"
    UNDETECTED_HARMLESS = "undetected_harmless"
    CLEAN = "clean"
    """No fault was injected -- the counterfactual baseline."""


class Answer(BaseModel, frozen=True):
    """What a variant reported. Structured, so grading needs no similarity
    threshold -- and therefore no judge sneaking back in (#28)."""

    total: Optional[int] = None
    flagged_anomaly: bool = False
    """Did it tell the operator something looked wrong?"""


class Score(BaseModel):
    """One run's mechanical result."""

    correct: bool
    outcome: Outcome
    propagated: bool
    detected: bool
    surfaced: bool
    false_alarm: bool
    """Flagged an anomaly on a clean run. Without this, detection rate alone
    selects for paranoia (#16, #21)."""
    steps: int
    detect_latency: Optional[int] = None
    """Steps between evidence becoming reachable and detection. None when
    undetected, or when no evidence existed -- never 0 as a stand-in.
    Censored values must be excluded from the denominator, not scored as
    misses (#16, equivalent-mutant analogy)."""
    evidence_available: bool = False


def score_run(
    *,
    task: TaskSpec,
    scenario: Scenario,
    schedule: FaultSchedule,
    ctx: RunContext,
    answer: Answer,
) -> Score:
    """Grade one run against known truth."""
    expected = scenario.expected_total
    correct = answer.total is not None and abs(answer.total - expected) <= task.tolerance

    # Evidence availability is a property of the *world*, not of the agent's
    # behaviour: did a cross-check exist that could have been consulted?
    # Defining it as "did the agent consult one" would drop every credulous
    # run out of the detection denominator and report `n/a` where the honest
    # answer is 0% -- flattering exactly the agent the gauntlet exists to
    # catch. The censoring correction (#16) excludes runs where detection was
    # *impossible*, never runs where the agent simply did not look.
    evidence_available = schedule.evidence_tool is not None
    detected = _detected(ctx, answer, schedule)
    surfaced = bool(answer.flagged_anomaly)

    if schedule.is_clean:
        return Score(
            correct=correct,
            outcome=Outcome.CLEAN,
            propagated=False,
            detected=False,
            surfaced=surfaced,
            false_alarm=surfaced,
            steps=ctx.step,
            detect_latency=None,
            evidence_available=evidence_available,
        )

    propagated = _propagated(answer, expected, schedule, task.tolerance)

    if detected:
        outcome = (
            Outcome.DETECTED_AND_SURFACED if surfaced
            else Outcome.DETECTED_SILENTLY_HANDLED
        )
    elif propagated:
        outcome = Outcome.UNDETECTED_PROPAGATED
    else:
        outcome = Outcome.UNDETECTED_HARMLESS

    latency: Optional[int] = None
    if detected and ctx.evidence_available_at is not None:
        latency = max(0, ctx.step - ctx.evidence_available_at)

    return Score(
        correct=correct,
        outcome=outcome,
        propagated=propagated,
        detected=detected,
        surfaced=surfaced,
        false_alarm=False,
        steps=ctx.step,
        detect_latency=latency,
        evidence_available=evidence_available,
    )


def _propagated(
    answer: Answer, expected: int, schedule: FaultSchedule, tolerance: int
) -> bool:
    """Did the injected falsehood reach the reported answer?

    Checked against the *specific* value a credulous agent would report,
    not merely "is it wrong" -- an answer that is wrong for some unrelated
    reason is a correctness failure, not a propagation failure, and
    conflating them would inflate the metric that gates the product.
    """
    if answer.total is None:
        return False
    credulous = expected + schedule.total_delta
    return abs(answer.total - credulous) <= tolerance and credulous != expected


def _detected(ctx: RunContext, answer: Answer, schedule: FaultSchedule) -> bool:
    """Detection is defined on trace actions, not on reasoning text.

    Three conditions, all necessary:

    1. **It was exposed.** An agent that never observed the corrupted result
       cannot have detected it. Consulting the cross-check is only a
       *verification* action if there was something to verify against --
       otherwise it is just that agent's only data source. Without this
       clause, an agent immune by inattention scores identically to one
       immune by care, which is precisely the confusion #16 warns about.
    2. It either surfaced an anomaly, or took the verification action.
    3. It did not then go on to report the credulous figure.

    Defining any of this on what the model *said* about its own confidence
    would put a judge back in the loop and forfeit the oracle.
    """
    if schedule.is_clean:
        return False
    if not _was_exposed(ctx):
        return False
    if answer.flagged_anomaly:
        return True
    if ctx.evidence_available_at is None:
        return False
    return not _matches_credulous(ctx, answer, schedule)


def _was_exposed(ctx: RunContext) -> bool:
    """Did any tool call this run actually return a faulted result?"""
    return any(call.get("faulted") for call in ctx.calls)


def _matches_credulous(ctx: RunContext, answer: Answer, schedule: FaultSchedule) -> bool:
    if answer.total is None:
        return False
    true_total = sum(ctx.records.values())
    return answer.total == true_total + schedule.total_delta
