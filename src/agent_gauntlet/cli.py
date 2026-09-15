"""`gauntlet` -- task spec in, deployable config out.

    gauntlet run fixtures/inventory/task.yaml --out runs/

One command walks every subsystem: generate variants, validate them, run the
matrix under clean/faulted pairs, score mechanically, write the ledger,
build the board, and export the winner as a `common/` folder.

`--offline` (the default for now) uses scripted policies and spends nothing.
A live run needs `--target` and real credentials; see `run_live`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import architect, board
from .analyze import stability
from .faults import FaultKind
from .ledger import Ledger, write_summary
from .matrix import run_matrix
from .spec import TaskSpec

DEFAULT_MODELS = {
    "cheap": "openai/gpt-4o-mini",
    "smart": "anthropic/claude-sonnet-5",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gauntlet", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a gauntlet end to end")
    run.add_argument("task", type=Path, help="path to a task spec YAML")
    run.add_argument("--out", type=Path, default=Path("runs"), help="output directory")
    run.add_argument("--repeats", type=int, default=3, help="k per (variant, scenario)")
    run.add_argument(
        "--seeds", type=int, default=2,
        help="independent seeds; >=2 enables the stability gate",
    )
    run.add_argument(
        "--fault", choices=[k.value for k in FaultKind],
        default=FaultKind.WRONG_VALUE.value,
    )
    run.add_argument(
        "--offline", action="store_true", default=True,
        help="use scripted policies (default; spends nothing)",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    return 1


def _run(args) -> int:
    task = TaskSpec.from_yaml(args.task)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"task        {task.id}  (oracle={task.oracle.value})")
    print(f"fingerprint {task.fingerprint()}")

    variants = architect.generate(
        out_dir=out / "variants", task=task, models=DEFAULT_MODELS
    )
    print(f"variants    {len(variants)} generated "
          f"({sum(v.is_sentinel for v in variants)} sentinel)")

    ledger = Ledger(out / "runs.jsonl")
    seeds = [f"seed{i}" for i in range(max(1, args.seeds))]
    for seed in seeds:
        run_matrix(
            task=task, variants=variants, ledger=ledger, base_seed=seed,
            repeats=args.repeats, fault_kind=FaultKind(args.fault),
            offline=bool(args.offline),
        )
    records = ledger.records()
    print(f"runs        {len(records)} across {len(seeds)} seed(s)")

    results = board.summarize(records)
    for r in results:
        for v in variants:
            if v.id == r.variant_id:
                r.is_sentinel = v.is_sentinel

    _print_board(results)
    _print_effects(results)
    rc = _print_gate(records, seeds)
    _export(results, variants, out)

    write_summary(
        out / "summary.json",
        {
            "task_id": task.id,
            "task_fingerprint": task.fingerprint(),
            "n_runs": len(records),
            "results": [r.model_dump() for r in results],
            "effects": [e.model_dump() for e in board.factor_effects(results)],
        },
    )
    print(f"\nwrote       {out/'runs.jsonl'}, {out/'summary.json'}")
    return rc


def _print_board(results) -> None:
    print("\n--- board " + "-" * 62)
    print(f"{'variant':<48}{'qual':>6}{'clean':>7}{'fault':>7}{'prop':>6}{'det':>6}")
    for tier in board.rank(results):
        for r in tier:
            det = "  n/a" if r.detection_rate is None else f"{r.detection_rate:>5.0%}"
            flag = "  [GATED: propagated]" if r.gated else ("  [sentinel]" if r.is_sentinel else "")
            print(
                f"{r.variant_id:<48}{r.quality:>6.0%}{r.clean_quality:>7.0%}"
                f"{r.faulted_quality:>7.0%}{r.propagation_rate:>6.0%}{det}{flag}"
            )


def _print_effects(results) -> None:
    print("\n--- per-factor effects " + "-" * 49)
    effects = board.factor_effects(results)
    if not effects:
        print("  (none -- variants declare no factors)")
        return
    for e in effects:
        levels = "  ".join(f"{k}={v:.0%}" for k, v in e.levels.items())
        print(f"{e.factor:<12} spread={e.spread:>5.0%}   {levels}")
    print(f"\n  {effects[0].caveat}")


def _print_gate(records, seeds) -> int:
    print("\n--- stability gate " + "-" * 53)
    if len(seeds) < 2:
        print("  skipped -- needs --seeds >= 2")
        return 0
    by_seed: dict[str, dict[str, float]] = {}
    for r in records:
        base = r.seed.split(":")[0]
        bucket = by_seed.setdefault(base, {})
        bucket[r.variant_id] = bucket.get(r.variant_id, 0.0) + float(r.score.correct)
    report = stability(by_seed)
    print(f"  variants={report.n_variants}  pairs={report.n_pairs}  "
          f"undefined tau={report.undefined_pairs}")
    print(f"  median tau={report.median_tau}  top-1 stability={report.top1_stability:.0%}")
    if report.undefined_pairs:
        print("  WARNING: undefined tau means variants tied -- likely a ceiling "
              "effect, and the gate measured nothing.")
    return 0


def _export(results, variants, out: Path) -> None:
    print("\n--- winner " + "-" * 61)
    champion = board.winner(results)
    if champion is None:
        print("  NO WINNER -- the top tier is tied or gated on propagation.")
        print("  That is the honest answer, not a failure to compute one.")
        return
    dest = board.export_winner(champion, variants, out / "winner")
    print(f"  {champion.variant_id}  quality={champion.quality:.0%}")
    print(f"  exported to {dest}")
    print(f"  run it:  commonadk validate {dest}")


if __name__ == "__main__":
    sys.exit(main())
