"""Experiment 008 — does the check actually catch a regression?

Post 002 states publicly that no regression has ever been caught with this
tool, which makes "regression testing for agent robustness" a design rather
than a result. This closes that.

The shape, deliberately end to end through the real API rather than a unit
test: certify a configuration, degrade it, re-run, and see whether the check
notices without being told where to look.

Offline and free. What it demonstrates is that the MECHANISM works -- a
degradation of a known size is detected, named, and refused. It does not
demonstrate that a real prompt edit on a real model produces a degradation
of that size; that needs live runs and is the honest limit stated in the
write-up.
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_gauntlet import (
    Ledger, TaskSpec, VariantSpec, board, certify, offline, run_matrix,
)
from agent_gauntlet.certify import MetricVerdict, RegressionBudget, Verdict
from agent_gauntlet.stats import detectable_difference

ROOT = Path(__file__).resolve().parents[2]
TASK = TaskSpec.from_yaml(ROOT / "fixtures" / "inventory" / "audited.yaml")
OUT = Path(__file__).parent

# Enough repeats that the resolution can see the drop being checked for.
# Chosen BEFORE the run, from the arithmetic, not after seeing the result.
REPEATS = 30
BUDGET = RegressionBudget(max_quality_drop=0.20)


def run(name: str, seed: str) -> list:
    variants = [VariantSpec(id="auditor",
                            factors={"prompt": "verifying",
                                     "toolset": "records+summary"})]
    ledger = Ledger(OUT / f"{name}.jsonl")
    run_matrix(task=TASK, variants=variants, ledger=ledger, base_seed=seed,
               repeats=REPEATS)
    return board.summarize(ledger.records())


def main() -> int:
    print("Experiment 008 — catching a regression\n" + "=" * 52)
    print(f"task fingerprint   {TASK.fingerprint()}")
    print(f"budget             quality may drop {BUDGET.max_quality_drop:.0%}")
    print(f"required resolution {BUDGET.needed_resolution():.0%}\n")

    # --- 1. certify the healthy configuration ---------------------------
    before = run("before", "seed-a")
    n = before[0].n_runs
    print(f"BEFORE   n={n} per contender, resolution {detectable_difference(n):.0%}")
    for key in ("quality", "propagation_rate", "detection_rate"):
        ci = before[0].intervals.get(key)
        value = getattr(before[0], key)
        print(f"  {key:<18} {value:.0%}" + (f"  [{ci.low:.0%},{ci.high:.0%}]" if ci else ""))

    certs = certify.issue_all(before, task_fingerprint=TASK.fingerprint(),
                              issued_by="experiment-008")
    certify.save(certs, OUT / "certificate.json")
    print(f"\ncertified, expires in {certs[0].expires_after_days} days\n")

    # --- 2. the edit ----------------------------------------------------
    # Somebody removes the reconciliation step from the `verifying` prompt
    # six weeks later. Offline, the stand-in is swapping the policy: same
    # variant id, same tool grant, different behaviour.
    print("--- someone edits the prompt: verifying no longer reconciles ---\n")
    offline.POLICIES["verifying"] = offline.naive

    after = run("after", "seed-b")
    print(f"AFTER    n={after[0].n_runs} per contender")
    for key in ("quality", "propagation_rate", "detection_rate"):
        ci = after[0].intervals.get(key)
        value = getattr(after[0], key)
        print(f"  {key:<18} {value:.0%}" + (f"  [{ci.low:.0%},{ci.high:.0%}]" if ci else ""))

    # --- 3. the check ---------------------------------------------------
    report = certify.compare(certs, after, budget=BUDGET)
    print(f"\n--- check ---")
    for comp in report.variants:
        for change in comp.changes:
            if change.verdict in (MetricVerdict.REGRESSED, MetricVerdict.IMPROVED):
                flag = " <-- REGRESSION" if change.exceeds_budget else ""
                print(f"  {change.metric:<18} {change.before:.0%} -> "
                      f"{change.after:.0%}  {change.verdict.value}{flag}")

    print(f"\nVERDICT: {report.verdict.value.upper()}")
    print(f"         {report.reason}")

    caught = [c for v in report.variants for c in v.changes
              if c.verdict is MetricVerdict.REGRESSED and c.exceeds_budget]
    ok = report.verdict is Verdict.FAIL and any(
        c.metric == "propagation_rate" for c in caught)
    print("\n" + ("CAUGHT — the check failed, naming propagation." if ok
                  else "NOT CAUGHT — the mechanism did not work."))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
