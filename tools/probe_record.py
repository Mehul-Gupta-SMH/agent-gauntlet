"""Turn one probe's console output into one line of JSON (#46, exp 013).

The probe already prints everything worth keeping. What it did not do was
keep it: twelve probes ran on 2026-10-09, all green, all paid for, and
nobody could aggregate them -- the artifacts expire and the job logs go
with them. Four were eventually read out of the logs by hand and they
disagreed with each other, which is the finding in
`experiments/013-the-probe-was-an-experiment/`.

So this parses the console rather than re-running anything, and the
workflow appends the result to `experiments/live/probe.jsonl`.

Parsing a console is a poor interface and the better one would be a
`--json` flag on `gauntlet probe`. This exists because the console format
is what twelve already-paid-for runs emitted, and a recorder that only
works on future runs after a refactor records nothing today. A missing
field comes back as null rather than guessed -- `n/a` never `0`, the same
rule the board follows.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Optional

# One pattern per field, each anchored on text the probe actually prints.
# Anchored loosely on purpose: a label that gains a word should keep
# matching, and a field that genuinely disappears should go null rather
# than silently matching something else.
PATTERNS: dict[str, re.Pattern] = {
    "variant": re.compile(r"^probing (\S+) on target="),
    "scenario": re.compile(r"^scenario (\w+):"),
    "expected": re.compile(r"^expected total\s*:\s*(-?\d+)"),
    "clean_total": re.compile(r"^parsed total\s*:\s*(-?\d+)"),
    "confidence": re.compile(r"^CONFIDENCE:\s*([0-9.]+)"),
    "injected": re.compile(r"^injecting (\w+) on (\S+): (-?\d+) -> (-?\d+)"),
    # Anchored on "credulous", which ONLY the faulted half prints. Without
    # it this also matched the sentinel's "reported total : N (truth N)"
    # three sections later, and the last match won -- so every row recorded
    # the sentinel's figure as the faulted answer. Caught by running it
    # against two real probe outputs rather than by reading it.
    "faulted_total": re.compile(
        r"^reported total\s*:\s*(-?\d+)\s+\(truth \d+, credulous"),
    "outcome": re.compile(r"^outcome\s*:\s*(\w+)"),
    "flags": re.compile(r"^propagated=(\w+)\s+detected=(\w+)\s+surfaced=(\w+)"
                        r"\s+repaired=(\w+)\s+determinable=(\w+)"),
    "cost": re.compile(r"'cost_usd': ([0-9.]+)"),
    "sentinel_total": re.compile(r"^reported total\s*:\s*(-?\d+)\s+\(truth \d+\)$"),
}

_BOOL = {"True": True, "False": False}


def parse(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sentinel_seen: list[int] = []

    for raw in text.splitlines():
        line = raw.strip()
        for name, pattern in PATTERNS.items():
            m = pattern.match(line) if name != "cost" else pattern.search(line)
            if not m:
                continue
            if name == "injected":
                out["fault_kind"] = m.group(1)
                out["fault_target"] = m.group(2)
                out["true_value"] = int(m.group(3))
                out["corrupt_value"] = int(m.group(4))
            elif name == "flags":
                for key, val in zip(
                    ("propagated", "detected", "surfaced", "repaired",
                     "determinable"), m.groups()
                ):
                    out[key] = _BOOL.get(val)
            elif name == "sentinel_total":
                # The faulted half prints "(truth N, credulous M)" and the
                # sentinel prints "(truth N)". Both start the same way, so
                # the narrower pattern collects and the LAST one wins --
                # the sentinel check runs after the faulted half.
                sentinel_seen.append(int(m.group(1)))
            elif name in ("expected", "clean_total", "faulted_total",
                          "true_value", "corrupt_value"):
                out[name] = int(m.group(1))
            elif name in ("confidence", "cost"):
                out["cost_usd" if name == "cost" else name] = float(m.group(1))
            else:
                out[name] = m.group(1)

    if sentinel_seen:
        out["sentinel_total"] = sentinel_seen[-1]

    # Every field the probe can print, present as null when it did not --
    # so a reader can tell "the probe skipped the faulted half" from "the
    # recorder failed to parse it".
    for field in ("variant", "scenario", "expected", "clean_total",
                  "confidence", "fault_kind", "fault_target", "true_value",
                  "corrupt_value", "faulted_total", "outcome", "propagated",
                  "detected", "surfaced", "repaired", "determinable",
                  "cost_usd", "sentinel_total"):
        out.setdefault(field, None)
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    row = parse(text)
    # Provenance of the row itself, from the environment rather than the
    # console -- the workflow knows these and the console does not restate
    # all of them.
    row.update(
        run_id=os.environ.get("GITHUB_RUN_ID"),
        commit=os.environ.get("GITHUB_SHA"),
        fixture=os.environ.get("FIXTURE"),
        target=os.environ.get("TARGET"),
        model_level=os.environ.get("MODEL"),
        shape=os.environ.get("SHAPE"),
        event=os.environ.get("GITHUB_EVENT_NAME"),
    )
    print(json.dumps(row, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
