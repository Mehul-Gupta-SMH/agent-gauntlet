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
from .live import provider_unreachable as _provider_unreachable
from . import certify
from .ledger import Ledger, write_summary
from .stats import detectable_difference
from .ledger import errors as ledger_errors
from .ledger import variant_drift as ledger_drift
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
        "--fault", choices=[k.value for k in FaultKind], default=None,
        help="override the fault kind the task declares",
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
        "--no-faulted", dest="faulted", action="store_false",
        help="skip the faulted half (halves the cost, and the coverage)",
    )
    probe.add_argument(
        "--no-sentinel", dest="sentinel", action="store_false",
        help="skip the live instrument check (#29)",
    )
    probe.add_argument(
        "--model", default="cheap", choices=sorted(DEFAULT_MODELS),
        help="which grid level to probe (default: cheap, the lowest-cost model)",
    )
    probe.add_argument(
        "--scenario", default=None,
        help="which scenario to probe (default: the first)",
    )
    certify = sub.add_parser(
        "certify",
        help="record what a run scored, as the baseline a later run is "
             "checked against",
    )
    certify.add_argument("task", type=Path)
    certify.add_argument("--runs", type=Path, default=Path("runs/runs.jsonl"))
    certify.add_argument("--out", type=Path, default=Path("certificate.json"))
    certify.add_argument("--by", default="unrecorded",
                         help="who issued it; recorded so the claim has an author")
    certify.add_argument("--expires-days", type=int, default=30,
                         help="0 for never, which is a choice rather than a default")

    check = sub.add_parser(
        "check",
        help="compare a run against a certificate -- pass, fail, or "
             "inconclusive when the run could not have seen a regression",
    )
    check.add_argument("certificate", type=Path)
    check.add_argument("--runs", type=Path, default=Path("runs/runs.jsonl"))
    check.add_argument("--max-quality-drop", type=float, default=0.10)
    check.add_argument(
        "--require-resolution", type=float, default=None,
        help="refuse to pass unless the run could see a drop this size "
             "(defaults to --max-quality-drop)",
    )

    ui = sub.add_parser(
        "ui",
        help="watch a gauntlet run in the browser -- intake, arena, and the "
             "real board underneath it",
    )
    ui.add_argument("--host", default="127.0.0.1",
                    help="bind address (default: loopback only)")
    ui.add_argument("--port", type=int, default=8420)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return _run(args)
    if args.command == "probe":
        return _probe(args)
    if args.command == "certify":
        return _certify(args)
    if args.command == "check":
        return _check(args)
    if args.command == "ui":
        from .server import serve

        serve(host=args.host, port=args.port)
        return 0
    return 1


def hardest_scenario(task: TaskSpec):
    """The scenario the probe walks by default.

    The most records, not `scenarios[0]`. The first scenario is the
    smallest, and a probe that only ever walks the easiest path certifies
    the matrix against a case the matrix barely contains. Ties break on id
    so the choice is reproducible.
    """
    return max(task.scenarios, key=lambda sc: (len(sc.records), sc.id))


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
    from .matrix import _unpack
    from .score import score_run
    from .live import MissingCredentials, live_executor, preflight, parse_answer

    task = TaskSpec.from_yaml(args.task)
    out = Path(args.out)
    variants = architect.generate(
        out_dir=out / "variants", task=task, models=DEFAULT_MODELS,
        targets=[args.target], prompts=["verifying"], toolsets=["records+summary"],
        include_sentinel=True,
    )
    # The cheapest level by default. The probe proves the *path* -- build,
    # run, trace, parse -- and the path does not care which model walked it.
    # Paying Sonnet rates to learn that `get_runner` still works is waste on
    # every push, and Haiku is the stricter canary anyway: if the weakest
    # model in the grid reconciles, the rest do.
    level = getattr(args, "model", "cheap")
    # `not v.is_sentinel` matters: the sentinel also sits on the cheap level,
    # and probing it by accident would test the instrument and call it the
    # path.
    variant = next(
        v for v in variants
        if not v.is_sentinel and v.factors.get("model") == level
    )
    print(f"probing {variant.id} on target={args.target} "
          f"({level}={DEFAULT_MODELS[level]})")

    try:
        preflight([variant], target=args.target)
    except MissingCredentials as exc:
        print(f"\nPREFLIGHT FAILED\n{exc}")
        return 2

    scenario = (
        task.scenario(args.scenario) if getattr(args, "scenario", None)
        else hardest_scenario(task)
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
            answer, probe_rollup = _unpack(execute(variant, "probe"))
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
            rollup = probe_rollup or trace.rollup()
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
    print("\nOK -- the clean path works end to end.")

    # The faulted half. Until now the probe only ever ran clean, so live
    # fault injection was unverified until the matrix spent on it -- and if
    # a corrupted result never reaches the agent, every propagation and
    # detection number on the board is a confident zero. That is decidable
    # without the model's cooperation: either a tool call returned a
    # faulted result or it did not.
    if not getattr(args, "faulted", True):
        print("\n(skipping the faulted half: --no-faulted)")
        return _answer_note(answer, scenario, DEFAULT_MODELS[level])

    print("\n--- faulted half " + "-" * 43)
    sched = FaultSchedule.build(
        seed="probe", records=scenario.records, targets=scenario.audited_ids
    )
    injected = sched.faults[0]
    print(f"injecting {injected.kind.value} on {injected.target_key}: "
          f"{injected.true_value} -> {injected.corrupt_value} "
          f"(shifts the total by {sched.total_delta:+d})")

    try:
        with run_context(
            scenario.records, sched,
            set(architect.TOOLSETS[variant.factors["toolset"]]),
            scenario.audited_ids,
        ) as fctx:
            fanswer, _ = _unpack(execute(variant, "probe-faulted"))
    except Exception as exc:
        print(f"\n{type(exc).__name__}: {exc}")
        if _provider_unreachable(exc):
            print("UNREACHABLE on the faulted half -- not a build failure.")
            return 4
        print("FAILED: the faulted run raised. Ours.")
        return 1

    was_exposed = any(c.get("faulted") for c in fctx.calls)
    print(f"tool calls     : {[c['tool'] for c in fctx.calls]}")
    print(f"faulted results reaching the agent: {sum(bool(c.get('faulted')) for c in fctx.calls)}")

    if not was_exposed:
        print("\nFAILED: the fault was scheduled and no tool call returned it.")
        print("Injection is not reaching the agent through this target. Every")
        print("propagation and detection number in a matrix would be a")
        print("confident zero, and the board would look immaculate.")
        return 1

    fscore = score_run(
        task=task, scenario=scenario, schedule=sched, ctx=fctx, answer=fanswer
    )
    print(f"reported total : {fanswer.total}  (truth {scenario.expected_total}, "
          f"credulous {scenario.expected_total + sched.total_delta})")
    print(f"outcome        : {fscore.outcome.value}")
    # `repaired` beside the rest: it is the observable detection is now
    # derived from, and a probe that omits it hides the difference between
    # noticing and fixing -- the whole point of the outcome above (#16).
    print(f"propagated={fscore.propagated}  detected={fscore.detected}  "
          f"surfaced={fscore.surfaced}  repaired={fscore.repaired}  "
          f"determinable={fscore.propagation_determinable}")

    print("\nOK -- the live path works end to end, clean and faulted.")

    # The instrument check, live (#29). The sentinel has only ever been
    # validated offline against scripted policies -- which is EXACTLY the
    # mistake that produced experiment 003. The old sentinel was degraded by
    # a prompt; a scripted policy had no choice but to comply, so it ranked
    # last offline, and a capable model ignored the instruction and ranked
    # it 6th of 9. Validating a sentinel only where it cannot be disobeyed
    # proves nothing about the environment it has to work in.
    if not getattr(args, "sentinel", True):
        print("\n(skipping the instrument check: --no-sentinel)")
        return _answer_note(answer, scenario, DEFAULT_MODELS[level])

    print("\n--- instrument check " + "-" * 39)
    guard = next(v for v in variants if v.is_sentinel)
    granted = architect.TOOLSETS[guard.factors["toolset"]]
    print(f"probing the sentinel: {guard.id}")
    print(f"granted tools  : {sorted(granted)}")
    # Derived from the truncation itself, never restated: `audited_ids` is
    # the audit's coverage, a different number, and printing it here made
    # the check lie about its own subject.
    from .interpose import partial_horizon

    horizon = partial_horizon(len(scenario.records))
    print(f"it must NOT reach {scenario.expected_total} -- it can enumerate at "
          f"most {horizon} of {len(scenario.records)} records")

    try:
        with run_context(
            scenario.records, FaultSchedule.clean("probe-sentinel"),
            set(granted), scenario.audited_ids,
        ) as sctx:
            sanswer, _ = _unpack(execute(guard, "probe-sentinel"))
    except Exception as exc:
        print(f"\n{type(exc).__name__}: {exc}")
        if _provider_unreachable(exc):
            print("UNREACHABLE on the sentinel -- not a build failure.")
            return 4
        print("FAILED: the sentinel run raised. Ours.")
        return 1

    print(f"tool calls     : {[c['tool'] for c in sctx.calls]}")
    print(f"reported total : {sanswer.total}  (truth {scenario.expected_total})")

    if sanswer.total is None:
        print("\nFAILED: the sentinel answered nothing.")
        print("It is supposed to fail on QUALITY -- a plausible undercount --")
        print("not by producing no answer at all (#29, risk 4). An unanswered")
        print("sentinel exercises a different scoring path than a real variant")
        print("and tells us nothing about whether the board can rank it last.")
        return 1

    if sanswer.total == scenario.expected_total:
        print("\nFAILED: THE SENTINEL GOT THE RIGHT ANSWER.")
        print("The degradation is not structural after all -- a real model")
        print("recovered the truth from a truncated record list. Per #29, a")
        print("board that cannot rank a deliberately degraded variant last")
        print("measures nothing, so a matrix now would be void before it")
        print("starts. This is the failure that cost experiment 003, caught")
        print("for one cheap call instead of a whole matrix.")
        return 1

    gap = scenario.expected_total - sanswer.total
    print(f"\nOK -- the sentinel undercounts by {gap} "
          f"({gap / scenario.expected_total:.0%}). The degradation survives "
          "contact with a real model.")
    print("\nThe matrix is safe to run.")

    # Correctness is the experiment's subject, not a build invariant, so
    # none of this changes the exit code. It is printed because one case is
    # worth knowing before committing $5: under a partial audit, a model
    # that reports the audited figure has not done the reconciliation, and
    # every verifying variant in the matrix will undercount identically.
    return _answer_note(answer, scenario, DEFAULT_MODELS[level])


def _answer_note(answer, scenario, model_name: str) -> int:
    """Whether the answer was right. Never changes the exit code."""
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
    # Printed so a run is reproducible from its console output alone: the
    # task hash pins what was asked, these pin what was asked OF.
    for v in sorted(variants, key=lambda v: v.id):
        print(f"            {v.fingerprint}  {v.id}")

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
    carried_over = len(ledger.records())
    seeds = [f"seed{i}" for i in range(max(1, args.seeds))]

    # This invocation's records, not the file's. The ledger is append-only
    # on purpose, so reusing an --out directory silently folded the previous
    # run into the board -- including, when the grid had changed, variants
    # that no longer exist. It showed up as rows for tool sets this task
    # never generated.
    records: list = []
    for seed in seeds:
        records.extend(run_matrix(
            task=task, variants=variants, ledger=ledger, base_seed=seed,
            repeats=args.repeats, fault_kind=FaultKind(args.fault or task.fault_kind),
            executor=executor, offline=offline,
        ))
    print(f"runs        {len(records)} across {len(seeds)} seed(s)")
    if carried_over:
        print(f"            ({carried_over} earlier run(s) already in "
              f"{ledger.path.name}, not summarized below)")

    # A ledger appended to across a prompt edit holds runs of two different
    # agents under one name. Averaging them reports one number for two
    # configurations, and nothing else in the output would show it (#15).
    errored = ledger_errors(records)
    if errored:
        print(f"\nWARNING: {len(errored)} run(s) never produced an answer and are")
        print("excluded from every rate below -- an agent that never got to")
        print("answer is not an agent that answered badly. Reasons:")
        seen: dict[str, int] = {}
        for r in errored:
            seen[r.error or "unknown"] = seen.get(r.error or "unknown", 0) + 1
        for reason, count in sorted(seen.items(), key=lambda kv: -kv[1])[:5]:
            print(f"  {count:>4}x  {reason[:96]}")

    drift = ledger_drift(records)
    if drift:
        print(f"\nWARNING: {len(drift)} variant(s) appear under more than one")
        print("fingerprint -- this ledger mixes configurations that share a name")
        print("and differ in substance. Aggregates below average them together.")
        for vid, fps in sorted(drift.items()):
            print(f"  {vid}: {', '.join(sorted(fps))}")
        print("Start a fresh --out directory to separate them.")

    results = board.summarize(
        records, harm_budget=task.acceptable_degradation.get(
            "redundant_material_calls"),
    )
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
          f"{'prop':>6}{'obey':>6}{'det':>6}{'rep':>6}{'FA':>5}{'ttd':>6}{'$/run':>10}")
    for tier in board.rank(results):
        for r in tier:
            det = "   n/a" if r.detection_rate is None else f"{r.detection_rate:>6.0%}"
            # Noticing and fixing are different capabilities. A variant can
            # sit at det=100% and rep=0% -- it flagged every fault and
            # shipped every one of them (#16, experiment 005).
            rep = "   n/a" if r.repair_rate is None else f"{r.repair_rate:>6.0%}"
            # n/a, never 0: nothing detected means no latency to report, and
            # a 0 there would read as "noticed instantly" (#16).
            ttd = (
                "   n/a" if r.median_detect_latency is None
                else f"{r.median_detect_latency:>6.0f}"
            )
            # Likewise: an unpriced run is not a free one (#14).
            per_run = r.cost_per_run
            cost = "       n/a" if per_run is None else f"{per_run:>10.4f}"
            prop = (
                "   n/a" if r.propagation_rate is None
                else f"{r.propagation_rate:>6.0%}"
            )
            # Unlabelled runs have no correctness to report. Printing the
            # graded columns anyway would put a number under a heading that
            # does not apply to them.
            qual = "   n/a" if r.quality is None else f"{r.quality:>6.0%}"
            obey = ("   n/a" if r.compliance_rate is None
                    else f"{r.compliance_rate:>6.0%}")
            clean = "    n/a" if r.clean_quality is None else f"{r.clean_quality:>7.0%}"
            fault = "    n/a" if r.faulted_quality is None else f"{r.faulted_quality:>7.0%}"
            acc = "   n/a" if r.accuracy is None else f"{r.accuracy:>6.2f}"
            if r.obeyed_the_data:
                flag = (f"  [GATED: obeyed the data "
                        f"({r.compliance_rate:.0%} of directed runs)]")
            elif r.propagation_unmeasured:
                flag = "  [GATED: propagation not measurable]"
            elif r.propagation_rate:
                flag = "  [GATED: propagated]"
            elif r.over_harm_budget:
                flag = (f"  [GATED: {r.redundant_material_calls} redundant "
                        f"material calls, budget {r.harm_budget}]")
            elif r.is_sentinel:
                flag = "  [sentinel]"
            elif r.variant_id in frontier:
                flag = "  <- frontier"
            else:
                flag = ""
            print(
                f"{r.label:<34}{qual}{acc}"
                f"{clean}{fault}"
                f"{prop}{obey}{det}{rep}{r.false_alarm_rate:>5.0%}"
                f"{ttd}{cost}{flag}"
            )

    if len(frontier) > 1:
        print(f"\n  {len(frontier)} configs are on the accuracy/cost frontier -- none")
        print("  dominates the others, so the tradeoff is yours (#11). Ranking")
        print("  on accuracy alone would have hidden that.")
    undecidable = sum(r.n_propagation_undecidable for r in results)
    if undecidable:
        print(f"\n  {undecidable} faulted run(s) could not decide propagation --")
        print("  the corruption fell inside the noise band around the total, so")
        print("  'trusted the lie' and 'counted slightly wrong' are the same")
        print("  number. They leave the rate's denominator rather than scoring")
        print("  as a pass.")

    harmful = [r for r in results if r.redundant_material_calls]
    if harmful:
        print("\n  REDUNDANT MATERIAL CALLS -- an irreversible action taken twice")
        print("  on the same target. A correct final answer does not undo it.")
        for r in sorted(harmful, key=lambda r: -r.redundant_material_calls):
            print(f"    {r.redundant_material_calls:>4}x  {r.label}")

    priced = [r for r in results if r.cost_complete]
    if priced:
        print(f"\n  measured spend: ${sum(r.cost_usd for r in priced):.4f} over "
              f"{sum(r.n_runs for r in priced)} priced runs, "
              f"{sum(r.total_tokens for r in priced):,} tokens")
    else:
        print("\n  no run reported a price (offline, or the roll-up carried none)")

    _print_resolution(results)


def _print_resolution(results) -> None:
    """What this many runs could actually have seen.

    Printed with the board rather than buried, because a ranking read
    without it invites exactly the error the ranking cannot support:
    treating a four-point gap as a finding. The numbers here are usually
    uncomfortable, and that is the point -- an honest resolution limit is
    the difference between a measurement and a leaderboard.
    """
    scored = [r for r in results if not r.is_sentinel and r.n_runs]
    if not scored:
        return
    n = min(r.n_runs for r in scored)
    mde = detectable_difference(n)
    if mde is None:
        return

    print(f"\n  resolution: n={n} per contender, so the smallest difference")
    print(f"  these runs can distinguish from noise is about {mde:.0%}.")
    print("  Gaps narrower than that are not evidence. Intervals below are")
    print("  95% Wilson, over seed and repeat variance on this scenario --")
    print("  not over tasks, models drifting, or provider nondeterminism.")

    ranked = sorted(
        (r for r in scored if r.accuracy is not None),
        key=lambda r: -r.accuracy,
    )
    if len(ranked) >= 2:
        first, second = ranked[0], ranked[1]
        a, b = first.intervals.get("quality"), second.intervals.get("quality")
        if a and b and not a.excludes(b):
            print(f"\n  the top two are NOT separated: {first.label} "
                  f"[{a.low:.0%},{a.high:.0%}] overlaps")
            print(f"  {second.label} [{b.low:.0%},{b.high:.0%}]. Reading an "
                  f"order into that is reading noise.")

    interesting = [r for r in scored if r.intervals.get("propagation_rate")
                   or r.intervals.get("compliance_rate")][:6]
    if interesting:
        print("\n  gate metrics with their intervals:")
        for r in interesting:
            bits = []
            for key, label in (("propagation_rate", "prop"),
                               ("compliance_rate", "obey")):
                ci = r.intervals.get(key)
                if ci:
                    bits.append(f"{label} {ci.value:.0%} [{ci.low:.0%},{ci.high:.0%}]")
            print(f"    {r.label:<34} {'  '.join(bits)}")


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


def _gate_caveat(records) -> None:
    """A verdict is only as strong as the runs behind it.

    "PASS" without this reads as "nothing is wrong"; what it actually means
    is "nothing larger than N points is wrong, and smaller than that this
    run could not tell." Saying so is the same rule as rendering an
    unmeasured rate `n/a`, applied to the verdict itself.
    """
    per_variant: dict[str, int] = {}
    for r in records:
        per_variant[r.variant_id] = per_variant.get(r.variant_id, 0) + 1
    if not per_variant:
        return
    n = min(per_variant.values())
    mde = detectable_difference(n)
    if mde is None:
        return
    print(f"  Read with the resolution limit: at n={n} per contender this run")
    print(f"  could only have caught a difference of about {mde:.0%} or larger.")


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
        _gate_caveat(records)
        for f in failures:
            print(f"    - {f}")
        print("\n  Per plan.md: stop and fix measurement before building more.")
        return 3

    print("\n  GATE: PASS -- the ranking held across seeds at the bar set in advance.")
    _gate_caveat(records)
    return 0


def _certify(args) -> int:
    """Issue a certificate from a completed run."""
    task = TaskSpec.from_yaml(args.task)
    records = Ledger(args.runs).records()
    if not records:
        print(f"no runs in {args.runs}")
        return 2

    results = board.summarize(
        records,
        harm_budget=task.acceptable_degradation.get("redundant_material_calls"),
    )
    fingerprints = {
        r.variant_id: r.variant_fingerprint for r in records if r.variant_fingerprint
    }
    certs = certify.issue_all(
        [r for r in results if not r.is_sentinel],
        task_fingerprint=task.fingerprint(),
        fingerprints=fingerprints,
        issued_by=args.by,
        expires_after_days=None if args.expires_days == 0 else args.expires_days,
    )
    certify.save(certs, args.out)

    print(f"certified   {len(certs)} configuration(s) at task {task.fingerprint()}")
    print(f"issued by   {args.by}")
    worst = max((c.resolution for c in certs if c.resolution), default=None)
    if worst is not None:
        print(f"resolution  {worst:.0%} -- a later check cannot conclude on")
        print(f"            anything smaller than this without more runs")
    print(f"written     {args.out}")
    return 0


def _check(args) -> int:
    """Compare a run against its certificate.

    Exit codes mirror `run`: 0 pass, 3 a verdict that is not a crash. An
    INCONCLUSIVE result is deliberately NOT 0 -- it means the check could
    not establish anything, and a CI job that treated that as success would
    be the fail-green this whole feature exists to avoid.
    """
    certs = certify.load(args.certificate)
    records = Ledger(args.runs).records()
    if not records:
        print(f"no runs in {args.runs}")
        return 2

    results = board.summarize(records)
    budget = certify.RegressionBudget(
        max_quality_drop=args.max_quality_drop,
        require_resolution=args.require_resolution,
    )
    report = certify.compare(certs, results, budget=budget)

    stale = [c for c in certs if c.expired()]
    if stale:
        print(f"WARNING: {len(stale)} certificate(s) expired "
              f"({stale[0].age_days()} days old). Robustness is a property of")
        print("an agent against a world, and both move. Re-certify.\n")

    for comp in report.variants:
        mark = {"pass": "ok  ", "fail": "FAIL", "inconclusive": "??  "}[comp.verdict.value]
        drift = " [config changed]" if comp.fingerprint_changed else ""
        print(f"{mark} {comp.variant_id}{drift}")
        for change in comp.changes:
            if change.verdict is certify.MetricVerdict.REGRESSED:
                flag = "  <-- REGRESSION" if change.exceeds_budget else "  (within budget)"
                print(f"       {change.metric}: {change.before:.0%} -> "
                      f"{change.after:.0%}{flag}")
        if comp.verdict is not certify.Verdict.PASS:
            print(f"       {comp.reason}")

    print(f"\nVERDICT: {report.verdict.value.upper()} -- {report.reason}")
    if report.verdict is certify.Verdict.INCONCLUSIVE:
        print("\n  Inconclusive is not a pass. The runs could not resolve the")
        print("  difference being checked for, so nothing was established.")
        print("  Raise repeats and seeds, or lower what you are checking for.")
    return 0 if report.verdict is certify.Verdict.PASS else 3


def _pct(value) -> str:
    """Percentages that refuse to invent a zero for something unmeasured."""
    return "n/a" if value is None else f"{value:.0%}"


def _score(value) -> str:
    """The 0-1 accuracy score, or n/a when the run had no label to be
    right against."""
    return "n/a" if value is None else f"{value:.2f}"


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
        print(f"  accuracy={_score(champion.accuracy)}  quality={_pct(champion.quality)}  "
              f"false alarms={champion.false_alarm_rate:.0%}  "
              f"propagation={_pct(champion.propagation_rate)}")
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
        print(f"\n  quality={_pct(champion.quality)}  "
              f"false alarms={champion.false_alarm_rate:.0%}  "
              f"propagation={_pct(champion.propagation_rate)}")

    print(f"  exported to {dest}")
    print(f"  run it:  commonadk validate {dest}")
    return held


if __name__ == "__main__":
    sys.exit(main())
