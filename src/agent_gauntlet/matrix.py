"""Run the M0 matrix: variants x scenarios x repeats x {clean, faulted}.

Fairness invariants enforced here rather than left to the caller: every
variant sees the same scenarios, the same fault seeds, and the same task
statement, and the clean/faulted halves of a pair share a seed so
robustness is measured against a variant's own baseline.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Optional, Sequence

import time

from . import events
from .faults import FaultKind, FaultSchedule
from .interpose import run_context
from .live import provider_unreachable
from .ledger import Ledger, RunRecord
from .offline import run_policy
from .score import Answer, _band, score_run
from .spec import TaskSpec, VariantSpec

Executor = Callable[[VariantSpec, str], Any]
"""Given a variant, produce its answer -- and, if it metered one, its roll-up.

Returns either an `Answer` or an `(Answer, rollup)` pair. The offline
executor runs a scripted policy and meters nothing; the live one drives
`commonadk.runners.get_runner(target).run_sync` and returns what that run
cost alongside what it said.

The pair exists because the first version returned the answer alone. The
live path had the `Trace` in hand -- token counts, `cost_usd`, durations,
all verified as complete back in experiment 002 -- and dropped it on the
floor, so a full paid matrix recorded no spend and the board's cost column
read `n/a` (#14).
"""


def _unpack(result: Any) -> tuple[Answer, dict]:
    """Accept an executor that meters and one that does not."""
    if isinstance(result, tuple):
        answer, rollup = result
        return answer, dict(rollup or {})
    return result, {}


def offline_executor(variant: VariantSpec, seed: str) -> Answer:
    """Execute a variant's scripted policy. No model, no cost.

    The seed is threaded through so the policy's occasional slip is
    reproducible: variance without losing determinism.
    """
    policy = variant.factors.get("prompt", "naive")
    return run_policy(policy, seed, variant.factors.get("model"))


def _attempt(
    execute: Executor,
    variant: VariantSpec,
    seed: str,
    *,
    max_attempts: int,
    backoff: float,
) -> tuple[Optional[Answer], dict, Optional[str], int]:
    """Run one cell, retrying only what is worth retrying.

    Returns `(answer, rollup, error, attempts)`; `answer` is None exactly
    when `error` is set.

    Provider-shaped failures are retried because they are transient and a
    matrix is hundreds of calls long -- one overloaded response part-way
    through used to discard every run that would have followed it. A
    missing import is not retried: it will fail identically three times and
    the only effect is to waste the operator's patience.

    Nothing here swallows the failure. When the retries are exhausted the
    reason is returned, recorded, and censored from the rates -- an agent
    that never got to answer is not an agent that answered wrongly.
    """
    last = ""
    for attempt in range(1, max_attempts + 1):
        try:
            answer, rollup = _unpack(execute(variant, seed))
            return answer, rollup, None, attempt
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt >= max_attempts or not provider_unreachable(exc):
                return None, {}, last, attempt
            if backoff:
                time.sleep(backoff * (2 ** (attempt - 1)))
    return None, {}, last, max_attempts  # pragma: no cover - loop always returns


def seed_for(*, base: str, variant: str, scenario: str, repeat: int) -> str:
    """Deterministic per-run seed.

    Deliberately includes the variant id: two variants facing the *same*
    scenario get different corrupted values, so a ranking cannot be an
    artifact of one variant happening to draw an easier corruption. It
    excludes the condition, so a clean/faulted pair stays twinned.
    """
    return f"{base}:{variant}:{scenario}:{repeat}"


def run_matrix(
    *,
    task: TaskSpec,
    variants: Sequence[VariantSpec],
    ledger: Ledger,
    base_seed: str,
    repeats: int = 3,
    fault_kind: FaultKind = FaultKind.WRONG_VALUE,
    executor: Optional[Executor] = None,
    offline: bool = True,
    max_attempts: int = 3,
    backoff: float = 1.0,
    user_tools: Optional[dict] = None,
    allowed_tools: Optional[dict] = None,
    project_inputs: Optional[Sequence] = None,
    decidable_faults: bool = False,
    budget: Optional[Any] = None,
) -> list[RunRecord]:
    """Run the full matrix and append every run to `ledger`."""
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    if not variants:
        raise ValueError("no variants to run")

    execute = executor or offline_executor
    fingerprint = task.fingerprint()
    produced: list[RunRecord] = []

    events.emit(
        "matrix.start",
        task=task.id,
        fingerprint=fingerprint,
        base_seed=base_seed,
        fault_tool=task.fault_tool,
        offline=offline,
        # Both conditions per cell: a faulted run means nothing without the
        # variant's own clean baseline to measure degradation from.
        total=len(variants) * len(task.scenarios) * repeats * 2,
        variants=[
            {
                "id": v.id,
                "factors": dict(v.factors),
                "model": v.model,
                "fingerprint": v.fingerprint,
                "sentinel": v.is_sentinel,
            }
            for v in variants
        ],
    )

    for variant in variants:
        for scenario in task.scenarios:
            for repeat in range(repeats):
                seed = seed_for(
                    base=base_seed,
                    variant=variant.id,
                    scenario=scenario.id,
                    repeat=repeat,
                )
                # A variant whose tool set excludes the cross-check has no
                # reachable evidence, so an injected falsehood is
                # undetectable *in principle* for it. Those runs are the
                # genuine censoring case and must leave the detection
                # denominator rather than score as misses (#16).
                faulted = FaultSchedule.build(
                    seed=seed,
                    records=scenario.records,
                    kind=fault_kind,
                    tool_name=task.fault_tool,
                    evidence_tool=_evidence_tool(variant, allowed_tools, task.fault_tool),
                    # Only the audited records may be corrupted. A fault
                    # outside the cross-check's coverage contradicts nothing
                    # reachable, so it is undetectable in principle -- but
                    # the run would still look like one where detection was
                    # possible and score as a miss. Confining injection
                    # keeps censoring a property of the tool set alone.
                    targets=scenario.audited_ids,
                    decidable_band=(
                        # TWICE the noise band: `_determinable` requires the
                        # delta to clear the band around the truth AND the
                        # one around the lie, or the two overlap and no
                        # answer could separate them. Passing one band's
                        # worth left most faults undecidable anyway.
                        2 * _band(scenario.expected_total, task.tolerance)
                        if decidable_faults else None
                    ),
                )
                # Clean first, always, and its answer is kept: under
                # `Oracle.BASELINE` it is the only truth the faulted half
                # has to be judged against.
                baseline: Optional[int] = None
                for condition, schedule in (
                    ("clean", FaultSchedule.clean(seed)),
                    ("faulted", faulted),
                ):
                    label = f"{variant.id}|{scenario.id}|{repeat}|{condition}"
                    events.emit(
                        "run.start",
                        run=label,
                        variant=variant.id,
                        scenario=scenario.id,
                        repeat=repeat,
                        condition=condition,
                        seed=seed,
                        # What the harness knows and the agent does not.
                        # The oracle is ours because we injected it, which
                        # is the whole reason no judge is needed.
                        faults=[
                            {
                                "tool": f.tool_name,
                                "target": f.target_key,
                                "true": f.true_value,
                                "corrupt": f.corrupt_value,
                                "kind": f.kind.value,
                            }
                            for f in schedule.faults
                        ],
                        expected=scenario.expected_total,
                        credulous=scenario.expected_total + schedule.total_delta,
                    )
                    with run_context(
                        scenario.records,
                        schedule,
                        _allowed_tools(variant, allowed_tools),
                        scenario.audited_ids,
                        user_tools=user_tools,
                        project_inputs=project_inputs,
                    ) as ctx:
                        ctx.run_label = label
                        answer, rollup, error, attempts = _attempt(
                            execute, variant, f"{seed}:{condition}",
                            max_attempts=max_attempts, backoff=backoff,
                        )
                        # An errored run is still scored, against an
                        # unanswered Answer, so the record has the same
                        # shape as every other. `error` is what marks it
                        # censored; the score is not evidence about the
                        # agent and nothing should read it as such.
                        score = score_run(
                            task=task,
                            scenario=scenario,
                            schedule=schedule,
                            ctx=ctx,
                            answer=answer if answer is not None else Answer(),
                            baseline=baseline,
                        )
                    if condition == "clean":
                        baseline = answer.total if answer is not None else None
                    record = RunRecord(
                        run_id=uuid.uuid4().hex[:12],
                        task_id=task.id,
                        task_fingerprint=fingerprint,
                        variant_id=variant.id,
                        model=variant.model,
                        variant_fingerprint=variant.fingerprint,
                        error=error,
                        attempts=attempts,
                        factors=dict(variant.factors),
                        scenario_id=scenario.id,
                        repeat=repeat,
                        seed=seed,
                        condition=condition,
                        schedule=schedule,
                        score=score,
                        answer=answer,
                        rollup=rollup,
                        tool_calls=list(ctx.calls),
                        offline=offline,
                    )
                    ledger.append(record)
                    produced.append(record)
                    # After the append, never before: a run that pushed the
                    # total past the ceiling is still a run that happened,
                    # and dropping its record would hide spend the operator
                    # has already been charged for.
                    if budget is not None:
                        budget.record(rollup)
                    events.emit(
                        "run.end",
                        run=label,
                        variant=variant.id,
                        condition=condition,
                        run_id=record.run_id,
                        answer=None if answer is None else answer.total,
                        flagged=bool(answer and answer.flagged_anomaly),
                        error=error,
                        attempts=attempts,
                        steps=score.steps,
                        correct=score.correct,
                        accuracy=score.accuracy,
                        outcome=score.outcome.value,
                        propagated=score.propagated,
                        repaired=score.repaired,
                        detected=score.detected,
                        exposure_possible=score.exposure_possible,
                        redundant_material=score.redundant_material_calls,
                    )

    events.emit("matrix.end", produced=len(produced))
    return produced


def _evidence_tool(
    variant: VariantSpec, override: Optional[dict], fault_tool: str
) -> Optional[str]:
    """Which tool would contradict the lie, if this variant holds one."""
    if not _has_evidence(variant, override, fault_tool):
        return None
    if override is None:
        return "get_summary"
    granted = override.get(variant.factors.get("toolset", ""), [])
    return next((t for t in sorted(granted) if t != fault_tool), None)


def _has_evidence(
    variant: VariantSpec,
    override: Optional[dict] = None,
    fault_tool: str = "",
) -> bool:
    """Can this variant reach a cross-check at all?

    Derived from the declared tool set where one exists. Variants exercised
    only through scripted policies (no `toolset` factor) are assumed to have
    it, since every bundled offline policy can call `summary_total()`.

    For a project the cross-check is not a named tool but a *shape*: any
    granted source other than the corrupted one covers the same inputs and
    can therefore contradict it. A grant with nothing but the corrupted tool
    has no reachable evidence, and detection is censored for it rather than
    scored as a miss (#16).
    """
    toolset = variant.factors.get("toolset")
    if toolset is None:
        return True
    if override is not None:
        return any(t != fault_tool for t in override.get(toolset, []))
    from .architect import TOOLSETS

    return "get_summary" in TOOLSETS.get(toolset, [])


def _allowed_tools(
    variant: VariantSpec, override: Optional[dict] = None
) -> Optional[set[str]]:
    """The tool grant implied by this variant's factors.

    `override` maps tool-set name -> members, for a project whose tool sets
    are built from the operator's own tools rather than the bundled
    inventory. None (unrestricted) when no toolset factor is declared, so
    variants exercised directly in tests keep working.
    """
    toolset = variant.factors.get("toolset")
    if toolset is None:
        return None
    if override is not None:
        return set(override.get(toolset, []))
    from .architect import TOOLSETS

    return set(TOOLSETS.get(toolset, []))
