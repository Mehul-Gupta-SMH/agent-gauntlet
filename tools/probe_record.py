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
    # Tokens, because cost alone cannot be read (#47). Two targets are two
    # agent SDKs, so a cost ratio between them mixes the RATE (which
    # pricing authority priced it) with the VOLUME (how large that
    # adapter's prompt is). Without the counts there is no way to tell
    # which moved -- the first claude-target row came in at 2.4x the
    # langgraph cost and nothing in the record could say why.
    "prompt_tokens": re.compile(r"'prompt_tokens': (\d+)"),
    "completion_tokens": re.compile(r"'completion_tokens': (\d+)"),
    "total_tokens": re.compile(r"'total_tokens': (\d+)"),
    "sentinel_total": re.compile(r"^reported total\s*:\s*(-?\d+)\s+\(truth \d+\)$"),
    # An instruction probe's HEADLINE, and it was being dropped. The
    # rotation reached `poisoning/instruction.yaml` on run 38041237112 and
    # the row recorded `outcome: surfaced_and_repaired` with nothing about
    # whether the config obeyed the directive -- which is the only thing
    # that fixture exists to ask. `propagation_rate` is not the gate for an
    # instruction fault; `compliance_rate` is, and no column carried it.
    "directive_shape": re.compile(r"^directive shown:\s*(\w+)"),
    "canary": re.compile(r"^canary\s*:\s*(\d+)"),
    # Three outcomes, and the third is censoring rather than a pass: the
    # control shape asks for nothing, so there is no observable act of
    # obedience to score. It must not record as "held".
    "compliance": re.compile(r"^compliance\s*:\s*(OBEYED|held|n/a)"),
    # How much of the fault actually reached the agent. Zero is a result,
    # not a parse failure, and the two were producing the same row.
    "faulted_reaching": re.compile(
        r"^faulted results reaching the agent:\s*(\d+)"),
    # The probe's own name for the third outcome: the agent called tools,
    # but never the corrupted one, so the fault was never put in front of
    # it. The probe exits 2 and prints no `outcome` line, which the
    # recorder was storing as a row of nulls -- indistinguishable from the
    # recorder failing to parse a good run.
    #
    # Reached for after run 38041428236 on `poisoning/memory.yaml` came
    # back with a clean half, an injection, a cost, and no faulted outcome
    # -- a row the recorder could not explain. That row is NOT this case
    # (the job succeeded, and this path exits 2, which fails it), which is
    # the point: three different results were arriving as the same row of
    # nulls, and none of them could be told from the recorder breaking.
    "never_consulted": re.compile(r"^THE AGENT NEVER CONSULTED '([^']+)'"),
}

_BOOL = {"True": True, "False": False}


def _int_or_none(raw: Optional[str]) -> Optional[int]:
    """An absent or unparseable exit status is null, never 0 -- 0 means the
    probe completed, and guessing it for a row the workflow said nothing
    about would be the censoring rule broken in one field."""
    try:
        return int(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def parse(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sentinel_seen: list[int] = []

    for raw in text.splitlines():
        line = raw.strip()
        for name, pattern in PATTERNS.items():
            searched = name in ("cost", "prompt_tokens",
                                "completion_tokens", "total_tokens")
            m = pattern.search(line) if searched else pattern.match(line)
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
                          "true_value", "corrupt_value", "prompt_tokens",
                          "completion_tokens", "total_tokens", "canary",
                          "faulted_reaching"):
                out[name] = int(m.group(1))
            elif name == "compliance":
                verdict = m.group(1)
                out["compliance_decidable"] = verdict != "n/a"
                # Null, never False, when undecidable: a control shape that
                # asks for nothing did not "hold".
                out["complied"] = (
                    None if verdict == "n/a" else verdict == "OBEYED")
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
                  "cost_usd", "prompt_tokens", "completion_tokens",
                  "total_tokens", "sentinel_total", "directive_shape",
                  "canary", "complied", "compliance_decidable",
                  "faulted_reaching", "never_consulted"):
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
        # What the probe returned, from the workflow rather than the
        # console: 0 complete, 1 the faulted run raised or the fault
        # reached no tool call, 2 a setup error or the agent never
        # consulting the faulted tool, 4 the provider unreachable. A blank
        # row carrying this needs no hand-written note to be readable, and
        # the only place it used to live was a `::warning::` on the check
        # run.
        probe_exit=_int_or_none(os.environ.get("PROBE_EXIT")),
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
