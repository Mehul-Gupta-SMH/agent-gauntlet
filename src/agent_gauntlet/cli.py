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
    "cheap": "anthropic/claude-haiku-4-5",
    "smart": "anthropic/claude-sonnet-5",
}
"""Both levels on one provider, deliberately.

A cross-provider grid makes the model axis unrunnable on a single
credential: half the variants demand a key the operator may not have, and
the preflight correctly refuses the whole matrix rather than compare an
uneven grid. Haiku-vs-Sonnet is a real capability difference and needs one
key, which is what makes the axis usable at all.

Override with --models to compare across providers, once the credentials
for every provider in the grid are present.
"""


def parse_models(pairs) -> dict:
    """`--models cheap=anthropic/claude-haiku-4-5` -> {alias: model}."""
    models: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"--models expects alias=model, got {pair!r}")
        alias, model = pair.split("=", 1)
        models[alias.strip()] = model.strip()
    return models or dict(DEFAULT_MODELS)


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
        "--live", action="store_true",
        help="run against real models (COSTS MONEY); otherwise scripted policies",
    )
    run.add_argument(
        "--models", nargs="*", metavar="ALIAS=MODEL",
        help="model grid, e.g. cheap=anthropic/claude-haiku-4-5 "
             "smart=anthropic/claude-sonnet-5",
    )
    run.add_argument(
        "--target", default="claude",
        help="CommonADK SDK target for --live (default: claude)",
    )
    probe = sub.add_parser(
        "probe",
        help="one live run, printing the raw reply -- proves the path before a matrix",
    )
    probe.add_argument("task", type=Path)
    probe.add_argument("--out", type=Path, default=Path("runs-probe"))
    probe.add_argument("--target", default="langgraph")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "probe":
        return _probe(args)
    return 1


def _probe(args) -> int:
    """One live call against one variant, with the raw reply shown.

    The cheapest possible check that the whole live path works: build the
    project, drive the SDK, get a Trace, find the final text, parse it. A
    silent failure in any of those scores every run as unanswered, and
    finding that out from a full matrix costs the whole matrix.
    """
    from .faults import FaultSchedule
    from .interpose import run_context
    from .live import MissingCredentials, live_executor, preflight, parse_answer

    task = TaskSpec.from_yaml(args.task)
    out = Path(args.out)
    variants = architect.generate(
        out_dir=out / "variants", task=task, models=DEFAULT_MODELS,
        targets=[args.target], prompts=["verifying"], toolsets=["records+summary"],
        include_sentinel=False,
    )
    variant = next(v for v in variants if v.factors.get("model") == "smart")
    print(f"probing {variant.id} on target={args.target}")

    try:
        preflight([variant], target=args.target)
    except MissingCredentials as exc:
        print(f"\nPREFLIGHT FAILED\n{exc}")
        return 2

    scenario = task.scenarios[0]
    execute = live_executor(args.target)

    # Capture the Trace itself, not just the parsed answer. Without this the
    # probe cannot distinguish "the model ignored the output contract" from
    # "we cannot find the reply in the trace at all" -- two failures with
    # completely different fixes, which cost a live call each to tell apart.
    captured: dict = {}
    from . import live as _live

    original = _live._final_text

    def _capture(trace):
        captured["trace"] = trace
        text = original(trace)
        captured["text"] = text
        return text

    _live._final_text = _capture
    try:
        with run_context(scenario.records, FaultSchedule.clean("probe")) as ctx:
            answer = execute(variant, "probe")
    finally:
        _live._final_text = original

    trace = captured.get("trace")
    raw = captured.get("text", "")

    print(f"\nexpected total : {scenario.expected_total}")
    print(f"parsed total   : {answer.total}")
    print(f"flagged anomaly: {answer.flagged_anomaly}")
    print(f"tool calls     : {[c['tool'] for c in ctx.calls]}")

    if trace is not None:
        kinds: dict[str, int] = {}
        for event in getattr(trace, "events", []) or []:
            name = type(event).__name__
            kinds[name] = kinds.get(name, 0) + 1
        print(f"trace events   : {kinds}")
        try:
            rollup = trace.rollup()
            print(f"tokens/cost    : {rollup.get('llm_calls')}")
        except Exception as exc:  # pragma: no cover - diagnostics only
            print(f"rollup failed  : {type(exc).__name__}: {exc}")

    print(f"\nfinal text ({len(raw)} chars):")
    print("-" * 60)
    print(raw[:2000] if raw else "(EMPTY -- no RunFinished.final_text and no "
          "AgentFinished.output_summary in the trace)")
    print("-" * 60)

    if answer.total is None:
        print("\nFAILED: no TOTAL:/ANOMALY: contract in the reply.")
        from .live import normalize_reply

        if not raw:
            print("Cause: the trace carried NO final text. Harness problem --")
            print("the reply exists but is unreachable through these events.")
        elif normalize_reply(raw) != raw:
            print("Cause: the reply arrived as structured content blocks and")
            print("needed flattening. Harness problem, not a model problem.")
        else:
            print("Cause: the model produced plain text but not in the")
            print("required format. A real instruction-following result.")
        print("Do NOT run the matrix until this parses -- every run would")
        print("score as unanswered and the whole spend would be wasted.")
        return 1
    print("\nOK -- the live path works end to end. The matrix is safe to run.")
    return 0


def _run(args) -> int:
    task = TaskSpec.from_yaml(args.task)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"task        {task.id}  (oracle={task.oracle.value})")
    print(f"fingerprint {task.fingerprint()}")

    models = parse_models(getattr(args, "models", None))
    print(f"models      {', '.join(f'{k}={v}' for k, v in sorted(models.items()))}")
    variants = architect.generate(
        out_dir=out / "variants", task=task, models=models,
        targets=[args.target],
    )
    print(f"variants    {len(variants)} generated "
          f"({sum(v.is_sentinel for v in variants)} sentinel)")

    executor = None
    offline = not args.live
    if args.live:
        from .live import MissingCredentials, live_executor, preflight

        try:
            preflight(variants, target=args.target)
        except MissingCredentials as exc:
            print(f"\nPREFLIGHT FAILED\n{exc}")
            return 2
        executor = live_executor(args.target)
        print(f"mode        LIVE via target={args.target} -- this spends money")
    else:
        print("mode        offline (scripted policies, no spend)")

    ledger = Ledger(out / "runs.jsonl")
    seeds = [f"seed{i}" for i in range(max(1, args.seeds))]
    for seed in seeds:
        run_matrix(
            task=task, variants=variants, ledger=ledger, base_seed=seed,
            repeats=args.repeats, fault_kind=FaultKind(args.fault),
            executor=executor, offline=offline,
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
    rc = _print_gate(records, seeds, task)
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
    print(f"{'variant':<46}{'qual':>6}{'acc':>6}{'clean':>7}{'fault':>7}"
          f"{'prop':>6}{'det':>6}{'FA':>5}")
    for tier in board.rank(results):
        for r in tier:
            det = "  n/a" if r.detection_rate is None else f"{r.detection_rate:>5.0%}"
            flag = "  [GATED: propagated]" if r.gated else ("  [sentinel]" if r.is_sentinel else "")
            print(
                f"{r.variant_id:<46}{r.quality:>6.0%}{r.accuracy:>6.2f}"
                f"{r.clean_quality:>7.0%}{r.faulted_quality:>7.0%}"
                f"{r.propagation_rate:>6.0%}{det}{r.false_alarm_rate:>5.0%}{flag}"
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


def _print_gate(records, seeds, task) -> int:
    """Report the gate, and judge it against the pre-registered bar.

    Returns non-zero when the gate FAILS, so a CI job cannot go green on an
    unstable measurement.
    """
    print("\n--- stability gate " + "-" * 53)
    gate = task.gate

    if len(seeds) < 2:
        print("  skipped -- needs --seeds >= 2")
        return 0

    by_seed: dict[str, dict[str, float]] = {}
    for r in records:
        base = r.seed.split(":")[0]
        bucket = by_seed.setdefault(base, {})
        bucket[r.variant_id] = bucket.get(r.variant_id, 0.0) + float(r.score.correct)
    report = stability(by_seed)

    print(f"  variants={report.n_variants}  seeds={len(seeds)}  "
          f"pairs={report.n_pairs}  undefined tau={report.undefined_pairs}")
    print(f"  median tau      = {report.median_tau}")
    print(f"  top-1 stability = {report.top1_stability:.0%}")

    if gate is None:
        print("\n  NO VERDICT -- the spec pre-registers no thresholds, and the")
        print("  gate will not invent one after seeing the numbers.")
        return 0

    print(f"\n  pre-registered bar (fingerprint {task.fingerprint()}):")
    print(f"    median tau      >= {gate.min_median_tau}")
    print(f"    top-1 stability >= {gate.min_top1_stability}")
    print(f"    seeds           >= {gate.min_seeds}")
    print(f"    set by {gate.set_by} on {gate.set_at}")

    failures: list[str] = []
    if len(seeds) < gate.min_seeds:
        failures.append(f"only {len(seeds)} seeds, need {gate.min_seeds}")
    if report.undefined_pairs:
        # A ranking with large tie groups cannot be unstable, so a passing
        # tau here would be vacuous rather than reassuring (#17).
        failures.append(
            f"{report.undefined_pairs} seed pair(s) had undefined tau -- "
            "ties, so the ranking did not discriminate"
        )
    if report.median_tau is None:
        failures.append("median tau undefined")
    elif report.median_tau < gate.min_median_tau:
        failures.append(f"median tau {report.median_tau:.3f} < {gate.min_median_tau}")
    if report.top1_stability is None:
        failures.append("top-1 stability undefined")
    elif report.top1_stability < gate.min_top1_stability:
        failures.append(
            f"top-1 stability {report.top1_stability:.2f} < {gate.min_top1_stability}"
        )

    if failures:
        print("\n  GATE: FAIL")
        for f in failures:
            print(f"    - {f}")
        print("\n  Per plan.md: stop and fix measurement before building more.")
        return 3

    print("\n  GATE: PASS -- the ranking held across seeds at the bar set in advance.")
    return 0


def _export(results, variants, out: Path) -> None:
    print("\n--- winner " + "-" * 61)
    champion = board.winner(results)
    if champion is None:
        print("  NO WINNER -- more than one config is non-dominated, or every")
        print("  config is gated. That is the honest answer, not a failure to")
        print("  compute one: the remaining tradeoff is yours to make.")
        return
    dest = board.export_winner(champion, variants, out / "winner")
    print(f"  {champion.variant_id}")
    print(f"  accuracy={champion.accuracy:.2f}  quality={champion.quality:.0%}  "
          f"false alarms={champion.false_alarm_rate:.0%}  propagation={champion.propagation_rate:.0%}")
    print(f"  exported to {dest}")
    print(f"  run it:  commonadk validate {dest}")


if __name__ == "__main__":
    sys.exit(main())
