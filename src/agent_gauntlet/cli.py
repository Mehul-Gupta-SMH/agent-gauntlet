"""`gauntlet` -- task spec in, deployable config out.

    gauntlet run fixtures/inventory/audited.yaml --out runs/

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
    probe.add_argument(
        "--model", default="cheap", choices=sorted(DEFAULT_MODELS),
        help="which grid level to probe (default: cheap, the lowest-cost model)",
    )
    probe.add_argument(
        "--scenario", default=None,
        help="which scenario to probe (default: the first)",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "probe":
        return _probe(args)
    return 1


_NEVER_THE_PROVIDER = (
    ImportError, AttributeError, TypeError, ValueError,
    KeyError, IndexError, OSError, NotImplementedError,
)
"""Failures a remote service cannot cause.

A missing package, a bad attribute, a wrong signature -- these are always
this repo or its environment. `ModuleNotFoundError` is an `ImportError`,
which is the specific case that shipped a green probe having called nothing.
`OSError` covers `FileNotFoundError`; the connection errors that subclass it
are caught by the markers below, which are checked first.
"""

_PROVIDER_SHAPED = (
    "timeout", "timed out", "connection", "unreachable", "overloaded",
    "rate limit", "rate_limit", "too many requests", "quota",
    "429", "500", "502", "503", "529",
    "service unavailable", "temporarily unavailable", "try again",
    "apiconnection", "apistatus", "apitimeout", "apierror",
    "remotedisconnected", "ssl", "econnreset", "authentication",
    "unauthorized", "invalid api key", "credit balance",
)
"""Substrings that positively mark a failure as the provider's or the
network's, matched against the exception type name and its message.

Deliberately a positive test. The alternative -- treat anything we do not
recognise as an outage -- is how a probe reports "provider outage" for a
missing import.
"""


def _provider_unreachable(exc: BaseException) -> bool:
    """Did the model genuinely fail to answer, for reasons not ours?

    Unknown failures are OURS. A false red is annoying and actionable; a
    false green is invisible, and the point of the probe is to be believed.

    The whole chain is inspected, not just the outermost exception: SDKs
    wrap, and a lazily-imported dependency surfaces as a `RuntimeError`
    whose `__cause__` is the `ModuleNotFoundError` that actually explains it.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__

    # Checked before the never-the-provider types because these subclass
    # OSError, which is in that tuple for FileNotFoundError's sake.
    if any(isinstance(e, (ConnectionError, TimeoutError)) for e in chain):
        return True

    # Any import/type/attribute failure anywhere in the chain settles it.
    # This also stops a message from matching by accident -- an
    # `ImportError: cannot import name 'Timeout'` contains "timeout".
    if any(isinstance(e, _NEVER_THE_PROVIDER) for e in chain):
        return False

    haystack = " ".join(f"{type(e).__name__} {e}" for e in chain).lower()
    return any(marker in haystack for marker in _PROVIDER_SHAPED)


def _probe(args) -> int:
    """One live call against one variant, with the raw reply shown.

    The cheapest possible check that the whole live path works: build the
    project, drive the SDK, get a Trace, find the final text, parse it. A
    silent failure in any of those scores every run as unanswered, and
    finding that out from a full matrix costs the whole matrix.

    Exit codes, because this runs unattended in CI:

    ==  ===================================================================
    0   the path works; the matrix is safe to run
    1   the path is broken -- a real defect, and the build should go red
    2   no credential
    4   the model was unreachable -- provider outage, rate limit, revoked
        key. NOT this commit's fault, and a build must not go red for it
    ==  ===================================================================

    Code 4 exists because of what happened to `ci.yml`: a red signal nobody
    can act on is indistinguishable from the background, and it hides the
    red signals that matter. A probe that fails the build when Anthropic has
    a bad afternoon would reintroduce exactly that.

    **4 must be earned, never assumed.** The first version of this decided
    it by absence -- nothing reached `_final_text`, so "the call never came
    back, so it must be the network". A missing `langchain_core` satisfies
    that too, and the first automatic probe reported a provider outage,
    exited 0, and went green having called no model at all. A false green is
    worse than the false red it was avoiding: nobody investigates a pass.

    So the default is now *ours*. Only a positively provider-shaped failure
    earns 4; anything unrecognised is this repo's problem and goes red.
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
    # The cheapest level by default. The probe proves the *path* -- build,
    # run, trace, parse -- and the path does not care which model walked it.
    # Paying Sonnet rates to learn that `get_runner` still works is waste on
    # every push, and Haiku is the stricter canary anyway: if the weakest
    # model in the grid reconciles, the rest do.
    level = getattr(args, "model", "cheap")
    variant = next(v for v in variants if v.factors.get("model") == level)
    print(f"probing {variant.id} on target={args.target} "
          f"({level}={DEFAULT_MODELS[level]})")

    try:
        preflight([variant], target=args.target)
    except MissingCredentials as exc:
        print(f"\nPREFLIGHT FAILED\n{exc}")
        return 2

    scenario = (
        task.scenario(args.scenario) if getattr(args, "scenario", None)
        else task.scenarios[0]
    )
    print(f"scenario {scenario.id}: {len(scenario.records)} records, "
          f"audit covers {len(scenario.audited_ids)} "
          f"({scenario.audited_total} of {scenario.expected_total})")
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
        with run_context(
            scenario.records,
            FaultSchedule.clean("probe"),
            set(architect.TOOLSETS[variant.factors["toolset"]]),
            scenario.audited_ids,
        ) as ctx:
            answer = execute(variant, "probe")
    except Exception as exc:
        print(f"\n{type(exc).__name__}: {exc}")
        if "trace" in captured:
            print("\nFAILED: the run produced a trace and then raised. That is")
            print("ours -- the defect is between the trace and the parsed answer.")
            return 1
        if _provider_unreachable(exc):
            print("\nUNREACHABLE: the model could not be reached.")
            print("Provider outage, rate limit, or a revoked key. This says")
            print("nothing about the commit -- not a build failure.")
            return 4
        print("\nFAILED: the run raised before any model reply arrived, and")
        print("the failure is not provider-shaped -- a missing dependency, a")
        print("bad import, a broken build. Ours, and the build should be red.")
        return 1
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

    # Correctness is the experiment's subject, not a build invariant, so
    # none of this changes the exit code. It is printed because one case is
    # worth knowing before committing $5: under a partial audit, a model
    # that reports the audited figure has not done the reconciliation, and
    # every verifying variant in the matrix will undercount identically.
    model_name = DEFAULT_MODELS[level]
    if answer.total == scenario.expected_total:
        print(f"ANSWER: correct -- the grand total ({model_name}).")
    elif (
        scenario.audited_total != scenario.expected_total
        and answer.total == scenario.audited_total
    ):
        print(
            f"ANSWER: the AUDITED figure ({scenario.audited_total}), not the "
            f"grand total ({scenario.expected_total}) -- on {model_name}.\n"
            "  The reconciliation instruction did not land for THIS model. On\n"
            "  the cheap level that is one grid cell, not a verdict on the\n"
            "  fixture: re-probe with --model smart before concluding the\n"
            "  prompt factor cannot be measured."
        )
    else:
        print(
            f"ANSWER: wrong ({answer.total} vs {scenario.expected_total}) on "
            f"{model_name}. A result about the model, not the path."
        )
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
    held = _export(results, variants, out, records)

    write_summary(
        out / "summary.json",
        {
            "task_id": task.id,
            "task_fingerprint": task.fingerprint(),
            "n_runs": len(records),
            "results": [r.model_dump() for r in results],
            "effects": [e.model_dump() for e in board.factor_effects(results)],
            # The reportable winner, or null. Deliberately a separate key
            # from `results`: a consumer that wants "the answer" should get
            # the held-out number, not the biased one it can compute itself.
            "held_out_winner": (
                {**held.model_dump(), "optimism": held.optimism,
                 "held_up": held.held_up}
                if held else None
            ),
        },
    )
    print(f"\nwrote       {out/'runs.jsonl'}, {out/'summary.json'}")
    return rc


def _print_board(results) -> None:
    """The leaderboard, with what it costs and how fast it notices.

    cost and time-to-detect were both measured from the first run and
    neither reached the output -- a cost-aware board that ranks on accuracy
    alone (#11, #14), and a project founded on "how fast does it surface the
    fault" that never printed the answer (#16).
    """
    print("\n--- board " + "-" * 62)
    frontier = {r.variant_id for r in board.pareto(
        [r for r in results if not r.is_sentinel],
        objectives=("accuracy", "cost_usd"), maximize=(True, False),
    )}
    print(f"{'variant':<34}{'qual':>6}{'acc':>6}{'clean':>7}{'fault':>7}"
          f"{'prop':>6}{'det':>6}{'FA':>5}{'ttd':>6}{'$/run':>10}")
    for tier in board.rank(results):
        for r in tier:
            det = "   n/a" if r.detection_rate is None else f"{r.detection_rate:>6.0%}"
            # n/a, never 0: nothing detected means no latency to report, and
            # a 0 there would read as "noticed instantly" (#16).
            ttd = (
                "   n/a" if r.median_detect_latency is None
                else f"{r.median_detect_latency:>6.0f}"
            )
            # Likewise: an unpriced run is not a free one (#14).
            per_run = r.cost_per_run
            cost = "       n/a" if per_run is None else f"{per_run:>10.4f}"
            if r.gated:
                flag = "  [GATED: propagated]"
            elif r.is_sentinel:
                flag = "  [sentinel]"
            elif r.variant_id in frontier:
                flag = "  <- frontier"
            else:
                flag = ""
            print(
                f"{r.label:<34}{r.quality:>6.0%}{r.accuracy:>6.2f}"
                f"{r.clean_quality:>7.0%}{r.faulted_quality:>7.0%}"
                f"{r.propagation_rate:>6.0%}{det}{r.false_alarm_rate:>5.0%}"
                f"{ttd}{cost}{flag}"
            )

    if len(frontier) > 1:
        print(f"\n  {len(frontier)} configs are on the accuracy/cost frontier -- none")
        print("  dominates the others, so the tradeoff is yours (#11). Ranking")
        print("  on accuracy alone would have hidden that.")
    priced = [r for r in results if r.cost_complete]
    if priced:
        print(f"\n  measured spend: ${sum(r.cost_usd for r in priced):.4f} over "
              f"{sum(r.n_runs for r in priced)} priced runs, "
              f"{sum(r.total_tokens for r in priced):,} tokens")
    else:
        print("\n  no run reported a price (offline, or the roll-up carried none)")


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


def _export(results, variants, out: Path, records=None):
    """Report the winner, and report it honestly.

    The headline number is measured on replications that took no part in
    choosing the winner. Quoting the selection score instead reports the
    maximum of N noisy estimates, which is biased upward by construction
    and gets worse the wider the search -- the winner's curse (#20).
    """
    print("\n--- winner " + "-" * 61)
    champion = board.winner(results)
    if champion is None:
        print("  NO WINNER -- more than one config is non-dominated, or every")
        print("  config is gated. That is the honest answer, not a failure to")
        print("  compute one: the remaining tradeoff is yours to make.")
        return None

    held = None
    if records is not None:
        held = board.held_out_winner(
            records, sentinels=[v.id for v in variants if v.is_sentinel]
        )

    dest = board.export_winner(champion, variants, out / "winner")
    print(f"  {champion.variant_id}")

    if held is None:
        print(f"  accuracy={champion.accuracy:.2f}  quality={champion.quality:.0%}  "
              f"false alarms={champion.false_alarm_rate:.0%}  "
              f"propagation={champion.propagation_rate:.0%}")
        print("\n  NOT VALIDATED -- this score was measured on the same runs that")
        print("  selected it, so it is optimistically biased (#20). Re-run with")
        print("  --seeds >= 2 to hold replications out of the choice.")
    else:
        print(f"  selected on {', '.join(held.selection_seeds)} "
              f"-> accuracy={held.selection_score:.3f}   (not the number to quote)")
        print(f"  HELD-OUT    {', '.join(held.holdout_seeds)} "
              f"-> accuracy={held.holdout_score:.3f}   <- report this one")
        print(f"  optimism    {held.optimism:+.3f}   "
              f"held-out rank {held.holdout_rank} of {held.n_candidates}")
        if held.variant_id != champion.variant_id:
            print(f"  NOTE: the all-data winner is {champion.variant_id}, which is")
            print("  a different config -- the selection is not stable.")
        if not held.held_up:
            print("\n  THE WINNER DID NOT HOLD UP. It was picked on one set of")
            print("  replications and is not top on fresh ones, which is what a")
            print("  search fitting noise looks like. Do not ship this config on")
            print("  the strength of this run.")
        print(f"\n  quality={champion.quality:.0%}  "
              f"false alarms={champion.false_alarm_rate:.0%}  "
              f"propagation={champion.propagation_rate:.0%}")

    print(f"  exported to {dest}")
    print(f"  run it:  commonadk validate {dest}")
    return held


if __name__ == "__main__":
    sys.exit(main())
