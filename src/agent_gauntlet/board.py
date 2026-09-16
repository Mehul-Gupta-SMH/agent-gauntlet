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
from statistics import mean, median
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

    repair_rate: Optional[float] = None
    """Of the runs where a lie reached the agent and a cross-check existed
    to catch it with, how many ended with the right answer anyway.

    None -- never 0.0 -- when nothing was repairable: no exposure, or no
    reachable evidence. Corruptions are plausible by design, so without a
    cross-check repair is impossible *in principle*, and those runs leave
    the denominator exactly as they leave detection's (#16).
    """

    repair_quality: Optional[float] = None
    """The graded companion: mean accuracy over the same denominator.

    Mirrors `correct` / `accuracy`. A variant that halves the damage is not
    the same as one that swallows it whole, and a binary repaired/not
    cannot say so.
    """

    n_repair_eligible: int = 0
    """The denominator, printed so a rate from two runs is legible as one."""

    false_alarm_rate: float
    accuracy: float = 0.0
    """Graded quality. Ranking uses this: binary correctness ties too
    readily to correlate across seeds (#17 vs #29 correction 3)."""
    cost_usd: float
    total_tokens: int = 0
    cost_complete: bool = False
    """Whether every LLM call in this variant's runs reported a price.

    False means the number below it is a floor, not a cost, and the board
    says so rather than printing a confident understatement. An offline
    variant spends nothing and is also False -- "not measured" and
    "measured as zero" must not render identically (#14).
    """

    median_detect_latency: Optional[float] = None
    """Steps from evidence becoming reachable to detection, over the runs
    that detected. None when nothing detected -- those runs are censored,
    not zero-latency, and averaging them in rewards failing fast (#16)."""

    n_detect_latency: int = 0
    """How many runs the median above is computed from. A median of one is
    not a distribution, and the reader can see that here."""

    mean_steps: float
    n_errored: int = 0
    """Runs that never produced an answer -- a provider error, a crash.

    Excluded from every rate above rather than scored as wrong. An agent
    that never got to answer is not an agent that answered badly, and
    counting infrastructure failures against a configuration would make the
    board a measure of the weather. Reported so the exclusion is visible
    rather than silent.
    """

    n_propagation_undecidable: int = 0
    """Faulted runs where the corruption was too small to separate a
    propagated answer from an honest miscount. Reported, never folded into
    the rate as a zero."""

    @property
    def label(self) -> str:
        """Factor values, in a fixed order, for a board a human can scan.

        `model-cheap__prompt-naive__toolset-records+summary` is precise and
        unreadable at nine rows; the factor names are constant down the
        column and carry no information there.
        """
        order = ("model", "prompt", "toolset")
        known = [self.factors[k] for k in order if k in self.factors]
        rest = [v for k, v in sorted(self.factors.items()) if k not in order]
        return " ".join(known + rest) or self.variant_id

    @property
    def cost_per_run(self) -> Optional[float]:
        """Spend per run, or None when nothing priced it."""
        if not self.cost_complete or not self.n_runs:
            return None
        return self.cost_usd / self.n_runs

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
    for vid, all_runs in sorted(by_variant.items()):
        errored = [r for r in all_runs if r.error]
        # Censored, exactly like unreachable evidence: the question was
        # never put to the agent, so there is no answer to grade.
        runs = [r for r in all_runs if not r.error]
        if not runs:
            continue
        clean = [r for r in runs if r.condition == "clean"]
        faulted = [r for r in runs if r.condition == "faulted"]
        with_evidence = [r for r in faulted if r.score.evidence_available]
        decidable = [r for r in faulted if r.score.propagation_determinable]
        # Repair is only askable where a lie actually reached the agent AND
        # something existed to catch it with. Anything else is censored.
        repairable = [
            r for r in with_evidence
            if any(c.get("faulted") for c in r.tool_calls)
        ]

        results.append(
            VariantResult(
                variant_id=vid,
                factors=dict(runs[0].factors),
                n_runs=len(runs),
                quality=mean(float(r.score.correct) for r in runs),
                accuracy=mean(float(r.score.accuracy) for r in runs),
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
                    mean(float(r.score.propagated) for r in decidable)
                    if decidable else 0.0
                ),
                n_errored=len(errored),
                n_propagation_undecidable=len(faulted) - len(decidable),
                repair_rate=(
                    mean(float(r.score.repaired) for r in repairable)
                    if repairable else None
                ),
                repair_quality=(
                    mean(float(r.score.accuracy) for r in repairable)
                    if repairable else None
                ),
                n_repair_eligible=len(repairable),
                # None, not 0.0: no reachable evidence means "not measured",
                # which is a different claim from "never detected".
                detection_rate=(
                    mean(float(r.score.detected) for r in with_evidence)
                    if with_evidence else None
                ),
                false_alarm_rate=(
                    mean(float(r.score.false_alarm) for r in clean) if clean else 0.0
                ),
                cost_usd=sum(_llm(r).get("cost_usd", 0.0) or 0.0 for r in runs),
                total_tokens=sum(
                    int(_llm(r).get("total_tokens", 0) or 0) for r in runs
                ),
                # Every run must have reported a price, or the total is a
                # floor. commonadk reports this per trace; a variant whose
                # runs never priced anything (offline) is False too.
                cost_complete=bool(runs) and all(
                    _llm(r).get("cost_complete") for r in runs
                ),
                median_detect_latency=(
                    median(_latencies(runs)) if _latencies(runs) else None
                ),
                n_detect_latency=len(_latencies(runs)),
                mean_steps=mean(float(r.score.steps) for r in runs),
            )
        )
    return results


def _llm(record: RunRecord) -> dict:
    """The `llm_calls` roll-up for one run, or an empty dict offline."""
    block = record.rollup.get("llm_calls") if record.rollup else None
    return block if isinstance(block, dict) else {}


def _latencies(runs: Sequence[RunRecord]) -> list[int]:
    """Time-to-detect over the runs that actually detected.

    Runs that never detected are right-censored -- there is no latency to
    average, and substituting one (0, or the run length) would reward an
    agent that gives up immediately (#16).
    """
    return [
        r.score.detect_latency for r in runs
        if r.score.detect_latency is not None
    ]


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
        tiers[round(r.accuracy, 4)].append(r)
    return [
        sorted(tiers[q], key=lambda r: r.variant_id)
        for q in sorted(tiers, reverse=True)
    ]


def winner(
    results: Sequence[VariantResult],
    *,
    objectives: Sequence[str] = ("accuracy", "false_alarm_rate"),
    maximize: Sequence[bool] = (True, False),
) -> Optional[VariantResult]:
    """The single non-dominated, ungated config -- or None.

    Deliberately *not* "the top of the accuracy ranking". Two configs can
    tie on accuracy and differ sharply on how often they cry wolf, and a
    ranking on one axis throws that away. Domination uses the extra axis
    without inventing weights for it: a config wins only if nothing is at
    least as good everywhere and better somewhere.

    Returns None when several configs remain non-dominated. That is a real
    answer -- the tradeoff is the user's to make (#11) -- not a failure to
    compute one.
    """
    eligible = [r for r in results if not r.gated and not r.is_sentinel]
    if not eligible:
        return None
    front = pareto(eligible, objectives=objectives, maximize=maximize)
    return front[0] if len(front) == 1 else None


class HeldOutWinner(BaseModel):
    """A winner, and the score it earns on data that did not choose it.

    The winner's curse (#20): picking the best of N on a set of runs and
    then quoting *that* run's score reports the maximum of N noisy
    estimates, which is biased upward by construction. The more variants
    the search covers, the worse it gets -- so the board's headline number
    would look best exactly when the search was widest.

    The fix is not a correction factor. It is refusing to report a number
    the selection could see: `holdout_score` is measured on replications
    held out of the choice entirely.
    """

    variant_id: str
    selection_seeds: list[str]
    holdout_seeds: list[str]

    selection_score: float
    """What it scored on the runs that chose it. NOT the number to quote."""

    holdout_score: float
    """What it scores on replications the selection never saw. This is the
    reportable number."""

    holdout_rank: int
    """Where it lands on the held-out data, 1-based, sentinels excluded. A
    winner that falls to 4th was noise, however good its selection score."""

    n_candidates: int

    @property
    def optimism(self) -> float:
        """Selection minus held-out: the winner's curse, in points.

        Positive is the expected direction and not a defect -- it is the
        bias being measured instead of shipped. A large gap means the
        search fit noise; report it next to the score rather than hiding
        it (#20).
        """
        return self.selection_score - self.holdout_score

    @property
    def held_up(self) -> bool:
        """Did the selection survive contact with fresh replications?"""
        return self.holdout_rank == 1


def split_seeds(seeds: Sequence[str], *, holdout: int = 1) -> tuple[list[str], list[str]]:
    """Partition replications into selection and held-out sets.

    Sorted, then split from the end, so the partition is deterministic and
    reproducible from the ledger alone -- a random split would make the
    reported score depend on an unrecorded choice.
    """
    ordered = sorted(set(seeds))
    if holdout < 1:
        raise ValueError("holdout must be >= 1")
    if len(ordered) <= holdout:
        raise ValueError(
            f"need more than {holdout} seed(s) to hold one out; got {len(ordered)}"
        )
    return ordered[:-holdout], ordered[-holdout:]


def held_out_winner(
    records: Iterable[RunRecord],
    *,
    sentinels: Optional[Sequence[str]] = None,
    holdout: int = 1,
) -> Optional[HeldOutWinner]:
    """Select a winner on some replications; score it on the others.

    Returns None when no unique winner emerges on the selection half --
    a tied front is a real answer (#11), and there is nothing to score.
    """
    runs = list(records)
    try:
        selection, held = split_seeds([r.base_seed for r in runs], holdout=holdout)
    except ValueError:
        return None

    sentinel_ids = set(sentinels or ())

    def _summarize(keep: set[str]) -> list[VariantResult]:
        out = summarize([r for r in runs if r.base_seed in keep])
        for row in out:
            row.is_sentinel = row.variant_id in sentinel_ids
        return out

    chosen = winner(_summarize(set(selection)))
    if chosen is None:
        return None

    held_results = _summarize(set(held))
    ranked = [r for tier in rank(held_results, exclude_sentinels=True) for r in tier]
    match = next((r for r in held_results if r.variant_id == chosen.variant_id), None)
    if match is None:
        return None

    return HeldOutWinner(
        variant_id=chosen.variant_id,
        selection_seeds=list(selection),
        holdout_seeds=list(held),
        selection_score=chosen.accuracy,
        holdout_score=match.accuracy,
        holdout_rank=1 + next(
            i for i, r in enumerate(ranked) if r.variant_id == chosen.variant_id
        ),
        n_candidates=sum(
            1 for r in held_results if not r.is_sentinel and not r.gated
        ),
    )


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
