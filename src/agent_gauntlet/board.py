"""The board: rank, find the frontier, attribute, and export the winner.

Two deliberate refusals here, both inherited from the design:

- **No single hidden scalar.** `pareto` returns the non-dominated set and
  leaves the tradeoff with the reader (#11). A scalarisation is available
  but must be asked for, with its weights visible.
- **No winner when the top is tied.** `rank` reports ties as ties. Breaking
  one arbitrarily would manufacture a result the data does not support.

Attribution here is one-at-a-time marginal effects, which are *wrong under
factor interaction* -- the honest treatment is Shapley (#22) and this slice
does not attempt it. The function says so in its return value rather than
quietly implying more than it knows.
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Iterable, Optional, Sequence, Union

from pydantic import BaseModel, Field

from .ledger import RunRecord
from .spec import VariantSpec


class VariantResult(BaseModel):
    """One variant's aggregate across the matrix."""

    variant_id: str
    factors: dict[str, str] = Field(default_factory=dict)
    is_sentinel: bool = False

    n_runs: int
    quality: float
    """Mean correctness across every run, clean and faulted.

    Not faulted-only: under fault alone a credulous-but-competent variant
    and a broken one both sit at zero, and the clean half is what separates
    them (see tests/test_matrix.py)."""

    clean_quality: float
    faulted_quality: float
    propagation_rate: float
    detection_rate: Optional[float]
    false_alarm_rate: float
    cost_usd: float
    mean_steps: float

    @property
    def robustness_drop(self) -> float:
        """Degradation from this variant's *own* clean baseline."""
        return self.clean_quality - self.faulted_quality

    @property
    def gated(self) -> bool:
        """Propagation is a gate, not a score (#16).

        Any run where an injected falsehood reached the answer disqualifies
        the config regardless of how well it scores elsewhere.
        """
        return self.propagation_rate > 0


def summarize(records: Iterable[RunRecord]) -> list[VariantResult]:
    """Aggregate a ledger into one row per variant."""
    by_variant: dict[str, list[RunRecord]] = defaultdict(list)
    for r in records:
        by_variant[r.variant_id].append(r)

    results: list[VariantResult] = []
    for vid, runs in sorted(by_variant.items()):
        clean = [r for r in runs if r.condition == "clean"]
        faulted = [r for r in runs if r.condition == "faulted"]
        with_evidence = [r for r in faulted if r.score.evidence_available]

        results.append(
            VariantResult(
                variant_id=vid,
                factors=dict(runs[0].factors),
                n_runs=len(runs),
                quality=mean(float(r.score.correct) for r in runs),
                clean_quality=mean(float(r.score.correct) for r in clean) if clean else 0.0,
                faulted_quality=(
                    mean(float(r.score.correct) for r in faulted) if faulted else 0.0
                ),
                # Over ALL faulted runs, never only the evidence-reachable
                # ones. Whether a falsehood reached the answer is checkable
                # regardless of whether a cross-check existed -- the
                # evidence denominator belongs to *detection* alone. Scoping
                # propagation to it lets the worst kind of variant (one that
                # propagates AND cannot detect) read 0% and slip the gate.
                propagation_rate=(
                    mean(float(r.score.propagated) for r in faulted)
                    if faulted else 0.0
                ),
                # None, not 0.0: no reachable evidence means "not measured",
                # which is a different claim from "never detected".
                detection_rate=(
                    mean(float(r.score.detected) for r in with_evidence)
                    if with_evidence else None
                ),
                false_alarm_rate=(
                    mean(float(r.score.false_alarm) for r in clean) if clean else 0.0
                ),
                cost_usd=sum(
                    float(r.rollup.get("llm_calls", {}).get("cost_usd", 0.0) or 0.0)
                    for r in runs
                ),
                mean_steps=mean(float(r.score.steps) for r in runs),
            )
        )
    return results


def rank(
    results: Sequence[VariantResult], *, exclude_sentinels: bool = False
) -> list[list[VariantResult]]:
    """Rank by quality, returning **tiers**: each inner list is a tie group.

    Returning tiers rather than a flat order is the point -- a caller that
    wants "the winner" has to notice when there isn't one.
    """
    pool = [r for r in results if not (exclude_sentinels and r.is_sentinel)]
    tiers: dict[float, list[VariantResult]] = defaultdict(list)
    for r in pool:
        tiers[r.quality].append(r)
    return [
        sorted(tiers[q], key=lambda r: r.variant_id)
        for q in sorted(tiers, reverse=True)
    ]


def winner(results: Sequence[VariantResult]) -> Optional[VariantResult]:
    """The single best config, or None if the top tier is tied or gated."""
    tiers = rank(results)
    if not tiers:
        return None
    top = [r for r in tiers[0] if not r.gated]
    return top[0] if len(top) == 1 else None


def pareto(
    results: Sequence[VariantResult],
    *,
    objectives: Sequence[str] = ("quality", "cost_usd"),
    maximize: Sequence[bool] = (True, False),
) -> list[VariantResult]:
    """The non-dominated set over the named objectives.

    A config is dominated when another is at least as good on every
    objective and strictly better on one.
    """
    if len(objectives) != len(maximize):
        raise ValueError("objectives and maximize must be the same length")

    def better(a: VariantResult, b: VariantResult) -> bool:
        at_least = True
        strictly = False
        for obj, mx in zip(objectives, maximize):
            av, bv = float(getattr(a, obj)), float(getattr(b, obj))
            if mx:
                at_least &= av >= bv
                strictly |= av > bv
            else:
                at_least &= av <= bv
                strictly |= av < bv
        return at_least and strictly

    return [
        r for r in results
        if not any(better(other, r) for other in results if other is not r)
    ]


class FactorEffect(BaseModel):
    factor: str
    levels: dict[str, float]
    spread: float
    """Best level minus worst. A crude importance proxy."""
    caveat: str = (
        "One-at-a-time marginal effect. Wrong under factor interaction; "
        "Shapley attribution is issue #22."
    )


def factor_effects(results: Sequence[VariantResult]) -> list[FactorEffect]:
    """Mean quality per level of each factor.

    Honest only when factors do not interact, which is rarely true. The
    caveat rides along with the numbers so it cannot be read without it.
    """
    by_factor: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in results:
        if r.is_sentinel:
            continue  # a deliberately broken config would skew every level
        for factor, level in r.factors.items():
            by_factor[factor][level].append(r.quality)

    effects: list[FactorEffect] = []
    for factor, levels in sorted(by_factor.items()):
        means = {lvl: mean(vals) for lvl, vals in sorted(levels.items())}
        if not means:
            continue
        effects.append(
            FactorEffect(
                factor=factor,
                levels=means,
                spread=max(means.values()) - min(means.values()),
            )
        )
    return sorted(effects, key=lambda e: e.spread, reverse=True)


def export_winner(
    result: VariantResult,
    variants: Sequence[VariantSpec],
    dest: Union[str, Path],
) -> Path:
    """Copy the winning variant's `common/` folder out as the deliverable.

    This is the product: not a score, a project you can hand to
    `commonadk` and run.
    """
    spec = next((v for v in variants if v.id == result.variant_id), None)
    if spec is None or not spec.common_dir:
        raise ValueError(f"variant {result.variant_id!r} has no common/ folder to export")

    src = Path(spec.common_dir)
    if not src.is_dir():
        raise FileNotFoundError(f"missing common/ folder: {src}")

    out = Path(dest)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(src, out)
    return out
