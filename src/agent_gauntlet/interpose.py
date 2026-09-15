"""The interposer: where an injected fault actually changes a tool result.

A variant's `tools.py` calls into here rather than reading its data
directly. That places injection *below* the SDK adapter, so the same fault
schedule applies identically on all six CommonADK targets -- and it works
where an observe-only hook cannot (see `faults.py` for why).

The active schedule is held in a `ContextVar`, not a module global, so
concurrent runs in one process cannot leak faults into each other. Latency
comparisons already require controlled concurrency; silently shared fault
state would corrupt correctness too.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator, Optional

from .faults import FaultKind, FaultSchedule

_ACTIVE: contextvars.ContextVar[Optional["RunContext"]] = contextvars.ContextVar(
    "agent_gauntlet_run_context", default=None
)


class ToolTimeout(RuntimeError):
    """Raised in place of a result when a TIMEOUT fault fires."""


class RunContext:
    """Per-run state: the world the tools serve, the schedule, the log.

    The log is what scoring reads afterwards. It records every tool call in
    order, whether it was faulted, and -- crucially -- the step index at
    which contradicting evidence first became reachable, which is where the
    time-to-detect clock starts (#16).
    """

    def __init__(self, records: dict[str, int], schedule: FaultSchedule) -> None:
        self.records = dict(records)
        self.schedule = schedule
        self.calls: list[dict[str, object]] = []
        self.evidence_available_at: Optional[int] = None

    @property
    def step(self) -> int:
        return len(self.calls)

    def _log(self, tool: str, key: str, faulted: bool, result: object) -> None:
        self.calls.append(
            {
                "step": self.step,
                "tool": tool,
                "key": key,
                "faulted": faulted,
                "result": result,
            }
        )

    def note_evidence(self) -> None:
        """Mark that a cross-check has now been observed.

        Only the *first* one starts the clock; later calls do not reset it.
        """
        if self.evidence_available_at is None:
            self.evidence_available_at = self.step


@contextmanager
def run_context(
    records: dict[str, int], schedule: FaultSchedule
) -> Iterator[RunContext]:
    """Activate a run's world and fault schedule for the enclosing block."""
    ctx = RunContext(records, schedule)
    token = _ACTIVE.set(ctx)
    try:
        yield ctx
    finally:
        _ACTIVE.reset(token)


def active() -> RunContext:
    ctx = _ACTIVE.get()
    if ctx is None:
        raise RuntimeError(
            "no active gauntlet run context -- a variant's tools.py was called "
            "outside run_context(). Tools must only execute inside a run."
        )
    return ctx


# --- the tool-facing surface a variant's tools.py calls -------------------


def fetch_quantity(record_id: str) -> int:
    """Return a record's quantity, corrupted if the schedule says so."""
    ctx = active()
    if record_id not in ctx.records:
        ctx._log("fetch_record", record_id, False, None)
        raise KeyError(f"no such record: {record_id}")

    fault = ctx.schedule.for_tool("fetch_record", record_id)
    if fault is None:
        value = ctx.records[record_id]
        ctx._log("fetch_record", record_id, False, value)
        return value

    if fault.kind is FaultKind.TIMEOUT:
        ctx._log("fetch_record", record_id, True, "timeout")
        raise ToolTimeout(f"fetch_record({record_id}) timed out")

    value = int(fault.corrupt_value or 0)
    ctx._log("fetch_record", record_id, True, value)
    return value


def list_record_ids() -> list[str]:
    ctx = active()
    ids = sorted(ctx.records)
    ctx._log("list_records", "*", False, ids)
    return ids


def summary_total() -> int:
    """The independently-computed true total -- the cross-check.

    This is the planted evidence that makes a WRONG_VALUE fault detectable
    in principle: it disagrees with the sum of per-record fetches whenever
    one of them was corrupted. Without it, no agent could catch the fault
    and time-to-detect would be infinite for every variant, measuring
    nothing (#5 fair-fault, #16 evidence availability).
    """
    ctx = active()
    total = sum(ctx.records.values())
    ctx.note_evidence()
    ctx._log("get_summary", "*", False, total)
    return total
