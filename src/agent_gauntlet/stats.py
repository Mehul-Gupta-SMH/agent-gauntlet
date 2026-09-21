"""Intervals and detectability, with no third-party dependency.

Every rate this project printed before this module was a bare point estimate.
`n_runs` was displayed and never used to qualify anything, which made three
different claims unanswerable at once: was that a regression or a reseed, is
A really better than B, and 40% compliance plus or minus what.

Closed forms and a bootstrap rather than scipy. The project's dependencies
are `commonadk`, `pydantic` and `pyyaml`, and adding a scientific stack to
compute two z-scores would be a poor trade.

**What the widths mean.** These intervals range over *seed and repeat
variance on a fixed scenario*. They do not cover task variance, model drift
over time, or provider-side nondeterminism. A width quoted without that
scope is the same species of overclaim as an uncensored zero, so
`Interval.over` carries it and the renderers print it.
"""

from __future__ import annotations

import math
import random
from typing import Optional, Sequence

from pydantic import BaseModel

Z_95 = 1.959963984540054
"""Two-sided 95% normal quantile."""

Z_POWER_80 = 0.8416212335729143
"""One-sided 80% power quantile, for the detectable-effect calculation."""


class Interval(BaseModel):
    """A measurement and how far it might be off.

    `low`/`high` rather than a single ± because the interesting intervals
    here are asymmetric: Wilson's bounds at p near 0 or 1 are lopsided, and
    reporting a symmetric ± would misstate which direction the uncertainty
    runs.
    """

    value: float
    low: float
    high: float
    n: int
    method: str
    over: str = "seed and repeat variance, on this scenario"
    """What the width ranges over. Never silently broader than this."""

    @property
    def width(self) -> float:
        return self.high - self.low

    def excludes(self, other: "Interval") -> bool:
        """Do the two intervals fail to overlap?

        A blunt, conservative test of "these differ": non-overlap implies a
        significant difference, though overlap does *not* imply the absence
        of one. Used where the alternative is comparing two point estimates,
        which implies certainty nobody measured.
        """
        return self.high < other.low or other.high < self.low


def wilson(successes: int, n: int, z: float = Z_95) -> Optional[Interval]:
    """Wilson score interval for a binomial proportion.

    Wilson rather than the normal approximation, and the reason is the whole
    point of this module: at p = 0 or p = 1 the normal approximation gives a
    **zero-width** interval. A board that printed "propagation 0% ± 0" after
    eight runs would be claiming certainty it has not got, dressed as
    rigour. Wilson stays wide where the evidence is thin, which is the
    behaviour that makes it safe to put next to a gate verdict.
    """
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return Interval(
        value=p,
        low=max(0.0, centre - margin),
        high=min(1.0, centre + margin),
        n=n,
        method="wilson",
    )


def bootstrap(
    values: Sequence[float],
    *,
    resamples: int = 2000,
    seed: str = "gauntlet",
    alpha: float = 0.05,
) -> Optional[Interval]:
    """Percentile bootstrap for a bounded continuous mean.

    For `accuracy`, which lives in [0, 1] and is not a proportion of
    anything. A t-interval would happily produce bounds outside that range
    on a skewed sample; a percentile bootstrap cannot, because every bound
    it reports is an actual resampled mean.

    Deterministically seeded, because a confidence interval that moves when
    you look at it twice is not one the gate can be built on.
    """
    data = [float(v) for v in values]
    if not data:
        return None
    if len(data) == 1:
        # One observation constrains nothing. Reported as the full range
        # rather than as a point, which is the honest width.
        return Interval(value=data[0], low=0.0, high=1.0, n=1,
                        method="bootstrap (n=1, uninformative)")

    rng = random.Random(seed)
    n = len(data)
    means = sorted(
        sum(data[rng.randrange(n)] for _ in range(n)) / n
        for _ in range(resamples)
    )
    lo = means[int((alpha / 2) * resamples)]
    hi = means[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    return Interval(value=sum(data) / n, low=lo, high=hi, n=n,
                    method="bootstrap")


def ratio_of_means(
    numerator: Sequence[float],
    denominator: Sequence[float],
    *,
    resamples: int = 2000,
    seed: str = "gauntlet",
    alpha: float = 0.05,
) -> Optional[Interval]:
    """Percentile bootstrap for the ratio of two independent means.

    Not `bootstrap`: that one is for values bounded in [0, 1] and its
    single-observation fallback reports `[0, 1]`, which would be a
    confident lie about a ratio that has no upper bound.

    Both groups are resampled independently, because they are independent
    samples -- the clean and faulted halves of a variant's runs. A ratio
    reported without a width is how experiment 007 ended up publishing
    three per-factor spreads that were all inside the noise floor.

    Returns None below two observations on either side: one run constrains
    nothing, and a width of "anything at all" is not worth printing.
    """
    num = [float(v) for v in numerator]
    den = [float(v) for v in denominator]
    if len(num) < 2 or len(den) < 2:
        return None
    base_den = sum(den) / len(den)
    if base_den <= 0:
        return None

    rng = random.Random(seed)
    ratios = []
    for _ in range(resamples):
        a = sum(num[rng.randrange(len(num))] for _ in range(len(num))) / len(num)
        b = sum(den[rng.randrange(len(den))] for _ in range(len(den))) / len(den)
        if b <= 0:
            continue
        ratios.append(a / b)
    if not ratios:
        return None
    ratios.sort()
    lo = ratios[int((alpha / 2) * len(ratios))]
    hi = ratios[min(len(ratios) - 1, int((1 - alpha / 2) * len(ratios)))]
    return Interval(
        value=(sum(num) / len(num)) / base_den,
        low=lo, high=hi, n=min(len(num), len(den)),
        method="bootstrap (ratio of means)",
    )


def detectable_difference(
    n_per_group: int, p: float = 0.5, z_alpha: float = Z_95,
    z_power: float = Z_POWER_80,
) -> Optional[float]:
    """The smallest difference in a rate these runs could distinguish.

    Two-proportion form at 95% confidence and 80% power. `p = 0.5` by
    default because it maximises the variance and therefore the required
    effect -- the conservative choice, and the right one for a number whose
    job is to stop a green tick being over-read.

    This is what separates "no regression" from "not enough runs to see
    one". Reported beside every verdict for exactly that reason.
    """
    if n_per_group <= 0:
        return None
    return min(1.0, (z_alpha + z_power) * math.sqrt(2 * p * (1 - p) / n_per_group))


def runs_needed(effect: float, p: float = 0.5) -> Optional[int]:
    """How many runs per variant it would take to see a difference of
    `effect`. The inverse of `detectable_difference`, for an operator
    choosing repeats and seeds before spending anything."""
    if effect <= 0 or effect > 1:
        return None
    return math.ceil(2 * p * (1 - p) * ((Z_95 + Z_POWER_80) / effect) ** 2)
