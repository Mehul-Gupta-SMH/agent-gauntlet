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
from contextlib import ExitStack
from pathlib import Path
from typing import Optional, Sequence

from . import architect, attribution, board, replay
from .analyze import stability
from .faults import FaultKind
from .score import Outcome
from .live import provider_unreachable as _provider_unreachable
from . import certify
from .ledger import Ledger, write_summary
from .stats import detectable_difference
from .ledger import duplicate_cells as ledger_duplicates
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
    run.add_argument(
        "--repeats", type=int, default=3,
        help="k per (variant, scenario). The default is a starting point, not "
             "a measured recommendation: experiment 009 found the k a stable "
             "board needs tracks the separation between adjacent "
             "configurations relative to their noise, which is not knowable "
             "before the run. Sweep it on your own task",
    )
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
    run.add_argument(
        "--max-cost-per-run", type=float, default=None, metavar="USD",
        help="also answer the question an operator actually arrives with: "
             "the best config that costs no more than this per run. A "
             "constraint, not a preference -- the Pareto frontier stays",
    )
    run.add_argument(
        "--replay", type=Path, default=None, metavar="PATH",
        help="also record this run's event stream to a JSON file the arena "
             "page can replay without a server. Captured from the run that "
             "actually happens -- there is no way to write one without "
             "running the matrix",
    )
    run.add_argument(
        "--with-relations", action="store_true",
        help="also check the task's metamorphic relations and put them on "
             "the board. OFF by default because a relation is a second full "
             "run of every variant against a perturbed world: k relations "
             "roughly multiply the matrix by k+1 (#43). Without it the "
             "relation cells read n/a, never ok",
    )
    canary = sub.add_parser(
        "canary",
        help="a tiny pinned probe of one config, to notice the world moving "
             "under it (#19)",
    )
    canary.add_argument("task", type=Path)
    canary.add_argument("--out", type=Path, default=Path("runs-canary"))
    canary.add_argument("--prompt", default="verifying")
    canary.add_argument("--toolset", default="records+summary")
    canary.add_argument("--model", default="smart", choices=sorted(DEFAULT_MODELS))
    canary.add_argument(
        "--repeats", type=int, default=8,
        help="how many pairs per scenario. Cheap on purpose -- and the "
             "report says what a canary this cheap is blind to rather than "
             "printing a green tick",
    )
    canary.add_argument(
        "--seed", default="canary",
        help="pinned, so the next take re-runs the same cells. A canary "
             "drawn against fresh seeds moves for two reasons at once",
    )
    canary.add_argument("--baseline", type=Path, default=None,
                        help="a canary file to compare against")
    canary.add_argument("--save", type=Path, default=None,
                        help="write this take to a canary file")
    canary.add_argument(
        "--budget", type=float, default=0.10,
        help="the quality drop you would want a regression check to catch. "
             "Used only to say whether a canary this size could have caught "
             "it",
    )
    canary.add_argument("--live", action="store_true")
    canary.add_argument("--target", default="langgraph")

    probe = sub.add_parser(
        "probe",
        help="one live run, printing the raw reply -- proves the path before a matrix",
    )
    probe.add_argument("task", type=Path)
    probe.add_argument("--out", type=Path, default=Path("runs-probe"))
    probe.add_argument("--target", default="langgraph")
    probe.add_argument(
        "--fault", choices=[k.value for k in FaultKind], default=None,
        help="override the fault kind the task declares",
    )
    probe.add_argument(
        "--shape", default=None,
        choices=["authority", "urgency", "correction", "flattery", "control"],
        help="which directive phrasing to inject, for an `instruction` "
             "task. One live run per shape is how the family gets walked "
             "against a real model",
    )
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

    check.add_argument(
        "--safety-only", action="store_true",
        help="fail only on the binary safety metrics (propagation, "
             "compliance) and report quality regressions without gating "
             "on them. The shape for a merge queue: a gate that blocks at "
             "random gets switched off, and then nothing is gated",
    )
    rel = sub.add_parser(
        "relations",
        help="check metamorphic relations -- objective scoring for a task "
             "with no known answer",
    )
    rel.add_argument("task", type=Path, help="path to a task spec YAML")
    rel.add_argument("--out", type=Path, default=Path("runs-relations"))
    rel.add_argument("--repeats", type=int, default=3,
                     help="pairs per relation; a relation must hold in all "
                          "of them")
    rel.add_argument(
        "--models", nargs="*", metavar="ALIAS=MODEL",
        help="model grid, as for `run`",
    )
    ui = sub.add_parser(
        "ui",
        help="watch a gauntlet run in the browser -- intake, arena, and the "
             "real board underneath it",
    )
    ui.add_argument("--host", default="127.0.0.1",
                    help="bind address (default: loopback only)")
    ui.add_argument("--port", type=int, default=8420)
    ui.add_argument(
        "--allow-code-execution", action="store_true",
        help="permit tool upload and credential entry. Uploaded code is "
             "imported and called IN THIS PROCESS with access to everything "
             "it has, including provider keys. Off by default: a loopback "
             "bind no longer implies privacy, because a tunnel forwards to "
             "loopback too.",
    )
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
    if args.command == "relations":
        return _relations(args)
    if args.command == "canary":
        return _canary(args)
    if args.command == "ui":
        from .server import serve

        serve(host=args.host, port=args.port,
              allow_code_execution=args.allow_code_execution)
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

    # Before generation, before preflight, before a single token. `--shape`
    # is inert on any task whose fault kind is not `instruction`, and
    # silently so: the probe would inject a different fault, report
    # success, and look exactly like the run that was asked for. Five live
    # dispatches went that way before this existed, which is the cost of
    # letting a flag mean nothing.
    asked_shape = getattr(args, "shape", None)
    asked_kind = FaultKind(getattr(args, "fault", None) or task.fault_kind)
    if asked_shape is not None and asked_kind is not FaultKind.INSTRUCTION:
        print("CANNOT WALK THE DIRECTIVE FAMILY ON THIS TASK")
        print(f"--shape {asked_shape} only means something where the fault "
              f"kind is 'instruction'.")
        print(f"{args.task} declares {asked_kind.value!r}, so no directive "
              f"would be injected and the")
        print("run would measure something else while reporting success.")
        print("\nProbe a task that carries one:")
        print(f"  gauntlet probe fixtures/poisoning/instruction.yaml "
              f"--shape {asked_shape}")
        return 2

    out = Path(args.out)
    # The task's own grid when it declares one. Hardcoding
    # `records+summary` meant the poisoning fixtures could not be probed at
    # all -- that tool set grants no `read_annotation`, so the instrument
    # check refused before a single token was spent, and the live half of
    # the compliance claim was unrunnable for reasons that had nothing to
    # do with money (#37).
    prompts = ["verifying"]
    toolsets = ["records+summary"]
    if task.grid is not None:
        # The first that can actually carry this task's fault: probing a
        # variant that cannot see it would test nothing.
        reaching = [t for t in task.grid.toolsets
                    if task.fault_tool in architect.TOOLSETS[t]]
        if reaching:
            toolsets = reaching[:1]
            prompts = task.grid.prompts[:1]
    try:
        variants = architect.generate(
            out_dir=out / "variants", task=task, models=DEFAULT_MODELS,
            targets=[args.target], prompts=prompts, toolsets=toolsets,
            include_sentinel=True,
        )
    except ValueError as exc:
        # A misconfigured probe, not a broken live path. Exit 2 is the
        # "your setup is wrong" code; letting it crash would have CI print
        # "the live path is broken. Do NOT run the matrix", which sends the
        # reader to look at the provider.
        print(f"\nCANNOT PROBE THIS TASK\n{exc}")
        return 2
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
    from .faults import DirectiveShape

    kind = FaultKind(getattr(args, "fault", None) or task.fault_kind)
    shape = DirectiveShape(asked_shape or "authority")
    sched = FaultSchedule.build(
        seed="probe", records=scenario.records, targets=scenario.audited_ids,
        kind=kind, tool_name=task.fault_tool, shape=shape,
    )
    injected = sched.faults[0]
    if injected.kind is FaultKind.INSTRUCTION:
        # The directive is the payload, so print it: a live compliance
        # result nobody can read the prompt of is not reproducible.
        print(f"injecting {injected.kind.value} ({shape.value}) on "
              f"{injected.tool_name}({injected.target_key})")
        print(f"canary         : {injected.canary}"
              if injected.canary is not None
              else "canary         : none (the control asks for nothing)")
        print(f"directive      : {injected.directive}")
    else:
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
        called = [c["tool"] for c in fctx.calls]
        # An agent that called NOTHING is not a task-design problem: it is
        # not using the tool surface at all, and a matrix of those scores
        # propagation 0% across the board. That stays the loud failure.
        # Calling tools but not the corrupted one is the other thing
        # entirely, and the two printed the same sentence.
        if called and task.fault_tool not in called:
            # Two very different failures used to print the same sentence.
            # The agent never CALLED the corrupted tool, which says nothing
            # about the adapter and everything about the task: nothing in
            # the statement gave it a reason to. Reporting that as "the
            # live path is broken" sends the reader to look at the SDK for
            # a problem in a YAML file. Observed on the first live run of
            # `poisoning/instruction.yaml`: haiku listed the records,
            # fetched all six, cross-checked the audit and answered,
            # without ever reading an annotation.
            print(f"\nTHE AGENT NEVER CONSULTED {task.fault_tool!r}")
            print(f"it called: {', '.join(dict.fromkeys(called)) or '(nothing)'}")
            print()
            print("The fault was scheduled correctly and the variant holds")
            print("the tool. The agent simply had no reason to call it, so")
            print("the directive was never put in front of it -- every")
            print("compliance number for this task would be n/a, which is")
            print("honest and useless.")
            print()
            print("This is the task's problem, not the adapter's. The")
            print("statement has to give the agent a reason to read what the")
            print("fault sits in, without telling it to obey what it finds.")
            return 2
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

    if fscore.directive_seen:
        # The headline for an instruction probe. Decided by comparison
        # against a value the agent had no other route to -- no judge, no
        # reading of intent.
        print(f"directive shown: {fscore.directive_shape}")
        if not fscore.compliance_decidable:
            print("compliance     : n/a -- the control asks for nothing, so "
                  "there is no observable act of obedience")
        elif fscore.complied:
            print(f"compliance     : OBEYED -- reported the canary "
                  f"({injected.canary}) exactly")
        else:
            print("compliance     : held -- the answer is not the canary")

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
    with ExitStack() as stack:
        # Attached only when asked. With nothing listening, `events.emit` is
        # an attribute lookup and a return, so an ordinary run is untouched
        # by the existence of this feature.
        log = stack.enter_context(replay.capture()) if args.replay else None
        if log is not None:
            log.emit("intake.accepted", **replay.intake_event(
                task, variants, seeds=len(seeds), repeats=args.repeats))
        for seed in seeds:
            records.extend(run_matrix(
                task=task, variants=variants, ledger=ledger, base_seed=seed,
                repeats=args.repeats,
                fault_kind=FaultKind(args.fault or task.fault_kind),
                executor=executor, offline=offline,
            ))
        if log is not None:
            log.emit("board", **replay.board_event(records, seeds=len(seeds)))
            written = replay.write(args.replay, replay.document(
                log,
                title=task.id,
                note=_replay_note(task, records, seeds, args, offline),
                source=_replay_source(args),
            ))
            print(f"replay      {written} "
                  f"({len(log.since(0))} events, captured live)")
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

    from .pricing import drift as price_drift

    priced = price_drift(records)
    if len(priced) > 1:
        print(f"\nWARNING: this ledger holds runs priced against "
              f"{len(priced)} different rate")
        print("tables. Each dollar figure is truthful about its own run and "
              "none of them")
        print("are comparable, so the cost column reads n/a for any variant "
              "that spans")
        print("more than one (#14).")
        for fp, variants in priced.items():
            print(f"  {fp}: {len(variants)} variant(s)")
    elif priced:
        (only,) = priced
        print(f"prices      {only}  (rates pinned at run time; there is no "
              f"upstream date to show)")

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
    _print_search_cost(results, len(seeds), args)
    if args.max_cost_per_run is not None:
        _print_under_budget(results, args.max_cost_per_run)
    _print_hypotheses(task, records, variants)
    _print_relations(task, variants, executor,
                     enabled=args.with_relations, live=args.live)
    _print_effects(results)
    _print_attribution(records, [v.id for v in variants if v.is_sentinel])
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


def _replay_note(task, records, seeds, args, offline: bool) -> str:
    """The caption the page shows above a recording.

    A replay is evidence put in front of someone who cannot re-run it, so
    it carries how it was produced -- and, for an offline capture, says
    plainly that it is scripted policies rather than models. A demo that
    let a viewer assume they were watching Sonnet would be the same sin as
    a mocked screenshot.
    """
    how = ("scripted offline policies -- no model was called and nothing "
           "was spent" if offline else
           f"live models via target={args.target}")
    return (f"{len(records)} real runs over {len(seeds)} seed(s), "
            f"{args.repeats} repeat(s) per cell, {how}. "
            f"Task fingerprint {task.fingerprint()}.")


def _replay_source(args) -> str:
    """The command that produced it, so the claim is checkable."""
    parts = ["gauntlet", "run", str(args.task),
             f"--repeats {args.repeats}", f"--seeds {args.seeds}"]
    if args.fault:
        parts.append(f"--fault {args.fault}")
    if args.live:
        parts.append(f"--live --target {args.target}")
    parts.append(f"--replay {args.replay}")
    return " ".join(parts)


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
    # The board is ordered by accuracy, so the rank column has to be too --
    # a `#` derived from one metric beside rows sorted by another would be
    # the most confusing possible version of this.
    labels = board.rank_labels(results, metric="accuracy")
    depth = board.decidable_depth(results, metric="accuracy")
    print(f"{'#':<5}{'variant':<34}{'qual':>6}{'acc':>6}{'clean':>7}{'fault':>7}"
          f"{'prop':>6}{'obey':>6}{'det':>6}{'rep':>6}{'FA':>5}{'ttd':>6}"
          f"{'work':>7}{'$/run':>10}")
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
            # What surviving cost in work. An agent that resists every
            # fault by tripling its tool calls is robust and expensive, and
            # the board could previously say only the first half (#37).
            work = ("    n/a" if r.effort_ratio is None
                    else f"{r.effort_ratio:>6.2f}x")
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
            # `3=` on three rows: somewhere in positions 3 to 5, and this
            # run cannot say which. Printing 3, 4, 5 would be the board
            # asserting an order nobody measured -- the same censoring rule
            # as an unmeasured rate, applied to the rank column (#44).
            print(
                f"{labels.get(r.variant_id, ''):<5}{r.label:<34}{qual}{acc}"
                f"{clean}{fault}"
                f"{prop}{obey}{det}{rep}{r.false_alarm_rate:>5.0%}"
                f"{ttd}{work}{cost}{flag}"
            )

    if depth is not None:
        total = sum(1 for r in results if not r.is_sentinel)
        if depth == 0:
            print("\n  ORDER: none. This run could not separate even the top two")
            print("  configs, so every row below is in arbitrary order. The")
            print("  numbers are real; the ranking is not.")
        elif depth < total:
            print(f"\n  ORDER HOLDS TO ROW {depth} of {total}. Below that the")
            print("  intervals overlap: rows sharing a `=` rank were not")
            print("  separated by this run, and their printed order is arbitrary.")
            print("  More runs may resolve them -- or they may be tied in truth,")
            print("  which no budget fixes (experiment 009).")
        else:
            print(f"\n  ORDER HOLDS TO ROW {depth} of {total} -- every adjacent")
            print("  pair was separated by its interval.")

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

    # Reported beside the column, because a ratio whose interval spans 1.0
    # is not evidence that anything got more expensive.
    costly = [
        r for r in results
        if r.effort_ratio and r.effort_ratio > 1.0
        and "effort_ratio" in r.intervals and r.intervals["effort_ratio"].low > 1.0
    ]
    if costly:
        print("\n  surviving the fault cost real work (interval excludes 1.0):")
        for r in costly:
            iv = r.intervals["effort_ratio"]
            print(f"    {r.label:<34}{iv.value:>5.2f}x  "
                  f"[{iv.low:.2f}, {iv.high:.2f}]  "
                  f"{r.clean_steps:.1f} -> {r.faulted_steps:.1f} steps")

    # How badly a config fails when it fails, beside how often. Two
    # configs with the same propagation rate -- one silent, one shipping
    # the lie with a warning attached -- read identically above (#37).
    worst = [r for r in results if r.worst_outcome
             and Outcome(r.worst_outcome).severity >= 3]
    if worst:
        print("\n--- how they fail, not just how often " + "-" * 34)
        for r in worst:
            counts = ", ".join(f"{n}x {o}" for o, n in r.outcome_counts.items()
                               if Outcome(o).severity >= 3)
            print(f"  {r.label:<34}{counts}")
        print("\n  Severity is an ordering, not a score, and it is not a")
        print("  ranking key: 'shipped the lie with a warning attached' and")
        print("  'took orders from its data' are equally unshippable, and a")
        print("  number claiming to say how much worse one is would be")
        print("  precision nobody here has measured.")

    directed = [r for r in results if r.n_directives]
    if directed:
        shapes = sorted({s for r in directed for s in r.n_by_shape})
        print("\n--- compliance by directive shape " + "-" * 38)
        print("  One phrasing measures susceptibility to one phrasing. The")
        print("  spread is where a hole shows: 0% on three shapes and 80% on")
        print("  the fourth has the same mean as 20% everywhere, and is not")
        print("  the same config.")
        head = "".join(f"{s[:10]:>11}" for s in shapes)
        print(f"\n  {'variant':<34}{head}{'spread':>8}{'control':>9}")
        for r in directed:
            cells = "".join(
                (f"{r.compliance_by_shape[s]:>10.0%} " if r.n_by_shape.get(s)
                 else f"{'n/a':>10} ")
                for s in shapes
            )
            spread = ("     n/a" if r.compliance_spread is None
                      else f"{r.compliance_spread:>8.0%}")
            # The null, beside the family: quality on directive-shaped text
            # that asked for nothing. A drop here is degradation caused by
            # the APPEARANCE of an instruction, not by obeying one.
            ctl = ("      n/a" if r.control_quality is None
                   else f"{r.control_quality:>9.0%}")
            print(f"  {r.label:<34}{cells}{spread}{ctl}")
        thin = [s for s in shapes
                if any(0 < r.n_by_shape.get(s, 0) < 2 for r in directed)]
        if thin:
            print(f"\n  {', '.join(thin)}: fewer than 2 runs per variant. A rate")
            print("  over one run is not a rate -- raise --repeats to walk more")
            print("  of the family.")
        # `shapes` holds the ASKING shapes only -- the control has no
        # compliance rate to appear under. Its coverage is its own count.
        missing = [s for s in ("authority", "urgency", "correction", "flattery")
                   if s not in shapes]
        if not any(r.n_control for r in directed):
            missing.append("control (the null)")
        if missing:
            print(f"\n  not covered at this repeat count: {', '.join(missing)}.")
            print("  Shapes rotate with --repeats; 5 walks the whole family.")

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

    # Every pair, not just the top one. The held-out split (#20) polices
    # the winner; people read the whole column, and an ordering the run
    # cannot support anywhere below the top is still an ordering somebody
    # will act on (#36).
    tiers = board.evidence_tiers(results)
    if tiers:
        print("\n  what this run can actually separate:")
        for i, tier in enumerate(tiers, 1):
            members = sorted(tier, key=lambda r: -(r.quality or 0))
            head = members[0].intervals["quality"]
            if len(members) == 1:
                print(f"    {i}. {members[0].label}  "
                      f"[{head.low:.0%},{head.high:.0%}]")
                continue
            print(f"    {i}. these {len(members)} are NOT separated "
                  f"from each other:")
            for r in members:
                ci = r.intervals["quality"]
                print(f"         {r.label:<34}{ci.value:>5.0%} "
                      f"[{ci.low:.0%},{ci.high:.0%}]")
        if any(len(t) > 1 for t in tiers):
            print("    Within a tier the order on the board is noise. A tier")
            print("    means this run could not tell them apart -- never that")
            print("    they are the same.")

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


def _print_search_cost(results, seeds: int, args) -> None:
    """What finding the answer cost, beside what running it will (#36).

    Every contender's `$/run` was on the board and the price of the
    *search* was nowhere, which makes the escalation ladder -- offline,
    then probe, then matrix -- a claim rather than a budget.
    """
    cost = board.search_cost(results)
    print("\n--- what this search cost " + "-" * 46)
    print(f"  {cost['runs']} runs across {cost['variants']} contenders, "
          f"{seeds} seed(s), {args.repeats} repeat(s)")
    if not cost["usd"]:
        # Nothing priced it, which offline means nothing was spent and
        # live means the roll-up carried no price. Those are different
        # facts and must not print the same confident $0.00.
        print("  spend: none" if not args.live else
              "  spend: NOT MEASURED -- the provider returned no price")
    elif cost["complete"]:
        print(f"  spend: ${cost['usd']:.4f} to find the answer")
    else:
        print(f"  spend: at least ${cost['usd']:.4f} -- some runs went "
              f"unpriced, so this is a floor")
    if not args.live:
        print("  Offline is the first rung of the ladder: it exercises the")
        print("  whole pipeline and proves nothing about agents. `probe` is")
        print("  next (~$0.03), then a live matrix.")


def _print_under_budget(results, ceiling: float) -> None:
    """The constrained answer, next to the unconstrained frontier."""
    pick, unpriced = board.best_under(results, ceiling)
    print(f"\n--- best config at or under ${ceiling:.4f}/run " + "-" * 31)
    if pick is None:
        print("  nothing priced came in under the ceiling.")
    else:
        ci = pick.intervals.get("quality")
        qual = "n/a" if pick.quality is None else f"{pick.quality:.0%}"
        band = f" [{ci.low:.0%},{ci.high:.0%}]" if ci else ""
        print(f"  {pick.label}   {qual}{band}   "
              f"${pick.cost_per_run:.4f}/run")
        print("  Cheapest of the configs this run could not tell apart at the")
        print("  top -- picking between them on quality would be reading")
        print("  noise, so the tiebreak is the constraint you actually set.")
    if unpriced:
        # An unpriced run is not a free one (#14). They cannot be admitted
        # under a ceiling, and dropping them silently would hide the fact
        # that the cheapest option might be among them.
        print(f"\n  {len(unpriced)} config(s) carried no price and were not")
        print("  considered: unpriced is not free, and admitting them would")
        print("  be the cheapest possible answer for the wrong reason.")


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

    # Not a footnote: a factor whose effect swings more across the other
    # factors than the marginal number itself is a row that does not
    # describe any configuration that exists. Experiment 005 is the live
    # case -- the model axis moved repair 22% -> 100% with prompt and
    # toolset fixed, while this table called it 10 points.
    unstable = [e for e in effects
                if e.interaction_range is not None
                and e.interaction_range >= e.spread]
    if unstable:
        print("\n  DO NOT READ THE ROWS ABOVE FOR:",
              ", ".join(e.factor for e in unstable))
        for e in unstable:
            lo = min(e.conditional_spreads.items(), key=lambda kv: kv[1])
            hi = max(e.conditional_spreads.items(), key=lambda kv: kv[1])
            print(f"    {e.factor}: marginal {e.spread:.0%}, but holding the "
                  f"others fixed it ranges")
            print(f"      {lo[1]:.0%} at {lo[0]}")
            print(f"      {hi[1]:.0%} at {hi[0]}")
        print("    The average is not a description of any config you could")
        print("    ship. Pick the cell you actually intend to run.")


def _miss(bound) -> float:
    """How badly a falsified clause missed, as the share of runs that broke
    it.

    Works for a floor and a ceiling alike, which the observed rate does
    not: 20% is terrible against `>= 90%` and fine against `<= 50%`. It
    orders the list so the config whose runs get named is the worst one.
    """
    return (bound.n_offending / bound.n) if bound.n else 0.0


def _print_hypotheses(task, records, variants) -> None:
    """Falsifiable bounds, per config, unaggregated (#17).

    The whole point is that the output is a broken promise with the
    offending runs named, rather than a robustness scalar. So the
    hypothesis is printed in the operator's own words and the verdict sits
    under it, and nothing here adds the verdicts up -- #17's open question
    about ranking a config that fails one and passes six is answered by
    refusing to collapse the profile, the same answer #43 gives relations.
    """
    from . import hypotheses as hyp

    if not task.hypotheses:
        return
    print("\n--- steady-state hypotheses " + "-" * 44)
    print(f"  {len(task.hypotheses)} pre-registered with the task "
          f"({task.fingerprint()}), so a bound moved after the fact")
    print("  changes the hash every run record carries.\n")

    label = {v.id: " ".join(str(v.factors[k]) for k in sorted(v.factors))
             or v.id for v in variants}
    results = hyp.check_all(task.hypotheses, records)
    by_hypothesis: dict[str, list] = {}
    for r in results:
        by_hypothesis.setdefault(r.hypothesis_id, []).append(r)

    for h in task.hypotheses:
        rows = by_hypothesis.get(h.id, [])
        print(f"  {h.id}")
        for line in h.says.strip().splitlines():
            print(f"    {line.strip()}")
        upheld = [r for r in rows if r.verdict is hyp.Verdict.UPHELD]
        broken = [r for r in rows if r.verdict is hyp.Verdict.FALSIFIED]
        untested = [r for r in rows if r.verdict is hyp.Verdict.NOT_TESTED]
        print(f"\n    upheld {len(upheld)}   FALSIFIED {len(broken)}   "
              f"not tested {len(untested)}   of {len(rows)} config(s)")

        # One line per config, worst first -- the profile. Fourteen
        # identically-shaped findings printed with their run ids is a
        # number again, so exactly one of them keeps a path into the data.
        pairs = sorted(
            ((r, b) for r in broken for b in r.falsified_by),
            key=lambda rb: -_miss(rb[1]),
        )
        for i, (r, b) in enumerate(pairs):
            where = f"{b.n_offending} of {b.n}" if b.n_offending else f"n={b.n}"
            print(f"      FALSIFIED  "
                  f"{label.get(r.variant_id, r.variant_id)[:34]:<34} "
                  f"{b.observed:>5.0%}  ({where} runs)   {b.says}")
            # Ids under the worst offender only, where they are a path into
            # the data rather than another wall of identifiers.
            if i == 0 and b.offending:
                more = (f" +{b.n_offending - len(b.offending)} more"
                        if b.n_offending > len(b.offending) else "")
                print(f"                 read one: "
                      f"{', '.join(b.offending)}{more}")

        # Upholding a zero-bound is the one that most wants qualifying:
        # "never, in n runs" is not "never", and the ceiling says how much
        # room is left under it.
        ceilings = [(r, b) for r in upheld for b in r.bounds
                    if b.ceiling is not None]
        if ceilings:
            worst = max(ceilings, key=lambda rb: rb[1].ceiling)
            print(f"      Upheld at zero in {worst[1].n} run(s) -- which bounds "
                  f"the true rate at {worst[1].ceiling:.0%},")
            print("      not at zero. A bound of exactly zero can be falsified "
                  "by one run")
            print("      and confirmed by none.")

        thin = [(r, b) for r in upheld for b in r.bounds if b.too_close_to_call]
        if thin:
            names = sorted({label.get(r.variant_id, r.variant_id)
                            for r, _ in thin})
            print("      Upheld by a margin smaller than these runs could "
                  "resolve, so read")
            print("      them as unproven rather than as passes:")
            for n in names[:4]:
                print(f"        {n}")
            if len(names) > 4:
                print(f"        (+{len(names) - 4} more)")

        if untested:
            print(f"      Not tested (no run could have falsified it): "
                  f"{len(untested)} config(s).")
        print()

    print("  Not summed. A config that breaks one of these and keeps two is")
    print("  not comparable to one with the reverse profile, and a score")
    print("  over them would undo the reason for stating them.")


def _print_relations(task, variants, executor, *, enabled: bool,
                     live: bool) -> None:
    """The oracle-free check, on the board rather than in its own command
    (#43).

    Per relation, never averaged. "7/9" across relations that measure
    different properties is the composite-scalar mistake again: a config
    that fails `relabel` and one that fails `scale` are not
    interchangeable, and one number says they are. Which relation broke is
    the finding.

    Not gated. A relation is a binary property, so #27's rule makes it
    gate-shaped rather than report-shaped -- which is a stronger claim than
    putting it in a column, and #43 is explicit that it deserves to be made
    separately. The block says so instead of quietly deciding.
    """
    from . import metamorphic

    chosen = metamorphic.relations_for(task)
    print("\n--- metamorphic relations " + "-" * 46)
    if not chosen:
        print("  none declared by this task.")
        return

    names = [r.name for r in chosen]
    if not enabled:
        # n/a, never ok: a relation nobody ran is not a relation that held,
        # and this is the obvious place that distinction would be broken.
        print(f"  {', '.join(names)}: n/a for every config -- not run.")
        print(f"  --with-relations runs them, at roughly {len(chosen) + 1} "
              f"clean runs per config")
        print(f"  ({(len(chosen) + 1) * len(variants)} extra runs on this "
              f"grid" + (", which costs money on a live target)." if live
                        else ")."))
        return

    print(f"  {len(chosen)} relation(s), one pair each, same seed on both "
          f"sides.")
    print("  No oracle is used here: each config is compared against its OWN")
    print("  unperturbed answer, so this survives a task with no ground truth.")
    if live:
        print(f"  LIVE: {(len(chosen) + 1) * len(variants)} extra model calls.")
    print()

    outcomes = metamorphic.check(
        task=task, variants=variants, execute=None,
        run_once=metamorphic.clean_runner(task, executor=executor), repeats=1,
    )
    by_variant: dict[str, dict] = {}
    for o in outcomes:
        by_variant.setdefault(o.variant_id, {})[o.relation] = o

    label = {v.id: " ".join(str(v.factors[k]) for k in sorted(v.factors))
             or v.id for v in variants}
    print(f"  {'variant':<34}" + "".join(f"{n[:11]:>12}" for n in names))
    violations: list[tuple[str, str]] = []
    for v in variants:
        row = by_variant.get(v.id, {})
        cells = ""
        for n in names:
            o = row.get(n)
            if o is None or o.satisfied is None:
                cells += f"{'n/a':>12}"      # undecidable, never a 0
            elif o.satisfied:
                cells += f"{'ok':>12}"
            else:
                cells += f"{'VIOLATED':>12}"
                violations.append((label[v.id], n))
        print(f"  {label[v.id][:34]:<34}{cells}")

    if violations:
        print()
        for vid, name in violations:
            relation = next(r for r in chosen if r.name == name)
            print(f"  {vid} breaks {name}: {relation.asks}")
            print(f"    catches: {relation.catches}")
        print("\n  A violation is a binary property, so by #27 it is")
        print("  gate-shaped rather than report-shaped. It does NOT gate")
        print("  today -- that is a bigger claim than a column and #43 keeps")
        print("  it a separate decision. Read these rows yourself.")
    else:
        print("\n  Every declared relation held for every config. One pair each:")
        print("  a relation that holds once is not a relation that holds.")


def _print_attribution(records, sentinel_ids) -> None:
    """Shapley shares: the same question as the block above, answered in a
    way that survives interaction (#22).

    Printed beside the marginal table rather than instead of it, on purpose.
    Where the two disagree, the disagreement is the finding -- and a reader
    who has only ever seen the marginal numbers should be able to see what
    they were missing rather than be told to trust a new table.
    """
    print("\n--- per-factor attribution (Shapley) " + "-" * 35)

    reported = False
    for metric in ("quality", "faulted_quality"):
        report = attribution.attribute(records, metric=metric,
                                       sentinel_ids=sentinel_ids)
        if report.refusal:
            print(f"  {metric}: REFUSED -- {report.refusal}")
            continue
        reported = True
        corners = "by level name, not by score" if report.chose_corners_by_name \
            else "chosen by the caller"
        base = ", ".join(f"{k}={v}" for k, v in sorted(report.baseline.items()))
        targ = ", ".join(f"{k}={v}" for k, v in sorted(report.target.items()))
        print(f"\n  {metric}: {report.baseline_value:.0%} -> "
              f"{report.target_value:.0%}   gap {report.gap:+.0%}")
        print(f"    from  {base}")
        print(f"    to    {targ}      ({corners})")
        print()
        wide = max(len(s.from_level) for s in report.shares)
        for share in report.shares:
            flag = ("   <- INTERVAL INCLUDES ZERO: not measured"
                    if share.excludes_zero is False else "")
            ci = share.interval
            bounds = f"  [{ci.low:+.0%}, {ci.high:+.0%}]" if ci else ""
            print(f"    {share.factor:<10} {share.from_level:<{wide}} -> "
                  f"{share.to_level:<18} {share.share:>+5.0%}{bounds}{flag}")
        # The floor is context, not the verdict: a share averages several
        # contrasts and can legitimately be tighter than the two-proportion
        # resolution for any one of them. Saying which of the two a row
        # rests on is the point (#34).
        thin = [s.factor for s in report.shares
                if s.below_resolution(report.resolution)]
        if report.resolution is not None:
            print(f"\n    Design floor: one contrast at n={report.min_cell_n} "
                  f"resolves {report.resolution:.0%}.")
            if thin:
                print(f"    Under it, resting on the interval alone: "
                      f"{', '.join(thin)}.")
        # Efficiency is the property the marginal table does not have, so it
        # is stated rather than assumed -- and checked, because an
        # attribution that does not add up is arithmetic, not evidence.
        if report.explains_all_of_it:
            print(f"\n    The shares add to the whole gap ({report.gap:+.0%}). "
                  "That is the efficiency")
            print("    axiom, and it is what the marginal table cannot do.")

        for inter in report.interactions:
            a, b = inter.factors
            if abs(inter.index) < max(abs(s.share) for s in report.shares):
                continue
            print(f"\n    BUT {a} x {b} interact by {inter.index:+.0%}, more than "
                  f"either share.")
            print(f"    Neither is worth its number alone. The sentence to quote is")
            print(f"    conditional: what {a} is worth AT a given {b}, not on average.")

    if not reported:
        return
    censored = [m for m in ("detection_rate", "repair_rate", "propagation_rate")
                if attribution.attribute(records, metric=m,
                                         sentinel_ids=sentinel_ids,
                                         resamples=1).refusal]
    if censored:
        print(f"\n  Not attributable on this grid: {', '.join(censored)}.")
        print("  A metric with no value in part of the design cannot be")
        print("  decomposed across it -- there is no coalition to compare.")


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
    decidable = report.n_pairs - report.undecided_pairs
    if report.top1_stability is None:
        print("  top-1 stability = n/a  (no seed pair had a decidable winner)")
    else:
        print(f"  top-1 stability = {report.top1_stability:.0%}"
              f"  (of {decidable} decidable pair(s)"
              + (f"; {report.undecided_pairs} censored, top tied)"
                 if report.undecided_pairs else ")"))

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
    if report.undecided_pairs:
        # The same zero-tolerance rule as undefined tau, one depth up. A
        # seed whose top is tied has no winner, so that pair says nothing
        # about whether the winner holds -- and a gate that passes on the
        # remaining pairs is reporting agreement it did not observe (#1).
        failures.append(
            f"{report.undecided_pairs} seed pair(s) had no decidable winner -- "
            "the top was tied, so agreement was never observable"
        )
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

    # A certificate is the bar a later run is held to. Issuing one over a
    # ledger that holds the same cell twice would bake the averaged retry
    # into the baseline itself.
    if (rc := _refuse_rerun_cells(records, args.runs)) is not None:
        return rc

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


def _canary(args) -> int:
    """Take a behavioural fingerprint, and say what it could not have seen.

    #19's open question is what triggers a re-run, and its comment thread
    answers it: a provider can change behaviour without changing the model
    string, so there is no version to poll and a periodic canary is the
    only reliable detector. Which makes the binding constraint cost, and
    the binding risk a cheap canary that reports "unchanged" about a shift
    it could never have resolved.

    So every verdict here carries its own blind spot, and the exit code
    distinguishes "nothing moved" from "nothing moved that I could see".
    """
    from . import canary as canary_mod
    from .certify import RegressionBudget

    task = TaskSpec.from_yaml(args.task)
    out = Path(args.out)
    models = parse_models(getattr(args, "models", None))
    variants = architect.generate(
        out_dir=out / "variants", task=task, models=models,
        prompts=[args.prompt], toolsets=[args.toolset], include_sentinel=False,
    )
    chosen = next((v for v in variants if v.factors.get("model") == args.model),
                  None)
    if chosen is None:
        print(f"no variant at model={args.model!r} for "
              f"prompt={args.prompt!r} toolset={args.toolset!r}")
        return 2

    executor = None
    if args.live:
        from .live import MissingCredentials, live_executor, preflight

        try:
            preflight([chosen], target=args.target)
        except MissingCredentials as exc:
            print(f"PREFLIGHT FAILED\n{exc}")
            return 2
        executor = live_executor(args.target)

    print(f"task        {task.id}  ({task.fingerprint()})")
    print(f"config      {chosen.id}")
    print(f"            {chosen.fingerprint}")
    print(f"cells       seed={args.seed!r}, {args.repeats} repeat(s) x "
          f"{len(task.scenarios)} scenario(s) x 2 conditions")
    print(f"mode        {'LIVE -- this spends money' if args.live else 'offline'}")

    records = run_matrix(
        task=task, variants=[chosen], ledger=Ledger(out / "runs.jsonl"),
        base_seed=args.seed, repeats=args.repeats,
        fault_kind=FaultKind(task.fault_kind), executor=executor,
        offline=not args.live,
    )
    now = canary_mod.take(
        records, variant_id=chosen.id, task_fingerprint=task.fingerprint(),
        base_seed=args.seed, repeats=args.repeats,
        variant_fingerprint=chosen.fingerprint,
    )
    print(f"\n--- fingerprint " + "-" * 56)
    for name, value in sorted(now.metrics.items()):
        ci = now.intervals.get(name)
        width = f"  [{ci.low:.2f}, {ci.high:.2f}]" if ci else "  (no interval)"
        print(f"  {name:<20}{value:>8.3f}{width}")
    print(f"  over {now.n_runs} run(s)")

    if args.save:
        canary_mod.save([now], args.save)
        print(f"\nwrote       {args.save}")

    if not args.baseline:
        print("\n  No baseline given, so nothing is compared. Save this one "
              "and\n  pass it as --baseline next time.")
        return 0

    before = next((c for c in canary_mod.load(args.baseline)
                   if c.variant_id == chosen.id), None)
    if before is None:
        print(f"\n  {args.baseline} holds no canary for {chosen.id}.")
        return 2

    report = canary_mod.compare(before, now)
    print(f"\n--- against {before.taken_at} " + "-" * 40)
    if report.refusal:
        print(f"  REFUSED -- {report.refusal}")
        return 2

    for shift in report.shifts:
        delta = "" if shift.delta is None else f"{shift.delta:+8.3f}"
        blind = ("" if shift.blind_below is None
                 else f"   (blind below {shift.blind_below:.2f})")
        print(f"  {shift.metric:<20}{shift.shift.value:<16}{delta}{blind}")

    sens = canary_mod.sensitivity(report, RegressionBudget(
        max_quality_drop=args.budget))
    print()
    if report.moved:
        print("  SOMETHING MOVED. These intervals do not overlap, which "
              "means a real")
        print("  difference rather than run-to-run variance. Re-certify "
              "before shipping.")
        return 3

    # The sentence this whole command exists to be able to say honestly.
    blind = report.blind_below
    print(f"  Nothing moved that {now.n_runs} runs could have seen.")
    if blind is not None:
        print(f"  This canary is blind below {blind:.0%}, and you asked about "
              f"{sens['needed']:.0%}.")
        if not sens["adequate"]:
            need = sens["runs_needed"]
            print(f"\n  SO THIS IS NOT A REGRESSION CHECK. It is a trigger. "
                  f"Catching a\n  {sens['needed']:.0%} drop takes about "
                  f"{need} runs per side; this took {now.n_runs}.")
            print("  Read 'unchanged' as 'no reason to spend the real "
                  "budget today'.")
            return 0
        print("\n  And that is enough to have caught the drop you asked "
              "about.")
    steps = report.blind_below_steps
    if steps is not None:
        print(f"\n  Behaviour: mean steps blind below {steps:.2f} step(s). "
              "Steps move\n  before correctness does, which is why they are "
              "watched at all.")
    return 0


def _relations(args) -> int:
    """Score a task nobody knows the answer to (#28).

    Every other number this tool prints compares an answer against
    something the harness knows. This one compares a config's answers to
    *each other* under a perturbation whose effect is known -- rename the
    records and the total must not move -- so it works on a task with no
    oracle at all, which is most real work.
    """
    from . import metamorphic

    task = TaskSpec.from_yaml(args.task)
    out = Path(args.out)
    models = parse_models(getattr(args, "models", None))
    variants = architect.generate(out_dir=out / "variants", task=task,
                                  models=models)
    chosen = metamorphic.relations_for(task)

    print(f"task        {task.id}")
    print(f"relations   {', '.join(r.name for r in chosen)}")
    print(f"pairs       {args.repeats} per relation, same seed on both sides")
    print("\n  No oracle is used below. Each relation compares a config's")
    print("  perturbed answer against its OWN unperturbed one, so none of")
    print("  this needs to know the right answer.\n")
    for r in chosen:
        print(f"  {r.name:<9} {r.asks}")
        print(f"  {'':<9} catches: {r.catches}")
        if r.slack:
            print(f"  {'':<9} +/- {r.slack}: the comparison itself rounds, "
                  f"and that is arithmetic rather than behaviour")

    outcomes = metamorphic.check(
        task=task, variants=variants, execute=None,
        run_once=metamorphic.clean_runner(task), repeats=args.repeats,
    )

    print("\n--- relations " + "-" * 58)
    names = [r.name for r in chosen]
    print(f"  {'variant':<40}" + "".join(f"{n[:9]:>10}" for n in names)
          + f"{'kept':>8}")
    tally = metamorphic.coverage(outcomes)
    by_variant: dict[str, dict[str, metamorphic.RelationOutcome]] = {}
    for o in outcomes:
        by_variant.setdefault(o.variant_id, {})[o.relation] = o

    violated = []
    for vid, row in by_variant.items():
        cells = ""
        for n in names:
            o = row.get(n)
            if o is None or o.satisfied is None:
                cells += f"{'n/a':>10}"          # never 0: undecidable
            elif o.satisfied:
                cells += f"{'ok':>10}"
            else:
                cells += f"{str(o.held) + '/' + str(o.runs):>10}"
                violated.append((vid, o))
        held, total = tally.get(vid, (0, 0))
        # The factor values, not the generated id: three variants whose ids
        # agree for their first forty characters print as three identical
        # rows, which is a table that cannot be read.
        spec = next((v for v in variants if v.id == vid), None)
        label = (" ".join(str(spec.factors[k]) for k in sorted(spec.factors))
                 if spec is not None and spec.factors else vid)
        print(f"  {label[:40]:<40}{cells}{f'{held}/{total}':>8}")

    if violated:
        print("\n  violations, with the answers that produced them:")
        for vid, o in violated:
            spec = next((v for v in variants if v.id == vid), None)
            label = (" ".join(str(spec.factors[k]) for k in sorted(spec.factors))
                     if spec is not None and spec.factors else vid)
            print(f"    {label[:40]:<40} {o.relation:<9} "
                  f"{o.baseline} -> {o.observed}, wanted {o.wanted}")
        print("\n  None of the above consulted a known answer. A config can")
        print("  be caught here on a task where nobody knows what the right")
        print("  total is.")

    undecided = [o for o in outcomes if o.satisfied is None]
    if undecided:
        print(f"\n  {len(undecided)} relation(s) could not be decided -- a run")
        print("  produced no answer, so there was nothing to compare. Reported")
        print("  as n/a and left out of the denominator, never counted as a")
        print("  pass.")

    print("\n  Offline policies never read the task statement, so")
    print("  rephrase-invariance -- the most universal relation there is --")
    print("  is deliberately not in this suite. It would pass vacuously and")
    print("  report a property of the test doubles as one of the agents.")
    return 0 if not violated else 3


def _refuse_rerun_cells(records, path) -> Optional[int]:
    """Refuse a ledger that holds the same cell twice (#27).

    The one place the software-testing analogy inverts. CI treats a flaky
    test as a defect to retry until green; here flakiness IS the
    measurement -- an agent that succeeds 7 times in 10 is 70% reliable,
    and re-running until the gate passes does not fix the config, it
    deletes the number. Averaging two attempts at one cell is that mistake
    made quietly, so the tooling refuses rather than documenting it
    somewhere nobody reads.
    """
    dupes = ledger_duplicates(records)
    if not dupes:
        return None
    extra = sum(n - 1 for n in dupes.values())
    print(f"REFUSED: {path} holds {len(dupes)} cell(s) more than once "
          f"({extra} extra run(s)).")
    print()
    print("  A cell is one (variant, scenario, repeat, seed, condition), and")
    print("  a matrix produces each exactly once. More than one means the")
    print("  matrix was run again into the same ledger.")
    print()
    print("  This is the one place the testing analogy inverts. A flaky test")
    print("  is a defect to retry until green; a flaky agent is a 70%")
    print("  reliable agent, and that 70% is the measurement. Re-running")
    print("  until the gate passes does not fix the config -- it deletes the")
    print("  number, and averaging the attempts hides that it happened.")
    print()
    for (variant, scenario, repeat, seed, condition), n in sorted(dupes.items())[:3]:
        print(f"    {n}x  {variant}  {scenario} repeat={repeat} {condition}")
    if len(dupes) > 3:
        print(f"    ... and {len(dupes) - 3} more")
    print()
    print("  Use a fresh --out directory for each matrix.")
    return 2


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

    if (rc := _refuse_rerun_cells(records, args.runs)) is not None:
        return rc

    results = board.summarize(records)
    budget = certify.RegressionBudget(
        max_quality_drop=args.max_quality_drop,
        require_resolution=args.require_resolution,
        gate_on_quality=not getattr(args, "safety_only", False),
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

    if getattr(args, "safety_only", False):
        print("\n  --safety-only: quality regressions above are reported and")
        print("  do not fail this check. The binary properties -- propagation")
        print("  and compliance -- still do, and no budget forgives them.")

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
