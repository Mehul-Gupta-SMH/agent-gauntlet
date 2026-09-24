"""Immutable run records.

A run is only a result if it can be replayed. Each record pins everything
that decided the number: the task fingerprint (so the bar is provable), the
fault seed, the variant's factor settings, and -- once real execution is
wired in -- the model ids and price table the cost was computed against.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Union

from pydantic import BaseModel, Field

from .faults import FaultSchedule
from .score import Answer, Score


class RunRecord(BaseModel):
    """One (variant, scenario, repeat, condition) run."""

    run_id: str
    task_id: str
    task_fingerprint: str
    """Hash of the spec this was scored against -- pre-registration made
    checkable rather than merely asserted (#15)."""

    variant_id: str
    model: Optional[str] = None
    """The resolved model string this run used, e.g.
    `anthropic/claude-haiku-4-5`.

    `factors` carries the alias ("cheap"), which is a label into a mapping
    that lives outside the record. The fingerprint would *detect* that the
    mapping changed but cannot say what it changed from, so "which model
    produced this run?" was unanswerable from the ledger alone (#15).
    """

    variant_fingerprint: Optional[str] = None
    """Hash of the prompt text, tool grant, and model this run actually used.

    `factors` names the levels; this pins them. Without it a ledger cannot
    tell "these two runs used the same verifying prompt" from "these two
    runs used prompts that happened to share a name" (#15).
    """

    factors: dict[str, str] = Field(default_factory=dict)
    scenario_id: str
    repeat: int
    seed: str
    condition: str
    """'clean' or 'faulted'. The two halves of a counterfactual pair share
    a seed; robustness is the difference between them."""

    schedule: FaultSchedule
    score: Score

    error: Optional[str] = None
    """Why this run produced no result, when it produced none.

    A run that raised used to leave no trace at all: the exception
    propagated out of `run_matrix`, the matrix stopped, and the ledger
    simply ended. One transient provider error part-way through a paid
    matrix discarded every run that would have followed it, and nothing on
    disk said why.

    An errored run is CENSORED, not failed: it is excluded from every rate
    rather than counted as an agent that got the answer wrong, because the
    agent never got to answer. Same treatment as unreachable evidence
    (#16).
    """

    attempts: int = 1
    """How many times this run was tried. >1 means a provider-shaped error
    was retried; useful for telling a flaky provider from a flaky agent."""

    answer: Optional[Answer] = None
    """What the agent actually reported.

    Absent from the first build, and that was a replayability hole this
    module's own premise forbids: the ledger stored the *verdict* but not
    the evidence, so no change to scoring could ever be applied to runs
    already recorded. Every revision of the outcome taxonomy -- and there
    have been three -- would have needed a fresh paid matrix to re-measure
    something the old runs already contained.

    With this plus `tool_calls` and `schedule`, a stored run can be graded
    again by today's rules. See `rescore`.

    None on records written before this field existed; `rescore` refuses
    them rather than guessing.
    """

    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    rollup: dict[str, Any] = Field(default_factory=dict)
    """`Trace.rollup()` from commonadk -- token counts, cost_usd, durations.
    Empty for offline runs, which spend nothing."""

    offline: bool = False
    """True when produced by a scripted policy rather than a real model.
    Offline runs test the harness; they are never evidence about agents."""

    prices: dict[str, Any] = Field(default_factory=dict)
    """The rate table `rollup["cost_usd"]` was computed against (#14).

    A `pricing.PriceSnapshot`, flattened. Empty for offline runs, which
    spend nothing and are priced by nobody -- so an absent fingerprint is
    not drift.

    This is what the module docstring above promised and did not deliver:
    the price table the cost was computed against, pinned into the record.
    Without it, two matrices run a month apart average into one cost column
    with no way to tell the rates moved underneath them."""

    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def pair_key(self) -> tuple[str, str, int]:
        """Identifies the counterfactual twin of this run."""
        return (self.variant_id, self.scenario_id, self.repeat)

    @property
    def base_seed(self) -> str:
        """The independent replication this run belongs to.

        `seed` is per-run (`base:variant:scenario:repeat`) so two variants
        never draw the same corruption. The part before the first colon is
        the replication, and it is the unit everything about independence
        is defined over: stability compares base seeds, and the held-out
        split partitions them (#1, #20).
        """
        return self.seed.split(":", 1)[0]


class Ledger:
    """Append-only JSONL store. One line per run, never rewritten."""

    def __init__(self, path: Union[str, Path]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: RunRecord) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(record.model_dump_json() + "\n")

    def __iter__(self) -> Iterator[RunRecord]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield RunRecord.model_validate_json(line)

    def records(self) -> list[RunRecord]:
        return list(self)

    def errors(self) -> list["RunRecord"]:
        """Runs that never produced an answer. Empty is the happy case."""
        return errors(self)

    def variant_drift(self) -> dict[str, set[str]]:
        """Variant ids that appear under more than one fingerprint."""
        return variant_drift(self)

    def fingerprints(self) -> set[str]:
        """Every task fingerprint present.

        More than one means the ledger mixes runs scored against different
        bars, and aggregating across them would be meaningless -- callers
        should refuse rather than average.
        """
        return {r.task_fingerprint for r in self}

    def scores_by_seed(
        self, *, condition: Optional[str] = None, metric: str = "correct"
    ) -> dict[str, dict[str, float]]:
        """Shape the ledger for `analyze.stability`: seed -> variant -> score.

        Aggregates repeats by mean, which is the point: one run is not a
        measurement, so the per-seed score a ranking is built from is always
        an average over k.
        """
        buckets: dict[str, dict[str, list[float]]] = {}
        for r in self:
            if condition is not None and r.condition != condition:
                continue
            value = float(getattr(r.score, metric))
            buckets.setdefault(r.seed, {}).setdefault(r.variant_id, []).append(value)
        return {
            seed: {v: sum(vals) / len(vals) for v, vals in variants.items()}
            for seed, variants in buckets.items()
        }


class ReplayContext:
    """A `RunContext` stand-in rebuilt from a stored record.

    Scoring reads three things off the live context: the world, whether any
    call came back faulted, and when a cross-check first became reachable.
    All three survive in the ledger, so grading does not need the agent
    back -- which is the whole point of storing the answer.
    """

    def __init__(self, record: "RunRecord", records: dict[str, int]) -> None:
        self.records = dict(records)
        self.schedule = record.schedule
        self.calls = list(record.tool_calls)
        self.allowed_tools = None
        evidence = record.schedule.evidence_tool
        self.evidence_available_at = next(
            (
                int(c.get("step", i))
                for i, c in enumerate(self.calls)
                if evidence and c.get("tool") == evidence
            ),
            None,
        )

    @property
    def step(self) -> int:
        return len(self.calls)


def rescore(record: "RunRecord", task: Any) -> Score:
    """Grade a stored run again, by today's rules.

    The reason `answer` exists. A scoring change can be applied to every
    run already on disk instead of being a one-way door that needs a fresh
    matrix to evaluate.

    Raises when the record predates `answer`: a re-score that quietly
    invented an answer would produce numbers indistinguishable from
    measured ones, which is the failure mode this project keeps finding in
    itself.
    """
    from .score import score_run

    if record.answer is None:
        raise ValueError(
            f"run {record.run_id} predates the stored answer and cannot be "
            "re-scored -- the reported figure was never written down"
        )
    scenario = task.scenario(record.scenario_id)
    ctx = ReplayContext(record, scenario.records)
    return score_run(
        task=task, scenario=scenario, schedule=record.schedule,
        ctx=ctx, answer=record.answer,
    )


def duplicate_cells(records: Iterable[RunRecord]) -> dict[tuple, int]:
    """Cells this ledger holds more than one attempt at.

    A cell is (variant, scenario, repeat, seed, condition) -- fully
    determined, so within one matrix every cell appears exactly once. Two
    attempts at the same cell mean the matrix was run again into the same
    ledger.

    That matters more here than in ordinary tooling, because of the one
    place the software-testing analogy inverts (#27). CI treats a flaky
    test as a defect to retry until green; here **flakiness is the
    measurement** -- an agent that succeeds 7 times in 10 genuinely is 70%
    reliable, and re-running until the gate passes does not fix the config,
    it destroys the number. Averaging two attempts at one cell is that
    mistake made quietly.
    """
    seen: dict[tuple, int] = {}
    for r in records:
        key = (r.variant_id, r.scenario_id, r.repeat, r.seed, r.condition)
        seen[key] = seen.get(key, 0) + 1
    return {k: n for k, n in seen.items() if n > 1}


def write_summary(path: Union[str, Path], payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def errors(records: Iterable["RunRecord"]) -> list["RunRecord"]:
    """Runs that never produced an answer. Empty is the happy case."""
    return [r for r in records if r.error]


def variant_drift(records: Iterable["RunRecord"]) -> dict[str, set[str]]:
    """Variant ids that appear under more than one fingerprint.

    A non-empty result means these records mix runs of configurations that
    share a name and differ in substance -- aggregating across them would
    average two different agents together and report it as one. Records
    predating the field are ignored rather than counted as a distinct
    version.

    Takes any iterable rather than only a `Ledger`, because the file is
    append-only across invocations and a caller usually wants this question
    answered about *its own* runs.
    """
    seen: dict[str, set[str]] = {}
    for r in records:
        if r.variant_fingerprint:
            seen.setdefault(r.variant_id, set()).add(r.variant_fingerprint)
    return {vid: fps for vid, fps in seen.items() if len(fps) > 1}
