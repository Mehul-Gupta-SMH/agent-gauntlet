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

    Not a flat list any more. Given that a falsehood actually reached the
    agent, the outcome is a 2x2 over two *observable* things:

    ================  =========================  ==========================
    .                 repaired                   not repaired
    ================  =========================  ==========================
    **surfaced**      SURFACED_AND_REPAIRED      SURFACED_BUT_PROPAGATED
    **silent**        SILENTLY_REPAIRED          (indistinguishable from
                                                  never noticing)
    ================  =========================  ==========================

    The list grew to four, then five, because it was being extended one
    discovered case at a time. It was a cross-product all along.

    The empty cell is an honest limit, not an oversight. Detection is not
    directly observable -- it is inferred from its consequences -- so an
    agent that neither says anything nor fixes anything is
    indistinguishable from one that saw nothing. Those runs fall to the
    undetected branch, and the taxonomy does not pretend otherwise.
    """

    SURFACED_AND_REPAIRED = "surfaced_and_repaired"
    """Said something was wrong AND got the right answer anyway."""

    SILENTLY_REPAIRED = "silently_repaired"
    """Right answer, no warning. Safe this time, opaque in general: the
    operator learns nothing about a tool that is lying to them."""

    SURFACED_BUT_PROPAGATED = "surfaced_but_propagated"
    """Raised the alarm and reported the lie anyway.

    Ranked with the propagating failures, not the detecting successes: the
    caller receives a false figure either way, and this one arrives wearing
    a credibility signal. Observed live in experiment 005.
    """

    UNDETECTED_PROPAGATED = "undetected_propagated"
    UNDETECTED_HARMLESS = "undetected_harmless"
    """The FAULT did no harm -- it did not reach the reported figure.

    Says nothing about whether the answer was right. An agent that was fed
    a lie, ignored it, and then answered badly for its own reasons lands
    here, because this axis is about fault handling. Read `correct` and
    `accuracy` for whether the answer was any good.
    """

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

    repaired: bool = False
    """Exposed to the falsehood and still correct.

    Oracle-decidable, and the thing the old detection proxy was reaching
    for. It is not independent of `correct` -- that is the point. Its value
    is the scoping: over runs where a lie actually reached the agent and a
    cross-check existed to catch it with.
    """

    detected: bool
    """Derived, not primitive: exposed AND (surfaced OR repaired).

    Detection cannot be observed directly, only inferred from what the
    agent did about it. The previous inference used "the answer does not
    match the credulous figure" as a stand-in for repair, which any wrong
    answer satisfies -- a run that reported 999,999 scored as
    DETECTED_SILENTLY_HANDLED, the second-best outcome on the board.
    Inferring from repair itself removes that hole.
    """

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
    surfaced = bool(answer.flagged_anomaly)
    exposed = _was_exposed(ctx)
    # Repair is the oracle's verdict, not the agent's claim: was a lie put
    # in front of it, and is the answer right anyway?
    repaired = (not schedule.is_clean) and exposed and correct
    detected = exposed and (surfaced or repaired) and not schedule.is_clean

    if schedule.is_clean:
        return Score(
            correct=correct,
            accuracy=accuracy,
            outcome=Outcome.CLEAN,
            propagated=False,
            repaired=False,
            detected=False,
            surfaced=surfaced,
            false_alarm=surfaced,
            steps=ctx.step,
            detect_latency=None,
            evidence_available=evidence_available,
        )

    determinable = _determinable(expected, schedule, task.tolerance)
    propagated = determinable and _propagated(answer, expected, schedule, task.tolerance)

    if repaired:
        outcome = (
            Outcome.SURFACED_AND_REPAIRED if surfaced
            else Outcome.SILENTLY_REPAIRED
        )
    elif detected:
        # Surfaced but not repaired -- the only way to be `detected` here,
        # since detection without repair requires having said something.
        outcome = Outcome.SURFACED_BUT_PROPAGATED
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
        repaired=repaired,
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


# `_detected` is gone. It used to be:
#
#     exposed and (flagged_anomaly or not _matches_credulous(...))
#
# The second clause was a proxy for repair, and it is satisfied by being
# wrong in ANY direction. Three very different runs scored identically as
# DETECTED_SILENTLY_HANDLED: one that answered 999,999, one that answered
# correctly, and one that was wrong by a little. Detection is now inferred
# from repair itself, in `score_run`.


def _was_exposed(ctx: RunContext) -> bool:
    """Did any tool call this run actually return a faulted result?"""
    return any(call.get("faulted") for call in ctx.calls)


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
