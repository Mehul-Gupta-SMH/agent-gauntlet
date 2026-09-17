"""Turning a project into a gauntlet run.

Everything here exists to reach the same `run_matrix` the fixtures use, with
the same scoring and the same board. A project does not get a parallel,
friendlier pipeline; it gets the same one, and where it cannot supply
something the pipeline needs, that shows up as `n/a` rather than as a
special case.

Two things a project has that a fixture does not:

* **tools the harness did not write.** Calibrated once into a value table
  before the matrix, which is what gives every later run an oracle and what
  keeps a MATERIAL tool from being called three hundred times.
* **possibly no label.** The right answer may be unknown. Propagation is
  still decidable against the variant's own clean run, so the gate still
  works; accuracy is censored rather than redefined.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Optional

from . import events, usertools
from .interpose import ToolTimeout, ToolUnavailable, active
from .ledger import Ledger
from .matrix import run_matrix
from .project import Project
from .score import Answer
from .spec import Oracle, Scenario, TaskSpec, VariantSpec

SENTINEL_TOOLSET = "half-sighted"
"""The structural sentinel for a project.

Degraded by a *missing capability* rather than an instruction, for the
reason experiment 003 established: a capable model asked to be careless
simply is not. A contender that can only enumerate half the inputs cannot
reason its way to the ones it cannot see, however good it is.
"""


# --- generic policies -----------------------------------------------------
#
# The bundled scripted policies call the inventory fixture's tools by name.
# A project's tools are the operator's, so its policies have to be written
# against the *shape* of the task instead: read the primary tool over every
# input, and -- if there is a second source -- reconcile against it.


def _inputs() -> list[list[Any]]:
    ctx = active()
    return list(ctx.project_inputs)


def _read(tool: str, args: list[Any]) -> Optional[int]:
    try:
        value = usertools.serve(tool, *args)
    except (ToolTimeout, ToolUnavailable, KeyError):
        return None
    return value if isinstance(value, (int, float)) else None


def _slip(seed: Optional[str], total: float, rate: float) -> int:
    """The same occasional miscount the bundled policies have.

    Without it every scripted contender is perfect on clean runs and the
    grid has no resolution at all -- a ranking needs something to rank.
    """
    if not seed or not total:
        return 0
    rng = random.Random(hash(seed) & 0xFFFFFFFF)
    if rng.random() >= rate:
        return 0
    return int(rng.choice([-1, 1]) * max(1, abs(total) * rng.uniform(0.02, 0.08)))


def project_naive(seed=None, rate=0.15, *, primary: str = "", **_) -> Answer:
    """Read the primary tool and report the total. Believes what it is told."""
    total = sum(v for args in _inputs() if (v := _read(primary, args)) is not None)
    return Answer(total=int(total) + _slip(seed, total, rate), flagged_anomaly=False)


def project_verifying(seed=None, rate=0.15, *, primary: str = "",
                      cross: str = "", **_) -> Answer:
    """Read the primary tool, then check it against a second source.

    Where the two disagree the cross-check wins and the disagreement is
    surfaced -- the free correction, applied to a tool where re-reading is
    free. It is deliberately *not* a re-call of the primary: on a MATERIAL
    tool that would be a second harm (#30).
    """
    rows = {usertools.key_for(args): _read(primary, args) for args in _inputs()}
    flagged = False
    if cross:
        for args in _inputs():
            key = usertools.key_for(args)
            other = _read(cross, args)
            if other is not None and rows.get(key) != other:
                rows[key] = other
                flagged = True
    total = sum(v for v in rows.values() if v is not None)
    return Answer(total=int(total) + _slip(seed, total, rate), flagged_anomaly=flagged)


def project_sentinel(seed=None, rate=0.15, *, primary: str = "", **_) -> Answer:
    """The instrument check: sees only half the inputs, and cannot know it."""
    visible = _inputs()[: max(1, len(_inputs()) // 2)]
    total = sum(v for args in visible if (v := _read(primary, args)) is not None)
    return Answer(total=int(total) + _slip(seed, total, rate), flagged_anomaly=False)


POLICIES = {
    "naive": project_naive,
    "verifying": project_verifying,
    "sentinel": project_sentinel,
}


# --- assembling the run ---------------------------------------------------


def toolsets(project: Project) -> dict[str, list[str]]:
    """The tool axis, built from the operator's own inventory.

    `all` is everything declared. `primary-only` drops every source but the
    corrupted one, which is what makes the cross-check a *factor* rather
    than an assumption: without a contender that lacks it, the board cannot
    show what having it is worth.
    """
    names = project.tool_names()
    sets = {"all": sorted(names)}
    if len(names) > 1:
        sets["primary-only"] = [project.fault_tool]
    sets[SENTINEL_TOOLSET] = sorted(names)
    return sets


def cross_check(project: Project, granted: list[str]) -> str:
    """A second source over the same inputs, if this grant has one."""
    for name in sorted(granted):
        if name != project.fault_tool:
            return name
    return ""


def task_for(project: Project, tables: dict[str, dict[str, Any]]) -> TaskSpec:
    """A real `TaskSpec`, fingerprinted like any other.

    The world is the corrupted tool's own calibrated table, so a fault lands
    on a value the harness measured rather than one it invented.
    """
    scenario = project.scenarios[0]
    records = {
        k: int(v) for k, v in tables[project.fault_tool].items()
        if isinstance(v, (int, float))
    }
    if not records:
        raise ValueError(
            f"{project.fault_tool} returned no numeric values, and the harness "
            "grades by comparing numbers -- a fault it cannot measure is one it "
            "cannot score"
        )
    return TaskSpec(
        id=project.id,
        statement=project.statement,
        # No label means no accuracy, and says so. It does not mean no gate:
        # propagation is still decided against each variant's clean twin.
        oracle=Oracle.FULL if scenario.expected is not None else Oracle.BASELINE,
        tolerance=0,
        fault_tool=project.fault_tool,
        acceptable_degradation={"propagation_rate": 0},
        scenarios=[Scenario(id=scenario.id, records=records,
                            expected=scenario.expected)],
    )


def variants_for(project: Project, sets: dict[str, list[str]]) -> list[VariantSpec]:
    out: list[VariantSpec] = []
    for model in project.models:
        for prompt in project.prompts:
            for name in sorted(sets):
                if name == SENTINEL_TOOLSET:
                    continue
                factors = {"model": model.alias, "prompt": prompt, "toolset": name}
                out.append(VariantSpec(
                    id="__".join(f"{k}-{v}" for k, v in sorted(factors.items())),
                    factors=factors, model=model.model,
                ))
    first = project.models[0]
    out.append(VariantSpec(
        id=f"model-{first.alias}__prompt-sentinel__toolset-{SENTINEL_TOOLSET}",
        factors={"model": first.alias, "prompt": "sentinel",
                 "toolset": SENTINEL_TOOLSET},
        model=first.model, is_sentinel=True,
    ))
    return out


def calibrate_all(project: Project) -> dict[str, dict[str, Any]]:
    """Establish the truth, once, before anything is scored.

    Emitted as events so the page can show what each tool actually returned
    -- the operator should see the harness's oracle before it is used to
    judge anyone.
    """
    inputs = project.scenarios[0].inputs
    tables: dict[str, dict[str, Any]] = {}
    for tool in project.tools:
        events.emit("calibrate.start", tool=tool.name, cost=tool.cost.value,
                    described=tool.described, inputs=len(inputs))
        table = usertools.calibrate(tool, inputs, project_dir=project.dir)
        tool.table = table
        tables[tool.name] = table
        events.emit("calibrate.done", tool=tool.name,
                    values={k: v for k, v in list(table.items())[:50]})
    return tables


def executor_for(project: Project, sets: dict[str, list[str]]):
    """The offline executor, bound to this project's tool names."""

    def execute(variant: VariantSpec, seed: str) -> Answer:
        policy = POLICIES[variant.factors.get("prompt", "naive")]
        granted = sets.get(variant.factors.get("toolset", "all"), [])
        return policy(
            seed, 0.22 if variant.factors.get("model") == "cheap" else 0.08,
            primary=project.fault_tool,
            cross=cross_check(project, granted),
        )

    return execute


def run(project: Project, ledger: Ledger) -> list:
    """Calibrate, assemble, and run the matrix. Offline only, for now."""
    tables = calibrate_all(project)
    sets = toolsets(project)
    task = task_for(project, tables)
    variants = variants_for(project, sets)
    by_name = {t.name: t for t in project.tools}

    events.emit(
        "project.ready",
        task=task.id, fingerprint=task.fingerprint(),
        oracle=task.oracle.value,
        labelled=task.oracle is Oracle.FULL,
        toolsets=sets, material=project.material_tools(),
    )

    produced: list = []
    for i in range(max(1, project.seeds)):
        produced.extend(run_matrix(
            task=task, variants=variants, ledger=ledger, base_seed=f"seed{i}",
            repeats=max(1, project.repeats), offline=True,
            executor=executor_for(project, sets),
            user_tools=by_name, allowed_tools=sets,
            project_inputs=project.scenarios[0].inputs,
        ))
    return produced
