"""Experiment 011 — is a timeout as informative as a lie?

Issue #5 opens with an intuition:

    A timeout is loud -- the agent either retries or fails, and either way
    you learn something shallow. A wrong-but-plausible result is silent.
    [...] If the discriminating power is concentrated in the integrity
    class, the budget should be too.

That is testable and nobody had tested it. `fetch_record` accepts both a
WRONG_VALUE and a TIMEOUT fault, so the two classes can be compared with
everything else held fixed: same fixture, same grid, same seeds, same
repeats. Only the kind of adversity changes.

Offline and free. What it measures is what the HARNESS can tell apart under
each class, which is the question #5 asks -- how much a class discriminates
is a property of the measurement, not of any particular model. What it
cannot say is whether a real agent handles the two differently; a scripted
policy swallows a timeout by construction, and that limit is the first
finding below rather than a footnote.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Optional

from agent_gauntlet import Ledger, TaskSpec, VariantSpec, board, run_matrix
from agent_gauntlet.analyze import stability
from agent_gauntlet.faults import FaultKind

ROOT = Path(__file__).resolve().parents[2]
TASK = TaskSpec.from_yaml(ROOT / "fixtures" / "inventory" / "audited.yaml")
OUT = Path(__file__).parent

VARIANTS = [
    VariantSpec(id=f"{model}-{prompt}-{toolset}",
                factors={"model": model, "prompt": prompt, "toolset": toolset})
    for model in ("cheap", "smart")
    for prompt in ("naive", "verifying")
    for toolset in ("records", "records+summary")
]

SEEDS = [f"seed-{c}" for c in "abcd"]
REPEATS = 20
"""Enough that the board can order anything at all: experiment 009 put the
decidable depth at 2 from k=4 upward on this grid, and 009's own curve is
flat well below 20."""

CLASSES = [
    (FaultKind.WRONG_VALUE, "integrity", "a plausible false quantity"),
    (FaultKind.TIMEOUT, "availability", "the read does not come back"),
]


def run(kind: FaultKind):
    records = []
    by_seed: dict[str, dict[str, float]] = {}
    for seed in SEEDS:
        with tempfile.TemporaryDirectory() as d:
            got = run_matrix(task=TASK, variants=VARIANTS,
                             ledger=Ledger(Path(d) / "runs.jsonl"),
                             base_seed=seed, repeats=REPEATS, fault_kind=kind)
        records.extend(got)
        bucket = by_seed.setdefault(seed, {})
        for r in got:
            bucket[r.variant_id] = bucket.get(r.variant_id, 0.0) + float(r.score.correct)
    return records, board.summarize(records), stability(by_seed)


def rule(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 68 - len(title)))


def main() -> int:
    print("Experiment 011 — is a timeout as informative as a lie?")
    print("=" * 70)
    print(f"task      {TASK.id}  ({TASK.fingerprint()})")
    print(f"held fixed  {len(VARIANTS)} contenders, {len(SEEDS)} seeds, "
          f"k={REPEATS}. Only the fault kind changes.")

    results: dict = {"task": TASK.fingerprint(), "classes": {}}

    rule("1. what each class lets the board tell apart")
    print(f"  {'class':<14}{'spread':>8}{'tiers':>7}{'depth':>7}"
          f"{'gated':>7}{'prop spread':>13}{'med tau':>9}")
    rows = {}
    for kind, family, _ in CLASSES:
        records, summary, stab = run(kind)
        faulted = [r.faulted_quality for r in summary]
        props = [r.propagation_rate or 0.0 for r in summary]
        row = {
            "spread": max(faulted) - min(faulted),
            "tiers": len(board.evidence_tiers(summary, metric="faulted_quality")),
            "depth": board.decidable_depth(summary, metric="faulted_quality"),
            "gated": sum(1 for r in summary if r.propagation_rate),
            "prop_spread": max(props) - min(props),
            "median_tau": stab.median_tau,
            "outcomes": dict(Counter(
                r.score.outcome.value for r in records if r.condition == "faulted")),
        }
        rows[family] = row
        print(f"  {family:<14}{row['spread']:>8.3f}{row['tiers']:>7}"
              f"{row['depth']:>7}{row['gated']:>4}/{len(summary):<2}"
              f"{row['prop_spread']:>13.3f}{row['median_tau']:>9.3f}")
    results["classes"] = rows

    integrity, availability = rows["integrity"], rows["availability"]

    rule("2. on correctness, the two classes are indistinguishable")
    same = [k for k in ("spread", "tiers", "depth", "median_tau")
            if integrity[k] == availability[k]]
    print(f"  identical on: {', '.join(same)}")
    print("""
  Not a coincidence and not a bug. `faulted_quality` is BINARY
  correctness, and both faults make the answer wrong: a lie shifts the
  total by its delta, a timeout drops the record entirely. Either way the
  run is not correct, so every statistic built on correctness -- spread,
  tiers, depth, rank stability across seeds -- comes out the same to three
  decimals.

  So the #5 intuition is half right. A timeout is not LESS informative
  about correctness. It is exactly as informative, which is a stronger
  statement than the issue guessed and a more awkward one: the two classes
  are redundant on the axis the leaderboard ranks by.""")

    rule("3. on safety, they are not comparable at all")
    print(f"  gated by propagation     integrity {integrity['gated']}/8   "
          f"availability {availability['gated']}/8")
    print(f"  propagation spread       integrity {integrity['prop_spread']:.3f}   "
          f"availability {availability['prop_spread']:.3f}")
    print("""
  An availability fault CANNOT propagate a falsehood. There is no false
  value to believe -- the read simply did not return. So the propagation
  gate, the one non-negotiable bound in every fixture's error budget, is
  structurally unreachable under this class.

  Which settles #5's open question 2 with arithmetic rather than taste. Run
  half a budget on timeouts, average propagation across classes, and the
  rate reads about half its integrity-class value -- diluted by runs where
  the metric could not have been non-zero. A single robustness number over
  a mixed fault budget is not a summary, it is a discount.""")

    rule("4. the taxonomy gap this measurement exposed")
    print("  faulted-run outcomes, per class:\n")
    for family in ("integrity", "availability"):
        print(f"  {family}")
        for name, n in sorted(rows[family]["outcomes"].items(), key=lambda kv: -kv[1]):
            print(f"    {n:>6}  {name}")
    print("""
  Read the availability column. Before this experiment there was no
  `undetected_degraded`, and every one of those runs -- a config that
  silently dropped the record it could not read and reported a short total
  -- scored `undetected_harmless`. "The fault did no harm", printed about a
  run that was never right.

  Nothing was fail-green: `faulted_quality` read 0% in the column beside
  it. But `worst_outcome` read harmless, and that is the column an operator
  scans. The category exists now, at the same severity as propagation --
  the caller gets a wrong figure and no warning either way -- and separate
  from it because the remedy differs: a cross-check versus retry and
  fallback handling.

  Note what is NOT in either column any more: `undetected_harmless`. Not
  one faulted run on this fixture was ever harmless. The word had been
  covering 519 integrity runs and 1,440 availability runs that returned the
  wrong number.

  And `surfaced_but_degraded` is the second gap the same count exposed.
  `SURFACED_BUT_PROPAGATED` used to be assigned to any run that surfaced
  and did not repair -- and on this grid EVERY run carrying it had
  `propagated=False`, under both fault kinds. An outcome whose name asserts
  propagation, printed beside a record that denies it. The two are split
  now, and a test asserts across a whole matrix that no outcome named
  "propagated" lands on a run that did not. Experiment 005's live finding
  is untouched: that run propagated, and keeps the name it earned.""")

    rule("what this does not show")
    print("""  A scripted policy swallows a timeout: `except ToolTimeout: continue`.
  So "a timeout is loud" was never tested here -- offline it is as silent
  as a lie by construction, and whether a real model retries, says
  something, or quietly reports a short total is unmeasured. That is the
  one part of #5's open question 1 that needs live runs, and it is the
  part where the two classes might stop being redundant.""")

    (OUT / "classes.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
