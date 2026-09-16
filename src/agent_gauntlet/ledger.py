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
from typing import Any, Iterator, Optional, Union

from pydantic import BaseModel, Field

from .faults import FaultSchedule
from .score import Score


class RunRecord(BaseModel):
    """One (variant, scenario, repeat, condition) run."""

    run_id: str
    task_id: str
    task_fingerprint: str
    """Hash of the spec this was scored against -- pre-registration made
    checkable rather than merely asserted (#15)."""

    variant_id: str
    factors: dict[str, str] = Field(default_factory=dict)
    scenario_id: str
    repeat: int
    seed: str
    condition: str
    """'clean' or 'faulted'. The two halves of a counterfactual pair share
    a seed; robustness is the difference between them."""

    schedule: FaultSchedule
    score: Score
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    rollup: dict[str, Any] = Field(default_factory=dict)
    """`Trace.rollup()` from commonadk -- token counts, cost_usd, durations.
    Empty for offline runs, which spend nothing."""

    offline: bool = False
    """True when produced by a scripted policy rather than a real model.
    Offline runs test the harness; they are never evidence about agents."""

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


def write_summary(path: Union[str, Path], payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
