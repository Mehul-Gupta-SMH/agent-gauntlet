"""Per-factor attribution that stays correct under interaction (#22).

`board.factor_effects` averages a factor's levels over everything else. On
this project's own grid that is not an approximation, it is wrong: a
`verifying` prompt is worth a great deal *with* a cross-check and nothing at
all without one, so the average describes no configuration anybody could
run. Experiment 007 made it concrete -- the marginal table called the model
axis worth 10 points while, in the only cell where the model can express
itself, it moved repair from 22% to 100%.

Shapley values are the canonical fix: a factor's contribution averaged over
every order in which the factors could have been switched on. They carry a
fairness axiomatisation rather than being an ad-hoc formula, and the
efficiency axiom gives the property the marginal table lacks --

    sum of the shares == the whole gap between the two configurations

-- so the table adds up to something real instead of to whatever it adds up
to.

Three deliberate departures from what #22 sketches.

**No sampling estimator.** #22 worries about 2^n coalitions, and rightly, in
general. This grid is three two-level factors: eight coalitions, all of them
already run. Exact Shapley is a loop. A sampling approximation here would
add variance to a number that has an exact value, which is the wrong trade
in the one place it can be avoided. `attribute` refuses above
`MAX_EXACT_FACTORS` rather than silently switching methods.

**No optuna.** #22 suggests evaluating `optuna.importance` (fANOVA) first.
fANOVA fits a random forest to a sampled search space to *estimate* what a
complete factorial measures directly; pulling in a dependency to
approximate eight numbers we already have would be worse in every direction.
The suggestion stands for #10, where successive halving stops measuring most
of the space.

**Intervals are not optional.** #34's finding was that the marginal table's
15/15/10 points were never distinguishable from zero at that run size. An
attribution that repeats the shape with better arithmetic fails the same
way, so every share carries a bootstrap interval and the report carries the
resolution the design could have seen.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations
from math import factorial
from statistics import mean
from typing import Callable, Mapping, Optional, Sequence

from pydantic import BaseModel, Field

from .ledger import RunRecord
from .stats import Interval, detectable_difference

MAX_EXACT_FACTORS = 8
"""Above this, `attribute` refuses rather than approximating.

2^8 = 256 coalitions is still instant; the real limit is that a factorial
design that wide has not been run, so the refusal fires on the missing cells
first. The bound exists so the exact loop can never be the thing that hangs.
"""

BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = "gauntlet-attribution"


@dataclass(frozen=True)
class Metric:
    """One number the board reports, as a filter and a value.

    Defined here as (which runs count, what each contributes) so the
    denominators match `board.summarize` exactly rather than approximately.
    `tests/test_attribution.py` asserts that equality cell by cell -- an
    attribution table that quietly disagrees with the board above it would
    be worse than no table.
    """

    name: str
    include: Callable[[RunRecord], bool]
    value: Callable[[RunRecord], float]
    note: str = ""


def _faulted(r: RunRecord) -> bool:
    return r.condition == "faulted"


def _with_evidence(r: RunRecord) -> bool:
    return (_faulted(r) and r.score.evidence_available
            and r.score.exposure_possible)


def _repairable(r: RunRecord) -> bool:
    return _with_evidence(r) and any(c.get("faulted") for c in r.tool_calls)


METRICS: dict[str, Metric] = {
    "quality": Metric(
        "quality", lambda r: r.score.graded, lambda r: float(r.score.correct)),
    "accuracy": Metric(
        "accuracy", lambda r: r.score.graded, lambda r: float(r.score.accuracy)),
    "clean_quality": Metric(
        "clean_quality",
        lambda r: r.condition == "clean" and r.score.graded,
        lambda r: float(r.score.correct)),
    "faulted_quality": Metric(
        "faulted_quality",
        lambda r: _faulted(r) and r.score.graded,
        lambda r: float(r.score.correct)),
    "propagation_rate": Metric(
        "propagation_rate", _faulted, lambda r: float(r.score.propagated),
        note="over ALL faulted runs; the evidence denominator belongs to "
             "detection alone"),
    "detection_rate": Metric(
        "detection_rate", _with_evidence, lambda r: float(r.score.detected),
        note="censored: only runs where detection was possible"),
    "repair_rate": Metric(
        "repair_rate", _repairable, lambda r: float(r.score.repaired),
        note="censored: only runs a lie actually reached, with something to "
             "catch it"),
}


class FactorShare(BaseModel):
    """One factor's share of the gap, and what it is a share of."""

    factor: str
    from_level: str
    to_level: str
    share: float
    """Shapley value: points of `Attribution.gap` credited to switching this
    factor. Signed -- a factor can cost you points."""
    interval: Optional[Interval] = None
    conditional: dict[str, float] = Field(default_factory=dict)
    """This factor's raw marginal contribution in each coalition it could be
    added to, keyed by the coalition already switched on.

    The spread across these is the interaction the single share averages
    over. A share is only a summary; where these disagree sharply, the
    sentence an operator wants is a conditional one, not an attribution.
    """

    @property
    def conditional_range(self) -> Optional[float]:
        if len(self.conditional) < 2:
            return None
        return max(self.conditional.values()) - min(self.conditional.values())

    @property
    def excludes_zero(self) -> Optional[bool]:
        """Does the bootstrap interval stay on one side of zero?

        This, not the design floor below, is the test the report flags on.
        #34's lesson was that a number without an interval is not a
        measurement, and this is that interval doing its job: it is computed
        from the statistic actually being reported, over the runs actually
        behind it.
        """
        if self.interval is None:
            return None
        return self.interval.low > 0 or self.interval.high < 0

    def below_resolution(self, resolution: Optional[float]) -> bool:
        """Is this share smaller than a single contrast could have resolved?

        Reported as context, deliberately NOT as the verdict. A Shapley
        share averages several contrasts, so it can be tighter than the
        two-proportion floor for any one of them -- comparing the two
        directly would call a measured effect unmeasured. The floor still
        earns its place on the page: a share sitting under it is resting
        entirely on its interval, and a reader should know which of the two
        they are trusting.
        """
        return resolution is not None and abs(self.share) < resolution


class Interaction(BaseModel):
    """How much two factors are worth together beyond their separate worth."""

    factors: tuple[str, str]
    index: float
    """Shapley interaction index. Positive: the pair is worth more together
    than apart -- each one needs the other. Zero: they are additive."""


class Attribution(BaseModel):
    """What `attribute` hands back, refusal included.

    A refusal is a result. `shares` is empty exactly when `refusal` is set,
    so a caller cannot read a table that was never computed.
    """

    metric: str
    refusal: Optional[str] = None
    baseline: dict[str, str] = Field(default_factory=dict)
    target: dict[str, str] = Field(default_factory=dict)
    baseline_value: Optional[float] = None
    target_value: Optional[float] = None
    shares: list[FactorShare] = Field(default_factory=list)
    interactions: list[Interaction] = Field(default_factory=list)
    resolution: Optional[float] = None
    """The smallest difference this design could have seen, from the
    thinnest cell. Shares below it are unmeasured, not small."""
    min_cell_n: Optional[int] = None
    chose_corners_by_name: bool = False
    """True when the two configurations were picked by level NAME rather
    than by score -- the default, and the reason the gap below is not
    selected on the data it is computed from (#20)."""

    @property
    def gap(self) -> Optional[float]:
        if self.baseline_value is None or self.target_value is None:
            return None
        return self.target_value - self.baseline_value

    @property
    def explains_all_of_it(self) -> bool:
        """The efficiency axiom, checked rather than assumed."""
        if self.gap is None or not self.shares:
            return False
        return abs(sum(s.share for s in self.shares) - self.gap) < 1e-9


def attribute(
    records: Sequence[RunRecord],
    *,
    metric: str = "quality",
    baseline: Optional[Mapping[str, str]] = None,
    target: Optional[Mapping[str, str]] = None,
    sentinel_ids: Sequence[str] = (),
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Attribution:
    """Decompose the gap between two configurations across the factors.

    With `baseline` and `target` left out, the two corners are chosen by
    sorting each factor's level names -- NOT by which scored best. That
    matters: a gap measured between the best and worst cells of the design
    is selected on the data it is then explained from, which is the winner's
    curse (#20) wearing a different hat. Chosen by name, the gap is whatever
    it is.
    """
    spec = METRICS.get(metric)
    if spec is None:
        return Attribution(metric=metric,
                           refusal=f"unknown metric {metric!r}; "
                                   f"known: {sorted(METRICS)}")

    # The sentinel is a property of the variant spec, not of any factor
    # value, so the caller has to say which runs are its. Left in, it would
    # sit in whichever coalitions its factor levels land in and drag them
    # down -- a deliberately broken configuration masquerading as evidence
    # about a factor level.
    excluded = set(sentinel_ids)
    usable = [r for r in records if r.variant_id not in excluded]
    if not usable:
        return Attribution(metric=metric, refusal="no non-sentinel runs")

    levels: dict[str, set[str]] = {}
    for r in usable:
        for factor, level in r.factors.items():
            levels.setdefault(factor, set()).add(level)
    factors = sorted(f for f, vals in levels.items() if len(vals) > 1)

    if not factors:
        return Attribution(metric=metric,
                           refusal="no factor varies; nothing to attribute")
    if len(factors) > MAX_EXACT_FACTORS:
        return Attribution(
            metric=metric,
            refusal=f"{len(factors)} varying factors; this computes Shapley "
                    f"exactly and refuses above {MAX_EXACT_FACTORS} rather "
                    f"than switching to an estimator without saying so")

    by_name = baseline is None and target is None
    if by_name:
        wide = [f for f in factors if len(levels[f]) != 2]
        if wide:
            return Attribution(
                metric=metric,
                refusal="corners can only be chosen by name when every "
                        f"factor has exactly two levels; {wide} do not. "
                        "Pass baseline= and target= explicitly")
        baseline = {f: sorted(levels[f])[0] for f in factors}
        target = {f: sorted(levels[f])[1] for f in factors}
    elif baseline is None or target is None:
        return Attribution(metric=metric,
                           refusal="pass both baseline= and target=, or neither")

    # --- the value function, one measured cell per coalition -------------
    cells: dict[frozenset[str], list[float]] = {}
    for size in range(len(factors) + 1):
        for switched in combinations(factors, size):
            on = frozenset(switched)
            want = {f: (target[f] if f in on else baseline[f]) for f in factors}
            runs = [r for r in usable
                    if all(r.factors.get(f) == v for f, v in want.items())
                    and spec.include(r)]
            if not runs:
                return Attribution(
                    metric=metric,
                    refusal=(
                        "the design is not complete for this metric: no run "
                        "measures " + ", ".join(f"{f}={v}" for f, v in
                                                sorted(want.items()))
                        + f". Shapley needs every coalition, and {metric!r} "
                        "has no value in that cell"
                        + (f" ({spec.note})" if spec.note else "")
                    ),
                )
            cells[on] = [spec.value(r) for r in runs]

    values = {k: mean(v) for k, v in cells.items()}
    shares = _shapley(factors, values)
    min_n = min(len(v) for v in cells.values())

    intervals = _bootstrap_shares(factors, cells, resamples=resamples)

    return Attribution(
        metric=metric,
        baseline=dict(baseline),
        target=dict(target),
        baseline_value=values[frozenset()],
        target_value=values[frozenset(factors)],
        chose_corners_by_name=by_name,
        min_cell_n=min_n,
        resolution=detectable_difference(min_n),
        shares=[
            FactorShare(
                factor=f,
                from_level=baseline[f],
                to_level=target[f],
                share=shares[f],
                interval=intervals.get(f),
                conditional=_conditional(f, factors, values),
            )
            for f in factors
        ],
        interactions=_interactions(factors, values),
    )


def _weight(s: int, n: int) -> float:
    """Shapley's weight for a coalition of size `s` out of `n` factors."""
    return factorial(s) * factorial(n - s - 1) / factorial(n)


def _shapley(
    factors: Sequence[str], values: Mapping[frozenset[str], float]
) -> dict[str, float]:
    n = len(factors)
    out: dict[str, float] = {}
    for f in factors:
        rest = [g for g in factors if g != f]
        total = 0.0
        for size in range(len(rest) + 1):
            for subset in combinations(rest, size):
                s = frozenset(subset)
                total += _weight(size, n) * (values[s | {f}] - values[s])
        out[f] = total
    return out


def _conditional(
    factor: str, factors: Sequence[str], values: Mapping[frozenset[str], float]
) -> dict[str, float]:
    """The raw marginal of `factor` in each coalition, unaveraged."""
    rest = [g for g in factors if g != factor]
    out: dict[str, float] = {}
    for size in range(len(rest) + 1):
        for subset in combinations(rest, size):
            s = frozenset(subset)
            label = "+".join(sorted(subset)) if subset else "(baseline)"
            out[label] = values[s | {factor}] - values[s]
    return out


def _interactions(
    factors: Sequence[str], values: Mapping[frozenset[str], float]
) -> list[Interaction]:
    """Pairwise Shapley interaction indices.

    #22 asks whether to report attribution or interaction. Both: the share
    says how much credit a factor earns, and this says whether that credit
    is even meaningful on its own. A large positive index is the table
    telling you to read the conditional sentence instead.
    """
    n = len(factors)
    if n < 2:
        return []
    out: list[Interaction] = []
    for a, b in combinations(factors, 2):
        rest = [g for g in factors if g not in (a, b)]
        total = 0.0
        for size in range(len(rest) + 1):
            for subset in combinations(rest, size):
                s = frozenset(subset)
                delta = (values[s | {a, b}] - values[s | {a}]
                         - values[s | {b}] + values[s])
                total += (factorial(size) * factorial(n - size - 2)
                          / factorial(n - 1)) * delta
        out.append(Interaction(factors=(a, b), index=total))
    return sorted(out, key=lambda i: -abs(i.index))


def _bootstrap_shares(
    factors: Sequence[str],
    cells: Mapping[frozenset[str], Sequence[float]],
    *,
    resamples: int,
    alpha: float = 0.05,
) -> dict[str, Interval]:
    """Percentile bootstrap, resampling runs inside each cell.

    Resampled per cell rather than over the pooled runs, because the design
    is what it is: the number of runs behind each coalition is fixed by the
    matrix, and resampling across cells would invent designs that were never
    run. Deterministically seeded, for the same reason `stats.bootstrap` is.
    """
    rng = random.Random(BOOTSTRAP_SEED)
    draws: dict[str, list[float]] = {f: [] for f in factors}
    keys = sorted(cells, key=lambda k: (len(k), sorted(k)))
    if all(len(cells[k]) < 2 for k in keys):
        return {}

    for _ in range(resamples):
        resampled = {}
        for key in keys:
            data = cells[key]
            n = len(data)
            resampled[key] = sum(data[rng.randrange(n)] for _ in range(n)) / n
        for f, v in _shapley(factors, resampled).items():
            draws[f].append(v)

    out: dict[str, Interval] = {}
    point = _shapley(factors, {k: mean(v) for k, v in cells.items()})
    lo_i = int((alpha / 2) * resamples)
    hi_i = min(resamples - 1, int((1 - alpha / 2) * resamples))
    for f, vals in draws.items():
        vals.sort()
        out[f] = Interval(
            value=point[f], low=vals[lo_i], high=vals[hi_i],
            n=min(len(cells[k]) for k in keys),
            method="bootstrap (exact Shapley, per-cell resample)",
            over="seed and repeat variance within each coalition's cell",
        )
    return out


__all__ = ["Attribution", "FactorShare", "Interaction", "METRICS", "Metric",
           "attribute", "MAX_EXACT_FACTORS"]
