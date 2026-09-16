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
    """The terminal outcomes of a faulted run (#16).

    The distinction that matters is between the failures: an agent that
    noticed and said nothing is not the same as one that never noticed,
    neither is the same as one that was simply never exposed -- and none of
    them is the same as one that noticed, said so, and reported the
    falsehood anyway.

    That last one was missing. Experiment 003 found two live variants
    scoring detection 100% *and* propagation 100%: they flagged the anomaly
    and shipped the corrupted total. The four-outcome taxonomy filed that
    under `DETECTED_AND_SURFACED`, the best-looking bucket there is, while
    operationally it may be the most dangerous pattern on the board -- it
    reads as vigilance and the falsehood still reaches the caller.
    """

    DETECTED_AND_SURFACED = "detected_and_surfaced"
    DETECTED_SILENTLY_HANDLED = "detected_silently_handled"
    SURFACED_BUT_PROPAGATED = "surfaced_but_propagated"
    """Raised the alarm and reported the lie anyway.

    Ranked with the propagating failures, not the detecting successes: the
    caller receives a false figure either way, and this one arrives wearing
    a credibility signal.
    """
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
    accuracy: float = 0.0
    """Graded correctness in [0, 1]: 1 - relative error, clipped.

    Binary `correct` is the gate-facing metric (#17 argues binary outcomes
    rank stably at lower k). `accuracy` exists because a binary metric has
    no resolution: with a handful of variants it produces ties, and a tied
    ranking cannot be correlated across seeds. Both are reported so the
    binary-vs-graded claim can be *measured* rather than assumed.
    """
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
    propagation_determinable: bool = True
    """Whether the corruption was large enough to tell a propagated answer
    apart from an honest miscount.

    When a fault shifts the total by less than the noise band, "trusted the
    lie" and "counted slightly wrong" are the same number, and propagation
    cannot be decided. Those runs leave the rate's denominator -- the same
    censoring treatment detection gets. Scoring them as *not* propagated
    would be a false all-clear on the one metric that gates the product.
    """


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
    accuracy = _accuracy(answer.total, expected)

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
            accuracy=accuracy,
            outcome=Outcome.CLEAN,
            propagated=False,
            detected=False,
            surfaced=surfaced,
            false_alarm=surfaced,
            steps=ctx.step,
            detect_latency=None,
            evidence_available=evidence_available,
        )

    determinable = _determinable(expected, schedule, task.tolerance)
    propagated = determinable and _propagated(answer, expected, schedule, task.tolerance)

    if detected and propagated:
        # Noticed and reported it anyway. Only reachable via the surfaced
        # branch of `_detected`: the silent branch is defined as *not*
        # matching the credulous figure, so it and propagation cannot both
        # hold.
        outcome = Outcome.SURFACED_BUT_PROPAGATED
    elif detected:
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
        accuracy=accuracy,
        outcome=outcome,
        propagated=propagated,
        detected=detected,
        surfaced=surfaced,
        false_alarm=False,
        steps=ctx.step,
        detect_latency=latency,
        evidence_available=evidence_available,
        propagation_determinable=determinable,
    )


def _propagated(
    answer: Answer, expected: int, schedule: FaultSchedule, tolerance: int
) -> bool:
    """Did the injected falsehood reach the reported answer?

    Judged by proximity to the figure a credulous agent would report, not by
    exact equality: an agent can propagate the lie *and* miscount slightly,
    and an exact match would score that as "not propagated" -- letting the
    worst case through the gate on a rounding error.

    A band rather than a distance comparison, because "closer to the lie
    than to the truth" alone would classify any wildly wrong answer as
    propagation. The answer must land near the credulous figure AND not
    near the truth.

    Callers must check `_determinable` first: when the corruption is
    smaller than the band this function cannot tell the two apart, and a
    False here would be a false all-clear rather than a finding.
    """
    if answer.total is None:
        return False
    credulous = expected + schedule.total_delta
    if credulous == expected:
        return False
    band = max(tolerance, int(abs(expected) * 0.10))
    near_lie = abs(answer.total - credulous) <= band
    near_truth = abs(answer.total - expected) <= band
    return near_lie and not near_truth


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
    """Same band rule as `_propagated`, so detection and propagation cannot
    disagree about whether the reported figure tracks the lie."""
    if answer.total is None:
        return False
    true_total = sum(ctx.records.values())
    credulous = true_total + schedule.total_delta
    band = max(0, int(abs(true_total) * 0.10))
    return abs(answer.total - credulous) <= band and abs(answer.total - true_total) > band


def _accuracy(reported: Optional[int], expected: int) -> float:
    """1 - relative error, clipped to [0, 1].

    Graded rather than binary so a ranking has resolution. An unanswered
    run scores 0.0 -- silence is not partial credit.
    """
    if reported is None:
        return 0.0
    if expected == 0:
        return 1.0 if reported == 0 else 0.0
    err = abs(reported - expected) / abs(expected)
    return max(0.0, min(1.0, 1.0 - err))


def _band(expected: int, tolerance: int) -> int:
    """Noise band around a reported figure: the most an honest miscount
    could plausibly move it."""
    return max(tolerance, int(abs(expected) * 0.10))


def _determinable(expected: int, schedule: FaultSchedule, tolerance: int) -> bool:
    """Is the corruption big enough to distinguish from an honest slip?

    Needs to clear *both* bands -- the one around the truth and the one
    around the lie -- or the two overlap and no answer could separate them.
    """
    if schedule.is_clean or schedule.total_delta == 0:
        return False
    return abs(schedule.total_delta) > 2 * _band(expected, tolerance)
