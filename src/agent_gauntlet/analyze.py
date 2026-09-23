"""The gate statistics (C0.7).

Kendall's tau rather than Spearman's rho: at the handful-of-variants scale
this runs at, tau has better small-sample behaviour and a directly
interpretable meaning (the excess of concordant over discordant pairs).

Two things this module deliberately does *not* do:

- It does not average a time-to-detect over runs where detection never
  happened. Those are right-censored; averaging them in silently rewards
  agents that fail fast. Detection rate and conditional latency are
  reported separately (#16).
- It does not report a single stability number. A gate decided on one
  correlation from one pair of seeds is the same kind of n=1 result the
  whole project exists to argue against, so `stability` takes many pairs
  and returns the distribution.
"""

from __future__ import annotations

from itertools import combinations
from statistics import mean, median
from typing import Mapping, Optional, Sequence

from pydantic import BaseModel


def kendall_tau(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Tau-b over paired scores, tie-corrected.

    Returns None when the statistic is undefined -- fewer than two items, or
    one ranking entirely tied. Returning 0.0 there would be a lie: "no
    association measured" is not "no association".
    """
    n = len(a)
    if n != len(b):
        raise ValueError(f"paired sequences differ in length: {n} vs {len(b)}")
    if n < 2:
        return None

    concordant = discordant = tie_a = tie_b = 0
    for i, j in combinations(range(n), 2):
        da, db = a[i] - a[j], b[i] - b[j]
        if da == 0 and db == 0:
            tie_a += 1
            tie_b += 1
        elif da == 0:
            tie_a += 1
        elif db == 0:
            tie_b += 1
        elif (da > 0) == (db > 0):
            concordant += 1
        else:
            discordant += 1

    n0 = n * (n - 1) / 2
    denom = ((n0 - tie_a) * (n0 - tie_b)) ** 0.5
    if denom == 0:
        return None
    return (concordant - discordant) / denom


class StabilityReport(BaseModel):
    """What C0.7 hands back. Read `top1_stability` first -- it is the
    decision-relevant number; tau describes the whole ranking."""

    n_variants: int
    n_pairs: int
    taus: list[Optional[float]]
    top1_stability: Optional[float]
    """Fraction of DECIDABLE seed pairs whose winner is the same variant.

    None when no pair was decidable -- every seed's top was tied, so there
    was never a winner to agree or disagree about. Zero would be a lie of
    exactly the kind this project exists to refuse: "nobody won twice" is
    not "the winner changed every time"."""
    undecided_pairs: int
    """Pairs dropped from `top1_stability`'s denominator because at least
    one of the two seeds had a tied top.

    These are censored, not failures. Counting them as disagreement -- the
    original behaviour -- made a board with a tied top read as an UNSTABLE
    board, which is a different diagnosis with a different fix (#1). The
    treatment mirrors `DetectionReport.n_with_evidence`: a run that could
    not have detected anything leaves the denominator rather than scoring
    as a miss."""
    undefined_pairs: int
    """Pairs where tau could not be computed -- usually a ceiling effect,
    every variant tied. A large count here means the task failed to
    discriminate and the gate measured nothing (#29, correction 3)."""

    @property
    def median_tau(self) -> Optional[float]:
        vals = [t for t in self.taus if t is not None]
        return median(vals) if vals else None

    @property
    def mean_tau(self) -> Optional[float]:
        vals = [t for t in self.taus if t is not None]
        return mean(vals) if vals else None


def stability(runs: Mapping[str, Mapping[str, float]]) -> StabilityReport:
    """Rank stability across seeds.

    `runs` maps seed label -> {variant id -> score}. Every seed must cover
    the same variants; a missing variant would silently change what is
    being correlated.
    """
    seeds = sorted(runs)
    if len(seeds) < 2:
        raise ValueError("stability needs at least two seeds to compare")

    variants = sorted(runs[seeds[0]])
    for s in seeds:
        if sorted(runs[s]) != variants:
            missing = set(variants) ^ set(runs[s])
            raise ValueError(f"seed {s!r} does not cover the same variants: {missing}")

    taus: list[Optional[float]] = []
    winners_agree = 0
    undecided = 0
    pairs = list(combinations(seeds, 2))
    for s1, s2 in pairs:
        v1 = [runs[s1][v] for v in variants]
        v2 = [runs[s2][v] for v in variants]
        taus.append(kendall_tau(v1, v2))
        w1, w2 = _winner(runs[s1]), _winner(runs[s2])
        if w1 is None or w2 is None:
            undecided += 1
        elif w1 == w2:
            winners_agree += 1

    decidable = len(pairs) - undecided
    return StabilityReport(
        n_variants=len(variants),
        n_pairs=len(pairs),
        taus=taus,
        top1_stability=(winners_agree / decidable) if decidable else None,
        undecided_pairs=undecided,
        undefined_pairs=sum(1 for t in taus if t is None),
    )


def _winner(scores: Mapping[str, float]) -> Optional[str]:
    """The single best variant, or None if the top is tied.

    A tied top is not a winner. Picking one arbitrarily would manufacture
    agreement between seeds that the data does not support.
    """
    if not scores:
        return None
    best = max(scores.values())
    top = [v for v, s in scores.items() if s == best]
    return top[0] if len(top) == 1 else None


class DetectionReport(BaseModel):
    """Detection rate and latency, kept separate on purpose."""

    n_runs: int
    n_with_evidence: int
    """Runs where detection was possible at all. Runs without reachable
    evidence are excluded from the rate's denominator rather than counted
    as misses -- the equivalent-mutant correction (#16)."""
    detected: int
    surfaced: int
    propagated: int
    false_alarms: int
    n_clean: int
    latencies: list[int]

    @property
    def detection_rate(self) -> Optional[float]:
        if self.n_with_evidence == 0:
            return None
        return self.detected / self.n_with_evidence

    @property
    def false_alarm_rate(self) -> Optional[float]:
        if self.n_clean == 0:
            return None
        return self.false_alarms / self.n_clean

    @property
    def propagation_rate(self) -> Optional[float]:
        if self.n_with_evidence == 0:
            return None
        return self.propagated / self.n_with_evidence

    @property
    def median_latency(self) -> Optional[float]:
        return median(self.latencies) if self.latencies else None
