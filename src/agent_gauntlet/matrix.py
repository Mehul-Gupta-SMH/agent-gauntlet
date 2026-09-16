"""Run the M0 matrix: variants x scenarios x repeats x {clean, faulted}.

Fairness invariants enforced here rather than left to the caller: every
variant sees the same scenarios, the same fault seeds, and the same task
statement, and the clean/faulted halves of a pair share a seed so
robustness is measured against a variant's own baseline.
"""

from __future__ import annotations

import uuid
from typing import Callable, Optional, Sequence

from .faults import FaultKind, FaultSchedule
from .interpose import run_context
from .ledger import Ledger, RunRecord
from .offline import run_policy
from .score import Answer, score_run
from .spec import TaskSpec, VariantSpec

Executor = Callable[[VariantSpec, str], Answer]
"""Given a variant, produce its answer. The offline executor runs a scripted
policy; a live one would drive `commonadk.runners.get_runner(target).run_sync`
and parse the final message."""


def offline_executor(variant: VariantSpec, seed: str) -> Answer:
    """Execute a variant's scripted policy. No model, no cost.

    The seed is threaded through so the policy's occasional slip is
    reproducible: variance without losing determinism.
    """
    policy = variant.factors.get("prompt", "naive")
    return run_policy(policy, seed, variant.factors.get("model"))


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
) -> list[RunRecord]:
    """Run the full matrix and append every run to `ledger`."""
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    if not variants:
        raise ValueError("no variants to run")

    execute = executor or offline_executor
    fingerprint = task.fingerprint()
    produced: list[RunRecord] = []

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
                    evidence_tool="get_summary" if _has_evidence(variant) else None,
                    # Only the audited records may be corrupted. A fault
                    # outside the cross-check's coverage contradicts nothing
                    # reachable, so it is undetectable in principle -- but
                    # the run would still look like one where detection was
                    # possible and score as a miss. Confining injection
                    # keeps censoring a property of the tool set alone.
                    targets=scenario.audited_ids,
                )
                for condition, schedule in (
                    ("clean", FaultSchedule.clean(seed)),
                    ("faulted", faulted),
                ):
                    with run_context(
                        scenario.records,
                        schedule,
                        _allowed_tools(variant),
                        scenario.audited_ids,
                    ) as ctx:
                        answer = execute(variant, f"{seed}:{condition}")
                        score = score_run(
                            task=task,
                            scenario=scenario,
                            schedule=schedule,
                            ctx=ctx,
                            answer=answer,
                        )
                    record = RunRecord(
                        run_id=uuid.uuid4().hex[:12],
                        task_id=task.id,
                        task_fingerprint=fingerprint,
                        variant_id=variant.id,
                        factors=dict(variant.factors),
                        scenario_id=scenario.id,
                        repeat=repeat,
                        seed=seed,
                        condition=condition,
                        schedule=schedule,
                        score=score,
                        tool_calls=list(ctx.calls),
                        offline=offline,
                    )
                    ledger.append(record)
                    produced.append(record)

    return produced


def _has_evidence(variant: VariantSpec) -> bool:
    """Can this variant reach the cross-check at all?

    Derived from the declared tool set where one exists. Variants exercised
    only through scripted policies (no `toolset` factor) are assumed to have
    it, since every offline policy can call `summary_total()`.
    """
    toolset = variant.factors.get("toolset")
    if toolset is None:
        return True
    from .architect import TOOLSETS

    return "get_summary" in TOOLSETS.get(toolset, [])


def _allowed_tools(variant: VariantSpec) -> Optional[set[str]]:
    """The tool grant implied by this variant's factors.

    None (unrestricted) when no toolset factor is declared, so variants
    exercised directly in tests keep working.
    """
    toolset = variant.factors.get("toolset")
    if toolset is None:
        return None
    from .architect import TOOLSETS

    return set(TOOLSETS.get(toolset, []))
