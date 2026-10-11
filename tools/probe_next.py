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
    variant: Optional[str] = None
    """Substring selecting WHICH config, or None for the first eligible.

    Added because every cell above it was answering "does this fixture /
    target / model behave" and #45 asks a different question: do DIFFERENT
    CONFIGS answer the clean run differently? The probe took the first
    eligible variant, so a fixture probed a hundred times measured one
    config a hundred times and could not speak to that.

    A `None` selector and an explicit one must not share a
    fixture/target/model, or a row would count toward both cells and the
    coverage would double-count itself. Enforced by a test.
    """

    paraphrase: Optional[int] = None
    """Which declared restatement to substitute, or None for the statement.

    The rephrase-invariance pair is two cells that differ ONLY here (#41):
    one baseline, one paraphrase, same fixture, target, model and config.
    The relation holds between them or it does not, by `==` on an integer.
    """


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
    # #45 / #4, the question the other cells cannot reach. Both live
    # matrices found the CLEAN leaderboard an eight-way tie, so the
    # statistic #4 asks for has only one ranking to correlate. #45 names
    # `lending/applicant.yaml` as the likeliest fixture to break the tie
    # and says outright that nobody has run it live, so it is a guess.
    #
    # It still is -- but a $5 matrix is not the only way to narrow it. Four
    # prompt levels at one model level, one clean answer each, accumulating
    # one per push at the budget the probe already spends. If they differ,
    # the clean side can discriminate and #4 becomes measurable. If they
    # agree, that is a third fixture confirming the tie, which is also what
    # #45 wants to know.
    #
    # The toolset is PINNED to records+bureau across all four so the only
    # thing varying is the prompt. Varying both would produce a spread
    # nobody could attribute -- which is the mistake I made reading the
    # offline clean spread, where the "discrimination" turned out to be one
    # shared slip rate (experiment 015).
    #
    # n=1 per cell after one pass, which decides nothing on its own: this
    # project's own rule is that one measurement is not a property (#37).
    # Two passes is ~$0.40 of a budget already committed.
    Cell("fixtures/lending/applicant.yaml", "claude", "cheap",
         "prompt-naive__toolset-records+bureau"),
    Cell("fixtures/lending/applicant.yaml", "claude", "cheap",
         "prompt-verifying__toolset-records+bureau"),
    Cell("fixtures/lending/applicant.yaml", "claude", "cheap",
         "prompt-bureau_repull__toolset-records+bureau"),
    Cell("fixtures/lending/applicant.yaml", "claude", "cheap",
         "prompt-bureau_deliberate__toolset-records+bureau"),
    # #41: rephrase-invariance, as a FALSIFICATION SEARCH rather than a
    # rate. Three cells make two pairs -- baseline against each declared
    # paraphrase -- on one config, same seed, same world. The relation
    # holds between a pair or it does not, by `==` on an integer: no
    # threshold, no judge.
    #
    # Two clean pairs bound the violation rate at about 66%, so a pass
    # here reads "no counterexample yet" and never "the relation holds"
    # (`metamorphic.pair_budget`). That is the honest return on ~$0.15,
    # and buying a tight bound would cost 90 runs.
    #
    # `rephrased.yaml` exists as its own fixture because paraphrases join
    # `fingerprint()`: adding them to audited.yaml would detach every live
    # record this project holds for it.
    Cell("fixtures/inventory/rephrased.yaml", "claude", "cheap"),
    Cell("fixtures/inventory/rephrased.yaml", "claude", "cheap", None, 1),
    Cell("fixtures/inventory/rephrased.yaml", "claude", "cheap", None, 2),
)


def _cell_for(row: dict) -> Optional[Cell]:
    """Which wanted cell this recorded row is evidence for, if any.

    A cell with a variant selector claims only the rows whose variant id
    contains it. A cell without one claims any row on its
    fixture/target/model -- which is why the two kinds must not share a
    fixture/target/model, or a row would be counted twice.
    """
    fixture = row.get("fixture") or ""
    target = row.get("target") or ""
    model = row.get("model_level") or ""
    variant = row.get("variant") or ""
    for cell in WANTED:
        if (cell.fixture, cell.target, cell.model) != (fixture, target, model):
            continue
        if cell.paraphrase != row.get("paraphrase"):
            # A baseline cell must not claim a paraphrase row or the pair
            # collapses into one count and the comparison never happens.
            continue
        if cell.variant is None or cell.variant in variant:
            return cell
    return None


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


MAX_EMPTY = 2
"""Empty rows a cell may accumulate before it leaves the rotation.

A row with no `outcome` is not evidence -- it records that the probe ran
and produced nothing parseable. Usually that means the cell is broken
rather than the model is interesting, and it happened immediately: the
first version of the rotation ran the chooser AFTER the step that installs
the target's SDK, so a rotated `claude` cell installed
`commonadk[langgraph]` and the probe failed on a runner that was never
installed.

Two is enough to tell a transient outage from a broken cell, and parking it
after that is the difference between a finding and a standing charge.
"""


class Count(NamedTuple):
    evidence: int
    """Rows carrying an `outcome` -- the ones that answer anything."""
    empty: int
    """Rows where the probe ran and produced nothing parseable."""


def coverage(path: Path) -> dict[Cell, Count]:
    """Per wanted cell: rows that are evidence, and rows that are not.

    Counted apart because they mean opposite things. Evidence is why the
    cell is in the rotation; an empty row is a bug report about the cell or
    about the workflow, and counting the two together would let a cell that
    can never succeed look well covered.
    """
    evidence = {cell: 0 for cell in WANTED}
    empty = {cell: 0 for cell in WANTED}
    for row in _rows(path):
        cell = _cell_for(row)
        if cell is None:
            continue
        if row.get("outcome"):
            evidence[cell] += 1
        else:
            empty[cell] += 1
    return {cell: Count(evidence[cell], empty[cell]) for cell in WANTED}


def eligible(counts: dict[Cell, Count]) -> list[Cell]:
    """Cells still worth spending on.

    A cell with no evidence after `MAX_EMPTY` attempts is parked. It stays
    in `WANTED` and stays dispatchable by hand -- parking is not deletion,
    and the chooser says out loud that it did it.
    """
    return [cell for cell in WANTED
            if counts[cell].evidence or counts[cell].empty < MAX_EMPTY]


def choose(path: Path) -> Cell:
    """The eligible cell with the least evidence; ties go to declaration order.

    Declaration order matters and is not arbitrary: the first entry is the
    cell this project has always probed, so a tie at zero does not abandon
    the path the matrix guard depends on.

    Chosen on EVIDENCE rather than on rows, because a cell that keeps
    failing would otherwise look increasingly well covered while answering
    nothing.
    """
    counts = coverage(path)
    live = eligible(counts) or list(WANTED)
    return min(live, key=lambda cell: (counts[cell].evidence,
                                       WANTED.index(cell)))


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    path = Path(argv[0]) if argv else Path("experiments/live/probe.jsonl")
    counts = coverage(path)
    cell = choose(path)
    live = set(eligible(counts))

    # To stderr so the step's `$GITHUB_OUTPUT` capture stays clean, and
    # because a human reading the log wants to see why this cell won -- and
    # which cells are producing nothing, which is the failure this project
    # keeps catching late.
    print("live evidence per cell (evidence / empty):", file=sys.stderr)
    for wanted in WANTED:
        count = counts[wanted]
        if wanted == cell:
            mark = "  <-- probing this"
        elif wanted not in live:
            mark = f"  <-- PARKED: {count.empty} attempts, no evidence"
        else:
            mark = ""
        print(f"  {count.evidence:3d} / {count.empty:<3d} {wanted.fixture} "
              f"{wanted.target}/{wanted.model}{mark}", file=sys.stderr)

    print(f"fixture={cell.fixture}")
    print(f"target={cell.target}")
    print(f"model={cell.model}")
    # Empty when the cell names no variant, which the workflow turns into
    # no `--variant` flag at all. An empty string must not become
    # `--variant ''`: that matches every variant and would probe the first
    # one while the row claimed a selector.
    print(f"variant={cell.variant or ''}")
    # Empty for a baseline cell, which the workflow turns into no flag.
    print(f"paraphrase={cell.paraphrase or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
