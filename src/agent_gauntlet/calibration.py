"""Proper scoring: make honest uncertainty the dominant strategy (#21).

A proper scoring rule is one whose expected score is optimised by reporting
your true belief. Overconfidence and under-confidence are both penalised,
which is what makes it the principled version of the metric pairing #16
reaches for by hand: the same rule that punishes the credulous agent
punishes the paranoid one.

The concrete target is live. Experiment 003 caught `smart · naive · records`
flagging an anomaly on **33% of clean runs**, with no cross-check tool that
could have justified the claim — hedging without evidence, and free, because
`FA` is printed beside detection and folded into nothing. A variant that
says "possible anomaly" about everything scores detection 100% under fault
and pays no price for the noise.

Four things this module is careful about, and three of them are the reason
it is more than one line of arithmetic.

**A Brier score alone means nothing.** 0.18 is excellent on a task nobody
gets right and terrible on one everybody does. What is interpretable is the
skill score against the base-rate reference — how much better than
predicting the observed accuracy every time — and that is what the board
gets. A raw Brier printed without its reference is a rate printed without
its interval.

**Calibration and discrimination are different virtues.** "Right 80% of the
time" and "knows which 80%" are the two halves of Murphy's decomposition,
and the second is the one #21 calls the more valuable property. Reported
separately, because a config can be perfectly calibrated and useless
(always says 0.8, is right 80% of the time, has told you nothing about
*which* runs).

**A missing confidence is not a confidence of one half.** A config that
ignored the elicitation has expressed no belief. It is censored out of the
denominator like every other unmeasured thing here, never defaulted.

**The log score is implemented and not used for the board.** It punishes a
confident error far harder, which is the right shape for the propagation
failure mode, and it is unbounded — one p=0 on a correct answer makes a
config's mean infinitely bad, so a single parse failure would decide a
leaderboard. Available for a hypothesis bound, where an infinity is a
falsification rather than a ranking.
"""

from __future__ import annotations

import math
from statistics import mean
from typing import Optional, Sequence

from pydantic import BaseModel, Field

CONFIDENCE_FLOOR = 1e-6
"""How far from 0 and 1 the log score clamps.

Only the log score needs it: `log(0)` is not a number a report can carry.
Brier needs no clamp, which is part of why it is the one that ranks.
"""


def brier(confidence: float, correct: bool) -> float:
    """Squared error of a probability against what happened. Lower is better.

    Proper: the expected value is minimised when `confidence` equals the
    true probability of being correct. `tests/test_calibration.py` verifies
    that numerically rather than citing it.
    """
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be a probability, got {confidence!r}")
    return (confidence - float(correct)) ** 2


def log_score(confidence: float, correct: bool) -> float:
    """Negative log likelihood. Lower is better, and unbounded above.

    Kept because #21 asks for it and because a calibration *bound* can
    usefully be brutal about a confident error. Not used for ranking: one
    p=0 on a correct answer is an infinite penalty, and a single parse
    failure should not decide a leaderboard.
    """
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence must be a probability, got {confidence!r}")
    p = min(max(confidence, CONFIDENCE_FLOOR), 1.0 - CONFIDENCE_FLOOR)
    return -math.log(p if correct else 1.0 - p)


class CalibrationReport(BaseModel):
    """What a config's stated confidences were worth.

    Every field is Optional and absent rather than zero when it could not be
    computed. `n` is the number of runs that actually stated a confidence
    AND had a label to be scored against -- not the number of runs.
    """

    n: int = 0
    n_censored: int = 0
    """Runs excluded: no confidence stated, or nothing to be right about."""

    brier: Optional[float] = None
    reference: Optional[float] = None
    """The base-rate Brier: what predicting the observed accuracy every time
    would have scored. The only thing that makes `brier` readable."""
    skill: Optional[float] = None
    """1 - brier/reference. Positive beats the base rate, 0 matches it,
    negative is worse than saying "the same as always" every time.

    None when the reference is 0 -- which happens when every run went the
    same way, and then no confidence could have added information. That is
    the ceiling effect from experiment 003 arriving in a new metric, and it
    reads n/a rather than as perfect skill.
    """

    reliability: Optional[float] = None
    """Murphy: how far the stated probabilities sat from the outcomes they
    predicted, bucketed. Lower is better. This is "calibrated"."""
    resolution: Optional[float] = None
    """Murphy: how much the buckets differ from the overall rate. HIGHER is
    better. This is "knows which ones", and #21's more valuable property.

    A config that always says the same number has resolution 0 however
    well calibrated it is: it has told you nothing about which runs."""
    uncertainty: Optional[float] = None
    """The base rate's own variance -- the task's difficulty, not the
    config's doing. `brier == reliability - resolution + uncertainty`."""

    mean_log_score: Optional[float] = None

    @property
    def beats_the_base_rate(self) -> Optional[bool]:
        return None if self.skill is None else self.skill > 0.0


BUCKETS = 10
"""Bins for the Murphy decomposition.

Ten because a stated confidence is a self-report to one or two decimal
places, and finer bins would put one observation in each -- which makes
reliability zero by construction and the decomposition meaningless.
"""


def assess(
    observations: Sequence[tuple[Optional[float], Optional[bool]]]
) -> CalibrationReport:
    """Score (confidence, correct) pairs.

    A pair with either half missing is censored, not defaulted. The count of
    those is reported, because "this config never stated a confidence" and
    "this config was badly calibrated" are different findings and only one
    of them is about calibration.
    """
    usable = [(c, o) for c, o in observations
              if c is not None and o is not None]
    censored = len(observations) - len(usable)
    if not usable:
        return CalibrationReport(n=0, n_censored=censored)

    outcomes = [float(o) for _, o in usable]
    base = mean(outcomes)
    scores = [brier(c, o) for c, o in usable]
    # What predicting the base rate every single time would have scored.
    # Anything a stated confidence is worth has to be measured against this.
    reference = mean((base - o) ** 2 for o in outcomes)

    report = CalibrationReport(
        n=len(usable), n_censored=censored,
        brier=mean(scores), reference=reference,
        skill=None if reference == 0 else 1.0 - mean(scores) / reference,
        mean_log_score=mean(log_score(c, o) for c, o in usable),
        uncertainty=base * (1.0 - base),
    )

    # Murphy's decomposition, over bins of stated confidence.
    bins: dict[int, list[tuple[float, float]]] = {}
    for conf, out in usable:
        index = min(BUCKETS - 1, int(conf * BUCKETS))
        bins.setdefault(index, []).append((conf, float(out)))

    n = len(usable)
    reliability = 0.0
    resolution = 0.0
    for members in bins.values():
        weight = len(members) / n
        stated = mean(c for c, _ in members)
        observed = mean(o for _, o in members)
        reliability += weight * (stated - observed) ** 2
        resolution += weight * (observed - base) ** 2
    report.reliability = reliability
    report.resolution = resolution
    return report


def from_runs(records: Sequence) -> CalibrationReport:
    """The report for a set of run records.

    Only graded runs contribute: a confidence has nothing to be scored
    against where there is no right answer, which is the caveat #21 names
    and the reason this axis appears and disappears with the task's oracle.
    """
    return assess([
        (
            (r.answer.confidence if r.answer is not None else None),
            (r.score.correct if r.score.graded else None),
        )
        for r in records
    ])


__all__ = ["BUCKETS", "CONFIDENCE_FLOOR", "CalibrationReport", "assess",
           "brier", "from_runs", "log_score"]
