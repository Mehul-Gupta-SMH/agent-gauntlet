#!/usr/bin/env python3
"""The census behind this experiment's README. Run it, do not quote it.

    python3 -I experiments/018-the-clean-tie-broke-on-the-contract/census.py \
        experiments/live/probe.jsonl > experiments/018-.../console.txt

Every table in the README is this script's output. The point of keeping it
is that the probe ledger keeps growing: a reader six months from now can
re-run this and see whether the rates below held or were an artefact of 58
rows.
"""

from __future__ import annotations

import collections
import json
import sys


def state(row: dict) -> str:
    """What a row is, as opposed to what its `outcome` column says.

    `outcome: null` covers four different things and the board cannot tell
    them apart, which is why this function exists:

      exit 0  a verdict was reached
      exit 1  a reply carried no TOTAL:/ANOMALY: contract, so nothing could
              be scored -- the agent's failure, not the instrument's
      exit 2  the agent called tools but never the corrupted one: the fault
              could not have propagated, so propagation is NOT APPLICABLE
      exit 4  the model was unreachable: the measurement never happened
    """
    code = row.get("probe_exit")
    if code == 0:
        return "complete"
    if code == 2:
        return "never_consulted" if row.get("never_consulted") else "exit2_other"
    if code == 4:
        return "model_unreachable"
    if code == 1:
        # Order matters: the clean half runs first, so a null clean total
        # means the faulted half never ran at all.
        if row.get("clean_total") in (None, 0):
            return "clean_half_unparsed"
        if row.get("faulted_total") is None:
            return "faulted_half_unparsed"
        return "exit1_other"
    return f"exit_{code}"


def prompt_of(row: dict) -> str:
    variant = row.get("variant")
    if not variant:
        return "(no variant recorded)"
    parts = variant.split("__")
    return parts[1] if len(parts) > 1 else variant


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "experiments/live/probe.jsonl"
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    print(f"{len(rows)} probe rows from {path}\n")

    print("=== what the rows are")
    for name, n in collections.Counter(state(r) for r in rows).most_common():
        label = name
        if name == "exit_None":
            # Rows written before the workflow recorded the probe's exit
            # status. Not a state the probe can be in -- a gap in the
            # ledger's own schema, and it has to read as one rather than
            # being folded into any of the states above.
            label = "exit_None (pre-schema)"
        print(f"  {label:24s} {n:3d}  ({n / len(rows):.0%})")

    # Counts BOTH halves: a faulted reply can fail to parse after a clean
    # one succeeded, and calling the column "clean" when it holds either
    # would be a table reporting one thing and labelled another.
    print("\n=== replies with no contract (either half), by fixture")
    by_fixture: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for row in rows:
        name = (row.get("fixture") or "?").split("/")[-1]
        by_fixture[name][0] += 1
        if state(row).endswith("unparsed"):
            by_fixture[name][1] += 1
    for name, (n, bad) in sorted(by_fixture.items()):
        print(f"  {name:20s} {bad}/{n}")

    app = [r for r in rows if "applicant" in (r.get("fixture") or "")]
    print(f"\n=== applicant.yaml ({len(app)} rows), by prompt")
    table: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0, 0, 0, 0])
    for row in app:
        cell = table[prompt_of(row)]
        kind = state(row)
        cell[0] += 1
        if kind.endswith("unparsed"):
            cell[1] += 1
        elif kind == "never_consulted":
            cell[2] += 1
        elif kind == "complete":
            cell[3] += 1
            if row.get("propagated"):
                cell[4] += 1
    head = f"  {'prompt':26s}{'runs':>5}{'unparsed':>9}{'n/a':>5}{'scored':>7}{'propagated':>11}"
    print(head)
    for name, (n, bad, na, done, prop) in sorted(table.items()):
        print(f"  {name:26s}{n:5d}{bad:9d}{na:5d}{done:7d}{prop:11d}")

    print("\n=== the two rankings applicant.yaml now supports")
    print("  parse rate is the clean column the board would score; a reply")
    print("  with no contract scores accuracy 0.0 by design (silence is not")
    print("  partial credit), so an unparsed run is a clean-side loss.")
    print(f"  {'prompt':26s}{'clean parse':>12}{'propagated':>12}")
    for name, (n, bad, na, done, prop) in sorted(
        table.items(), key=lambda kv: -(kv[1][0] - kv[1][1]) / max(kv[1][0], 1)
    ):
        if name.startswith("("):
            continue
        parse = (n - bad) / n
        prop_rate = "n/a" if done == 0 else f"{prop}/{done}"
        print(f"  {name:26s}{parse:11.0%}{prop_rate:>12}")

    print("\n=== every unparsed CLEAN reply, in full")
    print("  one LLM call and no tool calls in every single one: the model")
    print("  wrote prose instead of acting. Not a truncation and not a")
    print("  flattening bug -- there was nothing to flatten.")
    for row in rows:
        if state(row) != "clean_half_unparsed":
            continue
        print(
            f"  {row['run_id']}  {prompt_of(row):26s}"
            f" tools={row.get('tool_calls')}"
            f" llm={row.get('llm_calls')}"
            f" completion={row.get('completion_tokens')}"
            f" ${row.get('cost_usd')}"
        )

    spend = sum(r.get("cost_usd") or 0 for r in rows if state(r).endswith("unparsed"))
    total = sum(r.get("cost_usd") or 0 for r in rows)
    print(f"\nspend that bought no verdict: ${spend:.4f} of ${total:.4f} ({spend / total:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
