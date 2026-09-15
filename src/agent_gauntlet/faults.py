"""Seeded, deterministic fault schedules.

Why this lives here and not in a CommonADK hook: `commonadk.runners.hooks`
is **observe-only by deliberate design** -- a callback receives an Event
strictly after it happened and "cannot block a tool call, rewrite a reported
token count, veto a transfer, or otherwise change the run in progress."
Fault injection needs to *change* a tool result, so it is applied one layer
lower, inside the tool function itself (see `interpose.py`). That works
identically on all six SDK targets because it sits below the adapter.

Determinism is the contract: the same `FaultSchedule` seed produces the
same faults, in the same order, every time. A run that cannot be replayed
is not a result (plan.md, `ledger`).
"""

from __future__ import annotations

import hashlib
import random
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class FaultKind(str, Enum):
    """M0 ships two. The taxonomy proper is issue #5.

    `WRONG_VALUE` is the one that matters -- the agent believes the call
    succeeded and receives a falsehood. `TIMEOUT` is the cheap contrast: a
    loud failure, trivially detected, included so the two can be compared.
    """

    WRONG_VALUE = "wrong_value"
    TIMEOUT = "timeout"


class InjectedFault(BaseModel, frozen=True):
    """One fault, decided up front rather than sampled at call time."""

    kind: FaultKind
    tool_name: str
    target_key: str
    """Which record/argument this fault applies to."""
    true_value: Optional[int] = None
    corrupt_value: Optional[int] = None

    @property
    def delta(self) -> int:
        """How much the corruption shifts an aggregate that trusts it."""
        if self.true_value is None or self.corrupt_value is None:
            return 0
        return self.corrupt_value - self.true_value


class FaultSchedule(BaseModel):
    """The faults for one run, derived deterministically from a seed.

    `evidence_available_after` records the step index at which a cross-check
    first becomes reachable. Time-to-detect is measured from *there*, not
    from injection -- a fault contradicting nothing observable is
    undetectable by anyone and measures nothing (issues #5, #16).
    """

    seed: str
    faults: list[InjectedFault] = Field(default_factory=list)
    evidence_tool: Optional[str] = None
    """The tool whose output contradicts the corrupted value. None means no
    evidence exists, and detection metrics are undefined for this run."""

    @property
    def is_clean(self) -> bool:
        return not self.faults

    def for_tool(self, tool_name: str, key: str) -> Optional[InjectedFault]:
        for f in self.faults:
            if f.tool_name == tool_name and f.target_key == key:
                return f
        return None

    @property
    def total_delta(self) -> int:
        """The shift a fully credulous agent's aggregate would show."""
        return sum(f.delta for f in self.faults)

    @classmethod
    def clean(cls, seed: str) -> FaultSchedule:
        """The counterfactual half of a pair: same seed, no faults.

        Robustness is degradation from a variant's *own* clean baseline, so
        every faulted run needs this twin (plan.md, `chaos`).
        """
        return cls(seed=seed, faults=[])

    @classmethod
    def build(
        cls,
        *,
        seed: str,
        records: dict[str, int],
        kind: FaultKind = FaultKind.WRONG_VALUE,
        tool_name: str = "fetch_record",
        evidence_tool: Optional[str] = "get_summary",
    ) -> FaultSchedule:
        """Derive a schedule from `seed` and the scenario's records.

        The corrupted value is *plausible* -- same order of magnitude, never
        negative, never equal to the truth. An implausible value would be
        detectable by inspection alone, which would measure alertness to
        absurdity rather than to falsehood.
        """
        if not records:
            raise ValueError("cannot build a fault schedule over zero records")

        rng = random.Random(_stable_seed(seed))
        key = sorted(records)[rng.randrange(len(records))]
        true_value = records[key]

        if kind is FaultKind.TIMEOUT:
            return cls(
                seed=seed,
                faults=[
                    InjectedFault(kind=kind, tool_name=tool_name, target_key=key)
                ],
                evidence_tool=evidence_tool,
            )

        corrupt = _plausible_corruption(rng, true_value)
        return cls(
            seed=seed,
            faults=[
                InjectedFault(
                    kind=kind,
                    tool_name=tool_name,
                    target_key=key,
                    true_value=true_value,
                    corrupt_value=corrupt,
                )
            ],
            evidence_tool=evidence_tool,
        )


def _stable_seed(seed: str) -> int:
    """Hash the seed string to an int.

    `hash()` is salted per process, so using it would make runs
    irreproducible across processes -- the one thing this module exists to
    prevent.
    """
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16)


def _plausible_corruption(rng: random.Random, true_value: int) -> int:
    """A wrong value that looks like it could have been right."""
    for _ in range(16):
        factor = rng.choice([0.4, 0.5, 0.6, 1.5, 1.7, 2.0])
        candidate = max(1, int(round(true_value * factor)))
        if candidate != true_value:
            return candidate
    return true_value + max(1, true_value // 2)
