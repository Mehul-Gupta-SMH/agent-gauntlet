"""Scripted agent policies -- a test double for the harness, not for agents.

These let the whole pipeline (schedule -> tools -> trace -> score ->
stability) run end-to-end with no API key and no cost, which is what makes
the harness testable in CI. They are emphatically **not** evidence about
how real agents behave: a scripted policy has none of the variance that
makes issue #1 a hard question in the first place. Their only job is to
prove the measurement machinery does what it claims on inputs whose correct
grading is known in advance.

Real execution goes through `commonadk.runners.get_runner(target)`, which
returns a `Trace` carrying token counts, `cost_usd` and durations.
"""

from __future__ import annotations

import hashlib
import random
from typing import Callable, Optional

from .interpose import (
    RunContext, ToolTimeout, ToolUnavailable,
    fetch_quantity, list_record_ids, list_record_ids_partial, summary_total,
)
from .score import Answer

Policy = Callable[..., Answer]

SLIP_RATE = 0.15
MODEL_SLIP = {"cheap": 0.22, "smart": 0.08}
"""Per-model slip rates.

The model axis has to *do* something offline or it is dead weight in the
factor grid -- a level that cannot move the score teaches nothing about
attribution. A weaker model miscounting more often is the least contrived
stand-in available.
"""
"""How often a policy miscounts.

Real agents are stochastic; perfectly deterministic stand-ins make top-1
stability meaningless, because every seed produces an identical ranking and
the gate can never fail. A seeded slip gives the offline path enough
variance to exercise the stability machinery -- while staying exactly
reproducible from the run seed, which determinism requires.
"""


def _slip(seed: Optional[str], magnitude: int, rate: float = SLIP_RATE) -> int:
    """A reproducible occasional miscount, derived from the run seed."""
    if not seed or magnitude <= 0:
        return 0
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    if rng.random() >= rate:
        return 0
    return rng.choice([-1, 1]) * max(1, int(magnitude * rng.uniform(0.02, 0.08)))


def _enumerate() -> list[str]:
    """Whatever enumeration this variant's tool set actually grants.

    A variant holding only `list_records_sample` sees half the records and
    cannot know the rest exist -- that is the structural sentinel (#29), and
    it degrades the *policy* here exactly as it degrades a live model, so
    the offline and live paths test the same mechanism rather than two
    different ones.
    """
    try:
        return list_record_ids()
    except ToolUnavailable:
        pass
    try:
        return list_record_ids_partial()
    except ToolUnavailable:
        return []


def naive(seed: Optional[str] = None, rate: float = SLIP_RATE) -> Answer:
    """Fetch every record, sum, report. Never cross-checks.

    Under a WRONG_VALUE fault this propagates the falsehood -- the failure
    the gauntlet exists to catch.
    """
    total = 0
    for rid in _enumerate():
        try:
            total += fetch_quantity(rid)
        except ToolTimeout:
            continue
    return Answer(total=total + _slip(seed, total, rate), flagged_anomaly=False)


def verifying(seed: Optional[str] = None, rate: float = SLIP_RATE) -> Answer:
    """Sum the records, then reconcile against the independent summary.

    The positive control (#29): it *should* beat `naive` under faults. If a
    difference engineered this deliberately fails to show up, the instrument
    cannot detect subtler ones either.
    """
    total = 0
    for rid in _enumerate():
        try:
            total += fetch_quantity(rid)
        except ToolTimeout:
            continue
    total += _slip(seed, total, rate)
    try:
        cross_check = summary_total()
    except ToolUnavailable:
        # No cross-check granted: this variant cannot verify anything, and
        # degrades to the credulous path. That is the toolset factor doing
        # real work rather than labelling.
        return Answer(total=total, flagged_anomaly=False)
    if cross_check != total:
        return Answer(total=cross_check, flagged_anomaly=True)
    return Answer(total=total, flagged_anomaly=False)


def summary_only(seed: Optional[str] = None, rate: float = SLIP_RATE) -> Answer:
    """Read the summary and report it, never touching per-record data.

    Immune to fetch-level corruption -- but immune by inattention, not by
    care. It produces UNDETECTED_HARMLESS, the outcome that looks like
    success and is not, and exists here so scoring is tested against it.
    """
    try:
        total = summary_total()
    except ToolUnavailable:
        return Answer(total=None, flagged_anomaly=False)
    return Answer(total=total + _slip(seed, total, rate), flagged_anomaly=False)


POLICIES: dict[str, Policy] = {
    "naive": naive,
    "verifying": verifying,
    "summary_only": summary_only,
}
"""No `sentinel` policy.

There was one, and it hid the defect experiment 003 found. It truncated the
record list *itself*, so it ranked last offline while the live sentinel --
degraded only by a prompt telling it to hurry -- ranked 6th of 9. The
harness was testing one mechanism and shipping another.

The sentinel is now a plain `naive` policy on the `records-partial` tool
set, which truncates enumeration below the variant. Same mechanism offline
and live, and a capable model has no more say in it than this policy does.
"""


def run_policy(
    name: str, seed: Optional[str] = None, model: Optional[str] = None
) -> Answer:
    try:
        policy = POLICIES[name]
    except KeyError:
        raise KeyError(f"unknown policy {name!r}; known: {sorted(POLICIES)}") from None
    return policy(seed, MODEL_SLIP.get(model or "", SLIP_RATE))


__all__ = ["POLICIES", "Policy", "RunContext", "run_policy", "SLIP_RATE",
           *sorted(POLICIES)]
