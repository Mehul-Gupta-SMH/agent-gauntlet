"""A live event stream out of the harness.

The UI layer exists to show *what actually happened*, which means it has to
read the same events the ledger is built from rather than a parallel
narrative invented for display. Every frame the arena draws is one of these
records: a real variant, a real injected value, a real score.

The emitter is optional and off by default. `run_matrix` and `RunContext`
call `emit()` unconditionally, and with no emitter attached that is an
attribute lookup and a return -- so the CLI, the tests and CI behave exactly
as they did before this module existed.

Thread-safe because the server runs the matrix on a worker thread while the
page polls from request threads.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Event:
    """One thing that happened, in the order it happened."""

    seq: int
    kind: str
    at: float
    data: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"seq": self.seq, "kind": self.kind, "at": self.at, **self.data}


class EventLog:
    """An in-memory, append-only log the page reads by cursor.

    Polled rather than pushed. A run is seconds to minutes and the client is
    on localhost, so a cursor poll is both sufficient and far less fragile
    than holding a streaming response open through `http.server` -- which
    would also have to survive the worker thread raising.
    """

    def __init__(self, limit: int = 100_000) -> None:
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self._seq = 0
        self._limit = limit
        """Runs are bounded (variants x scenarios x repeats x 2), but a UI
        left open across many runs is not. Oldest events are dropped rather
        than growing without bound; `since` still works because the cursor
        is the sequence number, not an index."""

    def emit(self, kind: str, **data: Any) -> Event:
        with self._lock:
            self._seq += 1
            event = Event(seq=self._seq, kind=kind, at=time.time(), data=data)
            self._events.append(event)
            if len(self._events) > self._limit:
                del self._events[: len(self._events) - self._limit]
            return event

    def since(self, cursor: int) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.seq > cursor]

    @property
    def cursor(self) -> int:
        with self._lock:
            return self._seq

    def clear(self) -> None:
        """Start a new run's stream. The cursor keeps counting up, so a
        client polling across the boundary never re-reads an old event as
        if it were new."""
        with self._lock:
            self._events.clear()


_ACTIVE: Optional[EventLog] = None
_ACTIVE_LOCK = threading.Lock()


def attach(log: Optional[EventLog]) -> None:
    """Point the harness at a log, or detach with None."""
    global _ACTIVE
    with _ACTIVE_LOCK:
        _ACTIVE = log


def emit(kind: str, **data: Any) -> None:
    """Record an event if anything is listening. Otherwise do nothing."""
    log = _ACTIVE
    if log is not None:
        log.emit(kind, **data)


def active() -> Optional[EventLog]:
    return _ACTIVE
