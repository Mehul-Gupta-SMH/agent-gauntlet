"""A recorded run, replayed with no server behind it.

The arena is the most persuasive thing this project has, and a link in a
post has to load instantly and cost nothing to keep alive. A hosted server
is the wrong artifact for that: free tiers cold-start, their disks are
ephemeral, the event log is in memory, and a provider key in the host
environment would let any visitor spend the owner's money.

None of that is a reason to settle for a lesser demo, because the arena
already draws from an event stream and does not care whether the stream is
arriving from a poll or being read from a file. This module writes the
file.

Two rules shape it:

**Captured, never reconstructed.** The exporter attaches a log to a real
matrix and dumps what the harness emitted -- the same bytes the page would
have received live. Rebuilding a plausible stream from ledger records would
be a second implementation of the narrative, free to drift from the one the
UI actually consumes, and a mocked demo of a tool whose whole argument is
"measure it honestly" would be the worst possible own goal.

**One composer.** `row`, `intake_event` and `board_event` live here and are
called by the server *and* by the exporter, so a replay cannot show a
column the live page does not, or censor differently from it.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

from . import board, events
from .ledger import RunRecord
from .ledger import errors as ledger_errors
from .stats import detectable_difference

FORMAT = 1
"""Bumped only when the page would misread an older file.

The events themselves are not versioned: they are whatever the harness
emitted on the day, and a replay is a historical record rather than a
contract. This number is about the envelope around them.
"""


def row(r: board.VariantResult) -> dict[str, Any]:
    """One board row, censored exactly as the CLI censors it.

    `None` travels to the page as `null` and renders as `n/a`. The page has
    no rule for turning a missing measurement into a zero, because it is
    never given the chance to.
    """
    return {
        "variant_id": r.variant_id,
        "label": r.label,
        "factors": r.factors,
        "n_runs": r.n_runs,
        "graded": r.graded,
        "quality": r.quality,
        "accuracy": r.accuracy,
        "clean_quality": r.clean_quality,
        "faulted_quality": r.faulted_quality,
        "propagation_rate": r.propagation_rate,
        "propagation_unmeasured": r.propagation_unmeasured,
        "compliance_rate": r.compliance_rate,
        "n_directives": r.n_directives,
        "obeyed_the_data": r.obeyed_the_data,
        "detection_rate": r.detection_rate,
        "repair_rate": r.repair_rate,
        "false_alarm_rate": r.false_alarm_rate,
        "median_detect_latency": r.median_detect_latency,
        "clean_steps": r.clean_steps,
        "faulted_steps": r.faulted_steps,
        "effort_ratio": r.effort_ratio,
        "material_calls": r.material_calls,
        "redundant_material_calls": r.redundant_material_calls,
        "n_errored": r.n_errored,
        "cost_per_run": r.cost_per_run,
        "gated": r.gated,
        "over_harm_budget": r.over_harm_budget,
        "harm_budget": r.harm_budget,
        "n_propagation_undecidable": r.n_propagation_undecidable,
        "intervals": {k: v.model_dump() for k, v in r.intervals.items()},
        "is_sentinel": r.is_sentinel,
    }


def intake_event(task, variants: Sequence, *, seeds: int, repeats: int) -> dict[str, Any]:
    """What the page needs before the first run starts."""
    scenario = task.scenarios[0]
    return {
        "task": task.id,
        "statement": task.statement,
        "fingerprint": task.fingerprint(),
        "toolsets": task.grid.toolsets if task.grid else [],
        "prompts": task.grid.prompts if task.grid else [],
        "records": scenario.records,
        "audited": scenario.audited_ids,
        "expected": scenario.expected_total,
        "seeds": seeds,
        "repeats": repeats,
        # The whole plan up front, so the progress counter never shows a
        # total that grows as seeds arrive.
        "total_runs": len(variants) * len(task.scenarios) * repeats * seeds * 2,
    }


def board_event(
    records: Sequence[RunRecord],
    *,
    seeds: int,
    harm_budget: Optional[int] = None,
    graded: Optional[bool] = None,
) -> dict[str, Any]:
    """The final frame: the board, the resolution, and the held-out winner.

    `graded=None` means the question does not arise -- a fixture carries a
    label by construction. It is `False` only for an unlabelled project,
    where there is a gate but nothing to rank.
    """
    results = (board.summarize(records) if harm_budget is None
               else board.summarize(records, harm_budget=harm_budget))
    held = board.held_out_winner(records)
    n = min((r.n_runs for r in results if not r.is_sentinel and r.n_runs), default=0)
    return {
        "rows": [row(r) for r in results],
        "errored": len(ledger_errors(records)),
        "total": len(records),
        "graded": True if graded is None else graded,
        # What this many runs could have seen. Sent with the board so the
        # page cannot render a ranking without it.
        "resolution": {"n": n, "mde": detectable_difference(n)},
        # The selection score is never the reported score (#20). If the
        # split could not be formed, the page is told so rather than being
        # handed the optimistic number -- and *why* matters: a tie is a
        # real answer (#11), while one seed simply cannot be split.
        # Telling the page "run more seeds" for a tie would be advice that
        # does not fix anything.
        "winner_reason": (
            None if held is not None
            else "This project has no expected answer, so propagation is "
                 "gated but nothing can be ranked by correctness. Add the "
                 "right answer for a scenario to get a leaderboard."
            if graded is False
            else "one seed -- nothing to hold the winner out on"
            if seeds < 2
            else "The selection half is tied at the top, and a tie is a real "
                 "answer rather than a winner."
        ),
        "winner": None if held is None else {
            "variant_id": held.variant_id,
            "selection_score": held.selection_score,
            "holdout_score": held.holdout_score,
            "held_up": held.held_up,
            "holdout_rank": held.holdout_rank,
            "n_candidates": held.n_candidates,
        },
    }


@contextmanager
def capture() -> Iterator[events.EventLog]:
    """Attach a log for the duration of a run, then detach.

    The detach is in a `finally` on purpose: a log left attached would keep
    collecting events from every later run in the same process, and a
    replay is one run.
    """
    log = events.EventLog()
    events.attach(log)
    try:
        yield log
    finally:
        events.attach(None)


def document(log: events.EventLog, *, title: str, note: str,
             source: str) -> dict[str, Any]:
    """The envelope: a caption, provenance, and the events verbatim.

    `note` and `source` are not decoration. A replay is evidence shown to
    someone who cannot re-run it, so the file says what produced it.
    """
    return {
        "format": FORMAT,
        "title": title,
        "note": note,
        "source": source,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "events": [e.as_json() for e in log.since(0)],
    }


def write(path: Path, doc: dict[str, Any]) -> Path:
    """One event per line inside ordinary JSON.

    A recording of a real matrix is thousands of events and this file gets
    committed, so it is written compactly -- but one event per line, so a
    diff shows which events changed rather than one unreadable line. The
    result is still plain JSON: `json.load` reads it, and the page does not
    know or care.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    head = {k: v for k, v in doc.items() if k != "events"}
    lines = [json.dumps(head)[:-1] + ',', ' "events": [']
    events_json = [json.dumps(e, separators=(",", ":")) for e in doc["events"]]
    lines.append(",\n".join("  " + e for e in events_json))
    lines.append(" ]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
