"""Run the M0 gate path end-to-end, offline.

No API keys, no cost, no model. Scripted policies stand in for agents so the
machinery -- schedule, interposition, scoring, ledger, stability -- can be
exercised and checked. The numbers below describe the harness, NOT agents:
scripted policies are deterministic, and determinism is exactly what the
real gate exists to test for its absence.

    python examples/offline_gate.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from agent_gauntlet import Ledger, TaskSpec, VariantSpec, run_matrix, stability
from agent_gauntlet.analyze import DetectionReport

ROOT = Path(__file__).resolve().parent.parent
TASK = TaskSpec.from_yaml(ROOT / "fixtures" / "inventory" / "audited.yaml")

VARIANTS = [
    VariantSpec(id="v_naive", factors={"prompt": "naive", "toolset": "records"}),
    VariantSpec(id="v_verify", factors={"prompt": "verifying", "toolset": "records+summary"}),
    VariantSpec(id="v_summary", factors={"prompt": "summary_only", "toolset": "summary"}),
    # Degraded by a tool set that cannot enumerate every record, not by a
    # prompt asking it to hurry -- experiment 003 showed a capable model
    # simply ignores the latter.
    VariantSpec(
        id="v_sentinel",
        factors={"prompt": "naive", "toolset": "records-partial"},
        is_sentinel=True,
    ),
]
SEEDS = ["seedA", "seedB", "seedC"]
REPEATS = 3


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Ledger(Path(tmp) / "runs.jsonl")
        for seed in SEEDS:
            run_matrix(
                task=TASK, variants=VARIANTS, ledger=ledger,
                base_seed=seed, repeats=REPEATS,
            )
        report(ledger)


def report(ledger: Ledger) -> None:
    records = ledger.records()
    print(f"task          : {TASK.id}  (oracle={TASK.oracle.value})")
    print(f"fingerprint   : {TASK.fingerprint()}")
    print(f"runs          : {len(records)}  "
          f"({len(VARIANTS)} variants x {len(TASK.scenarios)} scenarios "
          f"x {REPEATS} repeats x 2 conditions x {len(SEEDS)} seeds)")
    print(f"spend         : $0.00 (offline)")

    print("\n--- outcomes under fault " + "-" * 45)
    # `repaired` beside `detected` on purpose: noticing and fixing are
    # different capabilities, and a variant can do the first without the
    # second (#16).
    print(f"{'variant':<14}{'correct':>9}{'propagated':>12}{'detected':>10}"
          f"{'repaired':>10}{'surfaced':>10}{'false alarm':>13}")
    for v in VARIANTS:
        faulted = [r for r in records if r.variant_id == v.id and r.condition == "faulted"]
        clean = [r for r in records if r.variant_id == v.id and r.condition == "clean"]
        n = len(faulted) or 1
        print(
            f"{v.id:<14}"
            f"{sum(r.score.correct for r in faulted)/n:>9.0%}"
            f"{sum(r.score.propagated for r in faulted)/n:>12.0%}"
            f"{sum(r.score.detected for r in faulted)/n:>10.0%}"
            f"{sum(r.score.repaired for r in faulted)/n:>10.0%}"
            f"{sum(r.score.surfaced for r in faulted)/n:>10.0%}"
            f"{sum(r.score.false_alarm for r in clean)/max(1,len(clean)):>13.0%}"
        )

    print("\n--- detection, censoring-aware " + "-" * 39)
    for v in VARIANTS:
        rs = [r for r in records if r.variant_id == v.id and r.condition == "faulted"]
        d = DetectionReport(
            n_runs=len(rs),
            n_with_evidence=sum(r.score.evidence_available for r in rs),
            detected=sum(r.score.detected for r in rs),
            surfaced=sum(r.score.surfaced for r in rs),
            propagated=sum(r.score.propagated for r in rs),
            false_alarms=0,
            n_clean=0,
            latencies=[r.score.detect_latency for r in rs if r.score.detect_latency is not None],
        )
        rate = "n/a" if d.detection_rate is None else f"{d.detection_rate:.0%}"
        lat = "n/a" if d.median_latency is None else f"{d.median_latency:.0f}"
        print(f"{v.id:<14} evidence-reachable runs={d.n_with_evidence:<4} "
              f"detection={rate:<6} median TTD={lat} steps")

    print("\n--- C0.7 stability (headline) " + "-" * 40)
    by_seed: dict[str, dict[str, float]] = {}
    for r in records:
        base = r.seed.split(":")[0]
        by_seed.setdefault(base, {})
        by_seed[base][r.variant_id] = by_seed[base].get(r.variant_id, 0.0) + float(
            r.score.correct
        )
    s = stability(by_seed)
    print(f"variants={s.n_variants}  seed pairs={s.n_pairs}  "
          f"undefined tau={s.undefined_pairs}")
    print(f"taus={s.taus}  median={s.median_tau}")
    print(f"top-1 stability={s.top1_stability:.0%}")

    print("\nNOTE: scripted policies are deterministic, so perfect stability")
    print("here measures the harness, not agents. The real gate needs models.")


if __name__ == "__main__":
    main()
