"""The task spec -- the contract a gauntlet run is measured against.

Pre-registration is the point (see issue #15). A spec is compiled once,
hashed, and every run record carries that hash, so the bar demonstrably was
not fitted to whichever variant happened to win.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

import yaml
from pydantic import BaseModel, Field, model_validator


class Oracle(str, Enum):
    """How much ground truth exists for this task.

    Declared, not inferred. It drives how much of the score can be
    mechanical and how wide the reported intervals should be (#2, #15).
    """

    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


class Scenario(BaseModel, frozen=True):
    """One concrete input, with its known-correct answer.

    `expected` is what makes this task's oracle `full`: the harness knows the
    right answer, so scoring needs no judge.
    """

    id: str
    records: dict[str, int]
    """Record id -> quantity. The world this scenario's tools serve."""

    @property
    def expected_total(self) -> int:
        return sum(self.records.values())


class GateCriteria(BaseModel, frozen=True):
    """Pre-registered stability thresholds for the M0 gate (#1, #29).

    Lives inside `TaskSpec` deliberately, so it is covered by
    `fingerprint()`. Moving a threshold changes the hash, and every run
    record carries the hash it was judged against -- which makes a moved
    goalpost detectable rather than deniable. That is the whole mechanism:
    a bar chosen after seeing the number is not a bar.
    """

    min_median_tau: float
    """Median Kendall's tau across seed pairs, at or above which the
    ranking counts as stable."""

    min_top1_stability: float
    """Fraction of seed pairs that must agree on the same winner."""

    min_seeds: int = 3
    """Fewer seeds than this yields too few pairs for a distribution."""

    set_by: str = "unrecorded"
    set_at: str = "unrecorded"
    """Who fixed the bar and when. Recorded so the ordering -- bar first,
    numbers second -- is auditable after the fact."""


class TaskSpec(BaseModel):
    """A task, its scenarios, and the bar it is measured against."""

    id: str
    statement: str
    """Passed verbatim to every variant. Identical across the matrix -- one
    of the fairness invariants."""

    oracle: Oracle
    scenarios: list[Scenario]

    acceptable_degradation: dict[str, Any] = Field(default_factory=dict)
    """The user's error budget (#15, #17). `propagation_rate: 0` is the
    non-negotiable one; the rest are bounds a hypothesis is checked against."""

    tolerance: int = 0
    """Absolute tolerance on the reported total. 0 = exact match required."""

    gate: Optional[GateCriteria] = None
    """Pre-registered gate thresholds. Absent means the gate reports
    numbers without a verdict -- it never invents a bar."""

    @model_validator(mode="after")
    def _check_scenarios(self) -> TaskSpec:
        if not self.scenarios:
            raise ValueError(f"task {self.id!r} declares no scenarios")
        ids = [s.id for s in self.scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError(f"task {self.id!r} has duplicate scenario ids: {ids}")
        if self.oracle is Oracle.FULL and self.tolerance < 0:
            raise ValueError("tolerance must be >= 0")
        return self

    def scenario(self, scenario_id: str) -> Scenario:
        for s in self.scenarios:
            if s.id == scenario_id:
                return s
        raise KeyError(f"no scenario {scenario_id!r} in task {self.id!r}")

    def fingerprint(self) -> str:
        """Stable hash of everything that defines the bar.

        Any edit to the statement, the scenarios, the oracle, the tolerance,
        or the error budget produces a different fingerprint -- which is how
        a run record proves which bar it was scored against.
        """
        payload = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> TaskSpec:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)


class VariantSpec(BaseModel, frozen=True):
    """One point in the factor space, plus where its `common/` folder lives."""

    id: str
    common_dir: Optional[str] = None
    """Path to the generated/hand-written `common/` project. None for
    variants exercised only through the offline fake agent."""

    entry_agent: str = "auditor"
    factors: dict[str, str] = Field(default_factory=dict)
    """The named factor settings this variant represents -- model, prompt
    strategy, tool set. Attribution (#22) is computed over these."""

    is_sentinel: bool = False
    """A deliberately degraded variant. If the board does not rank it last,
    the instrument is broken -- see issue #29. Excluded from the ranking
    under test; used to validate it."""
