"""Read the live probe record and print #41's rephrase-invariance verdicts.

A tool rather than a note in a write-up, because the comparison was being
done by eye. "No threshold, no judge" is worth very little if the join
between the two rows of a pair is manual -- a verdict that depends on
somebody lining up a baseline and a restated run by hand is not a
mechanical check, however exact the `==` at the end of it is.

Usage:

    python tools/paraphrase_verdict.py experiments/live/probe.jsonl

Prints one line per decidable pair plus the overall verdict, and exits
non-zero only if a pair is VIOLATED -- which is a finding, so it is worth
an exit code. A clean sweep is not a pass and does not pretend to be: a
relation is a binary property, so no number of passes proves it and the
output says so in those words.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_gauntlet.metamorphic import (
    pair_budget, paraphrase_outcomes, paraphrase_verdict,
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.exists():
        print(f"no such record: {path}", file=sys.stderr)
        return 2

    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            # A malformed row is not a pair and not a reason to stop.
            continue

    outcomes = paraphrase_outcomes(rows)
    if not outcomes:
        print("no pairs in the record: a baseline run and a paraphrase run "
              "of the SAME config are what make one")
        return 0

    for o in outcomes:
        if not o.decidable:
            # Undecidable, never "held": comparing two missing totals with
            # `==` would call a pair of failures an agreement.
            print(f"  n/a      {o.fixture} p{o.paraphrase} [{o.variant}] -- "
                  f"baseline={o.baseline_total} restated={o.restated_total}")
            continue
        mark = "VIOLATED" if o.violated else "no c/ex"
        print(f"  {mark:<8} {o.fixture} p{o.paraphrase} [{o.variant}] -- "
              f"{o.baseline_total} vs {o.restated_total}")

    decidable = [o for o in outcomes if o.decidable]
    print()
    print(f"  {paraphrase_verdict(outcomes)}")
    if decidable:
        budget = pair_budget(len(decidable), 1)
        print(f"  ({budget.extra_runs} pair(s) is what bought that bound; "
              f"pair_budget prices a tighter one)")
    return 1 if any(o.violated for o in decidable) else 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
