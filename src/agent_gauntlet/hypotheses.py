"""Steady-state hypotheses: falsifiable bounds instead of a score (#17).

Chaos engineering does not inject a fault and see what happens. It states a
falsifiable claim about a steady-state metric first -- "checkout success
stays above 99.2% when we kill an availability zone" -- and then tries to
disprove it. The artifact is a broken promise, not a number.

Applied here, the difference is what an operator is handed:

    robustness: 0.71
    integrity-containment FALSIFIED -- propagated in 3 of 10 runs
      (run ids listed, so the three can be read)

Three things this module is careful about, all of them corrections to the
obvious version.

**The bounds are checked on the board's own denominators.** Every metric a
bound can name comes from `attribution.METRICS`, which the board and the
Shapley table already share. A hypothesis that did its own arithmetic could
be upheld while the board beside it disagrees, which is the one thing a
falsifiable statement must never do.

**Upholding a zero-bound is not proof of zero.** "Never propagated" over 10
runs is consistent with a true rate near 26%. A bound of exactly zero is
falsified by a single run and can never be *confirmed* by finitely many, so
an upheld zero-bound reports the Wilson ceiling rather than a tick. The
rule of three, said out loud.

**A bound held by a hair is not a bound held.** #34's lesson: at n runs a
rate difference smaller than `detectable_difference(n)` was never
observable, so "detection 91% >= 90%" at n=12 is a coin flip wearing a
verdict. Those are marked, not silently counted as passes.

Not aggregated, deliberately. #17 asks whether a config failing one
hypothesis and passing six ranks above one with the reverse profile, and
the honest answer is that hypotheses resist aggregation by design. Summing
them into a score would undo the entire reason for stating them.
"""

from __future__ import annotations

from enum import Enum
from statistics import mean
from typing import Optional, Sequence

from pydantic import BaseModel, Field

from .attribution import METRICS, Metric
from .ledger import RunRecord
from .spec import Bound, Hypothesis
from .stats import detectable_difference, wilson

MAX_NAMED_RUNS = 5
"""How many offending run ids to print.

The point of naming them is that somebody can go and read one. A list of
three hundred is a number again, so the rest are counted rather than
listed.
"""


class Verdict(str, Enum):
    UPHELD = "upheld"
    FALSIFIED = "falsified"
    NOT_TESTED = "not_tested"
    """No run could have tested it -- a censored denominator, never a pass.

    The distinction this project keeps having to make: "nothing violated
    it" and "nothing could have violated it" are different claims, and only
    one of them is about the configuration.
    """


def _detected_within(steps: int) -> Metric:
    """Rate of runs that noticed the fault within `steps` of the evidence
    becoming available.

    Its denominator is the detection denominator, not every faulted run: a
    config whose tool set holds no cross-check could not have noticed at
    all, and scoring that as a late detection would blame it for a
    capability it was never given (#16).
    """
    base = METRICS["detection_rate"]
    return Metric(
        name=f"detected_within_{steps}",
        include=base.include,
        value=lambda r: float(
            r.score.detected
            and r.score.detect_latency is not None
            and r.score.detect_latency <= steps
        ),
        note=base.note,
    )


def metric_for(bound: Bound) -> Optional[Metric]:
    if bound.metric == "detected_within":
        return _detected_within(int(bound.steps or 0))
    return METRICS.get(bound.metric)


class BoundResult(BaseModel):
    metric: str
    says: str
    verdict: Verdict
    observed: Optional[float] = None
    n: int = 0
    n_offending: int = 0
    offending: list[str] = Field(default_factory=list)
    """Ids of the runs that failed this bound's per-run predicate, capped at
    `MAX_NAMED_RUNS`. The falsifying artifact #17 asks for."""
    ceiling: Optional[float] = None
    """For an upheld bound of exactly zero: the Wilson upper bound on the
    true rate. "Never, in n runs" is not "never"."""
    resolution: Optional[float] = None
    too_close_to_call: bool = False
    """Upheld, but by less than this many runs could have resolved."""


class HypothesisResult(BaseModel):
    variant_id: str
    hypothesis_id: str
    says: str
    verdict: Verdict
    bounds: list[BoundResult] = Field(default_factory=list)

    @property
    def falsified_by(self) -> list[BoundResult]:
        return [b for b in self.bounds if b.verdict is Verdict.FALSIFIED]


def evaluate(
    hypothesis: Hypothesis, runs: Sequence[RunRecord], *, variant_id: str
) -> HypothesisResult:
    """Check one hypothesis against one configuration's runs."""
    results = [_check(bound, runs) for bound in hypothesis.bounds]

    if any(b.verdict is Verdict.FALSIFIED for b in results):
        verdict = Verdict.FALSIFIED
    elif any(b.verdict is Verdict.NOT_TESTED for b in results):
        # A conjunction with an untested clause is an untested conjunction.
        # Reporting it upheld would mean "we checked", and we did not.
        verdict = Verdict.NOT_TESTED
    else:
        verdict = Verdict.UPHELD

    return HypothesisResult(
        variant_id=variant_id, hypothesis_id=hypothesis.id,
        says=hypothesis.says.strip(), verdict=verdict, bounds=results,
    )


def _check(bound: Bound, runs: Sequence[RunRecord]) -> BoundResult:
    spec = metric_for(bound)
    if spec is None:
        return BoundResult(
            metric=bound.metric,
            says=f"unknown metric {bound.metric!r}; known: {sorted(METRICS)} "
                 "+ detected_within",
            verdict=Verdict.NOT_TESTED,
        )

    eligible = [r for r in runs if spec.include(r)]
    if not eligible:
        return BoundResult(
            metric=bound.metric, says=bound.says(), verdict=Verdict.NOT_TESTED,
            n=0,
        )

    values = [spec.value(r) for r in eligible]
    observed = mean(values)
    n = len(eligible)

    offenders = [
        r for r, v in zip(eligible, values)
        if (bound.max is not None and v > bound.max)
        or (bound.min is not None and v < bound.min)
    ]

    broken = (
        (bound.max is not None and observed > bound.max)
        or (bound.min is not None and observed < bound.min)
    )
    verdict = Verdict.FALSIFIED if broken else Verdict.UPHELD

    result = BoundResult(
        metric=bound.metric, says=bound.says(), verdict=verdict,
        observed=observed, n=n, n_offending=len(offenders),
        offending=[r.run_id for r in offenders[:MAX_NAMED_RUNS]],
    )

    if verdict is not Verdict.UPHELD:
        return result

    if bound.max == 0 and not offenders:
        # Rule of three, properly: zero events in n runs bounds the true
        # rate, it does not establish it.
        ci = wilson(0, n)
        result.ceiling = ci.high if ci else None
        return result

    resolution = detectable_difference(n)
    result.resolution = resolution
    if resolution is not None:
        slack = min(
            abs(observed - t)
            for t in (bound.max, bound.min) if t is not None
        )
        result.too_close_to_call = slack < resolution
    return result


def check_all(
    hypotheses: Sequence[Hypothesis], records: Sequence[RunRecord]
) -> list[HypothesisResult]:
    """Every hypothesis against every configuration, ungrouped and
    unaggregated. The caller prints the profile; nothing here scores it."""
    by_variant: dict[str, list[RunRecord]] = {}
    for r in records:
        by_variant.setdefault(r.variant_id, []).append(r)
    return [
        evaluate(h, runs, variant_id=vid)
        for vid, runs in sorted(by_variant.items())
        for h in hypotheses
    ]


__all__ = ["BoundResult", "HypothesisResult", "Verdict", "check_all",
           "evaluate", "metric_for", "MAX_NAMED_RUNS"]
