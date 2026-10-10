"""Pick the probe cell with the least live evidence (#4, #5, #45, #47).

The probe fires on every push to `main` and, since experiment 013, records
what it saw. For months it has probed exactly one cell -- `audited.yaml` on
`langgraph` with the cheap model -- because those are the defaults, so the
record is six rows deep on one cell and empty everywhere else.

Meanwhile four issues are blocked on live evidence that a probe would
produce:

* **#45 / #4** want `discontinued.yaml` against a real model: it is the only
  bundled fixture whose clean ranking can discriminate, and nobody knows
  whether the split survives a model that is not a scripted policy.
* **#47** wants the same work priced by both authorities -- `claude` takes
  `cost_usd` from the SDK's billed figure, every other target takes it from
  the flat table.
* **#5** wants fault kinds other than `wrong_value`, which means the
  poisoning fixtures.

None of that needs a new budget. The probe already runs, already spends
~$0.02, and already throws the result away. Rotating *which* cell it probes
costs exactly the same per push and fills the record instead.

So: least-covered first. Deterministic given the record, self-balancing,
and it converges on even coverage without anybody maintaining a schedule.
A dispatch with explicit inputs still wins -- this only chooses when nobody
did.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator, NamedTuple, Optional


class Cell(NamedTuple):
    fixture: str
    target: str
    model: str


# Each entry earns its place by being blocked on live evidence, and the
# comment says which issue. A cell nobody is waiting on does not belong
# here -- the probe's budget is one run per push and this list spends it.
WANTED: tuple[Cell, ...] = (
    # The default, kept first so the path this project has always probed
    # keeps accumulating. It is also #47's table-priced half.
    Cell("fixtures/inventory/audited.yaml", "langgraph", "cheap"),
    # #47: the same work, SDK-priced. The ratio against the row above is
    # the measured gap between a flat table and a billed figure.
    Cell("fixtures/inventory/audited.yaml", "claude", "cheap"),
    # #45 / #4: the only fixture whose clean ranking can discriminate.
    Cell("fixtures/inventory/discontinued.yaml", "claude", "cheap"),
    # #45's real risk, and experiment 003's lesson restated: a capable
    # model handed the `filtering` prompt may ALSO catch the lie, which
    # collapses the split the fixture exists to create. `cheap` passing
    # proves less than `smart` failing to collapse it.
    Cell("fixtures/inventory/discontinued.yaml", "claude", "smart"),
    # #5 open question 1: are the fault kinds equally informative? Only
    # `wrong_value` has live data, because only it is declared by the
    # fixtures probed so far.
    Cell("fixtures/poisoning/instruction.yaml", "claude", "cheap"),
    Cell("fixtures/poisoning/memory.yaml", "claude", "cheap"),
)


def _rows(path: Path) -> Iterator[dict]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            # A malformed row is not a reason to stop probing. It is also
            # not evidence, so it counts toward nothing.
            continue


def coverage(path: Path) -> dict[Cell, int]:
    """How many recorded rows each wanted cell already has."""
    counts = {cell: 0 for cell in WANTED}
    for row in _rows(path):
        cell = Cell(row.get("fixture") or "", row.get("target") or "",
                    row.get("model_level") or "")
        if cell in counts:
            counts[cell] += 1
    return counts


def choose(path: Path) -> Cell:
    """The wanted cell with the fewest rows; ties go to declaration order.

    Declaration order matters and is not arbitrary: the first entry is the
    cell this project has always probed, so a tie at zero does not abandon
    the path the matrix guard depends on.
    """
    counts = coverage(path)
    return min(WANTED, key=lambda cell: (counts[cell], WANTED.index(cell)))


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv else Path("experiments/live/probe.jsonl")
    counts = coverage(path)
    cell = choose(path)

    # To stderr so the step's `$GITHUB_OUTPUT` capture stays clean, and
    # because a human reading the log wants to see why this cell won.
    print("live evidence per cell:", file=sys.stderr)
    for wanted in WANTED:
        mark = " <-- probing this" if wanted == cell else ""
        print(f"  {counts[wanted]:3d}  {wanted.fixture} "
              f"{wanted.target}/{wanted.model}{mark}", file=sys.stderr)

    print(f"fixture={cell.fixture}")
    print(f"target={cell.target}")
    print(f"model={cell.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
