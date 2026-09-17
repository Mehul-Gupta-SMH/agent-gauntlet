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

    BASELINE = "baseline"
    """No label, but a usable oracle anyway.

    The right answer is unknown; the answer this same variant gave on its own
    clean run is not. Propagation -- the gate -- is "did the injected delta
    move the answer away from where this variant put it without the lie",
    which needs no label at all.

    Accuracy still does, and reads `n/a` under this oracle rather than being
    quietly redefined as agreement with a baseline. The two are different
    claims and a board that printed one under the other's heading would be
    the exact failure this project is about.
    """


class Scenario(BaseModel, frozen=True):
    """One concrete input, with its known-correct answer.

    `expected` is what makes this task's oracle `full`: the harness knows the
    right answer, so scoring needs no judge.
    """

    id: str
    records: dict[str, int]
    """Record id -> quantity. The world this scenario's tools serve."""

    audited: list[str] = Field(default_factory=list)
    """Which records the independent cross-check covers. Empty = all of them.

    Partial coverage is what gives the task any headroom at all. When the
    cross-check covers everything it *is* the answer, so a variant that can
    reach it scores 1.00 deterministically and every such variant ties --
    which is exactly how experiment 003's gate failed: perfect rank
    stability (tau 1.0) and no unique winner to be stable about.

    With partial coverage the cross-check verifies a subset and the variant
    still has to do the rest of the work itself, so there is something for
    model capability to show up in.
    """

    expected: Optional[int] = None
    """The known-correct answer, when it is not simply the sum of the world.

    A project whose task is not a total supplies it here. Absent, and with a
    `full` oracle, the sum stands -- which is what every bundled fixture
    means. Joins `fingerprint()` only when set, so no existing hash moves.
    """

    @property
    def expected_total(self) -> int:
        return self.expected if self.expected is not None else sum(self.records.values())

    @property
    def audited_ids(self) -> list[str]:
        """The covered record ids, defaulting to all of them."""
        return sorted(self.audited) if self.audited else sorted(self.records)

    @property
    def audited_total(self) -> int:
        """What the independent cross-check reports. Not the answer, unless
        coverage happens to be total."""
        return sum(self.records[r] for r in self.audited_ids)

    @model_validator(mode="after")
    def _check_audited(self) -> "Scenario":
        unknown = sorted(set(self.audited) - set(self.records))
        if unknown:
            raise ValueError(
                f"scenario {self.id!r} audits records that do not exist: {unknown}"
            )
        return self


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


class GridSpec(BaseModel):
    """Which prompt strategies and tool sets a task's matrix should span."""

    prompts: Optional[list[str]] = None
    toolsets: Optional[list[str]] = None


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

    fault_tool: str = "fetch_record"
    """Which tool the schedule corrupts.

    Pointing it at a MATERIAL tool is what makes remediation cost
    measurable: the agent's correction is then a choice between re-calling
    something irreversible and reconciling against the free sources it
    already has.
    """

    fault_kind: str = "wrong_value"
    """Which KIND of lie this task injects.

    `wrong_value` corrupts data; `instruction` corrupts what the agent is
    told to do; `poisoned_memory` corrupts what it previously worked out.
    Different failures with different oracles, so a task declares one rather
    than mixing them -- averaging three questions into one number is exactly
    what the outcome taxonomy exists to prevent.

    Joins `fingerprint()` only when non-default, so no existing hash moves.
    """

    gate: Optional[GateCriteria] = None
    """Pre-registered gate thresholds. Absent means the gate reports
    numbers without a verdict -- it never invents a bar."""

    grid: Optional[GridSpec] = None
    """The factor levels this task needs, when the defaults will not do.

    `lending/applicant.yaml` corrupts `pull_credit_report`, which the
    default tool sets do not grant -- so the default grid produced a full
    leaderboard in which no variant could ever see the fault. Deliberately
    *not* part of `fingerprint()`: the grid is the search space, not the
    bar, and overriding it from a CLI flag must mean the same thing as
    declaring it here.
    """

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

        The payload is built from named fields rather than dumped wholesale,
        and that is load-bearing rather than tidiness. Hashing the whole
        model means *adding a field* rehashes every spec that does not use
        it, and the fingerprint on last week's run records stops resolving
        to any task that still exists. Experiment 003 was judged against
        `5c9848747223eaa4`; adding `Scenario.audited` moved that hash until
        this method was written out explicitly, silently detaching the one
        record the whole pre-registration argument rests on.

        So the rule for extending this: a new field joins the payload only
        when it is actually set. A field left at its default was not part of
        the bar, and must not be part of the hash.
        """
        payload = json.dumps(
            {
                "id": self.id,
                "statement": self.statement,
                "oracle": self.oracle.value,
                "scenarios": [_scenario_payload(s) for s in self.scenarios],
                "acceptable_degradation": self.acceptable_degradation,
                "tolerance": self.tolerance,
                # Joins the payload only when set, per the rule above.
                **({"fault_tool": self.fault_tool}
                   if self.fault_tool != "fetch_record" else {}),
                **({"fault_kind": self.fault_kind}
                   if self.fault_kind != "wrong_value" else {}),
                "gate": self.gate.model_dump(mode="json") if self.gate else None,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def from_yaml(cls, path: Union[str, Path]) -> TaskSpec:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)


def _scenario_payload(scenario: Scenario) -> dict[str, Any]:
    """One scenario's contribution to the fingerprint.

    Optional fields appear only when set, so a scenario that predates them
    keeps the hash it was recorded under.
    """
    payload: dict[str, Any] = {"id": scenario.id, "records": dict(scenario.records)}
    if scenario.audited:
        payload["audited"] = sorted(scenario.audited)
    if scenario.expected is not None:
        payload["expected"] = scenario.expected
    return payload


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

    model: Optional[str] = None
    """The resolved model string, e.g. `anthropic/claude-haiku-4-5`.

    `factors["model"]` is an alias into a mapping supplied at generation
    time. Keeping only the alias made "which model was this?" unanswerable
    from a run record; the fingerprint detects a change but cannot name
    what changed.
    """

    fingerprint: Optional[str] = None
    """Hash of what this variant actually *is*: the realized `skill.md`, the
    tools it was granted, the resolved model string, the entry agent.

    A run record naming `prompt-verifying` pins a *label*, and labels drift.
    Edit `PROMPTS["verifying"]` -- as this project did, to teach the
    verifying strategy about partial audits -- and every earlier record
    citing that name now refers to text that no longer exists, with nothing
    to show it changed. The task fingerprint does not cover prompts; it
    covers the task.

    Same defect as the schema-sensitive task hash and the unstored answer:
    a record that names a thing instead of pinning it (#15).
    """

    is_sentinel: bool = False
    """A deliberately degraded variant. If the board does not rank it last,
    the instrument is broken -- see issue #29. Excluded from the ranking
    under test; used to validate it."""
