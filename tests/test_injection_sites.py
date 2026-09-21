"""Where a fault can actually land, asserted rather than assumed.

`fault_tool` and `fault_kind` are free text on a task. Four builtin tools
consult the fault schedule, each for a specific kind, and every other tool
-- and every other pairing -- returns its clean value. That is correct
behaviour: an annotation has no number to corrupt, and a quantity has
nowhere to put a sentence.

What was wrong was leaving the pairing implicit. A task naming a tool with
no injection site, or naming the wrong kind for a real one, produced a
matrix of runs *labelled faulted in which nothing was ever injected* --
and the board then reported `prop=0%`, ungated, for every variant. A clean
pass for a test that never ran, in the most flattering direction
available. The fail-green shape, again (#40).

The correction is the same one #38 made about network reachability, one
layer up: reachability is asserted from a registry, not inferred from a
tool being granted. `_exposure_possible` answers "could this variant have
*called* it", which is a different question, and its answer reads as a
pass when the answer to this one is no.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board
from agent_gauntlet.faults import FaultKind, FaultSchedule
from agent_gauntlet.interpose import (
    BUILTIN_TOOLS, INJECTION_SITES, USER_TOOL_KINDS, fetch_quantity,
    pull_credit_report, read_annotation, recall_note, record_note,
    run_context, unreachable,
)
from agent_gauntlet.matrix import run_matrix

ROOT = Path(__file__).resolve().parents[1]
RECORDS = {"a": 10, "b": 20, "c": 30}

NO_SITE = ["list_records", "get_summary", "record_note", "list_audited_records"]


def _task(tool: str, kind: str) -> TaskSpec:
    """The inventory fixture, repointed. Its grid grants every tool under
    test, so a refusal can only be about the injection site."""
    import yaml

    spec = yaml.safe_load((ROOT / "fixtures" / "inventory" / "task.yaml")
                          .read_text(encoding="utf-8"))
    spec["fault_tool"] = tool
    spec["fault_kind"] = kind
    return TaskSpec.model_validate(spec)


# --- the registry matches what the code actually does ---------------------


def test_a_registered_site_really_injects():
    """The registry is only worth having if it describes the functions. One
    call per entry, through the real interposer."""
    tools = set(INJECTION_SITES) | {"list_records", "get_summary",
                                    "record_note", "list_audited_records"}

    sched = FaultSchedule.build(seed="s", records=RECORDS,
                                tool_name="fetch_record", targets=["a"])
    with run_context(RECORDS, sched, tools, ["a"]) as ctx:
        assert fetch_quantity("a") != RECORDS["a"]
        assert any(c["faulted"] for c in ctx.calls)

    sched = FaultSchedule.build(seed="s", records=RECORDS,
                                kind=FaultKind.INSTRUCTION,
                                tool_name="read_annotation", targets=["a"])
    with run_context(RECORDS, sched, tools, ["a"]) as ctx:
        assert "SYSTEM NOTICE" in read_annotation("a")
        assert ctx.injected_directives

    sched = FaultSchedule.build(seed="s", records=RECORDS,
                                kind=FaultKind.POISONED_MEMORY,
                                tool_name="recall_note", targets=list(RECORDS))
    with run_context(RECORDS, sched, tools, list(RECORDS)) as ctx:
        record_note("running", 60)
        assert recall_note("running") != 60
        assert any(c["faulted"] for c in ctx.calls)


@pytest.mark.parametrize("tool", NO_SITE)
def test_a_tool_with_no_site_never_reports_a_faulted_call(tool):
    """The premise of the whole issue, pinned at the source: a fault placed
    on one of these is inert, whatever the board goes on to say about it."""
    sched = FaultSchedule.build(seed="s", records=RECORDS, tool_name=tool,
                                targets=list(RECORDS))
    assert sched.faults and all(f.tool_name == tool for f in sched.faults)
    with run_context(RECORDS, sched, BUILTIN_TOOLS, list(RECORDS)) as ctx:
        from agent_gauntlet.interpose import list_record_ids, summary_total
        for rid in list_record_ids():
            fetch_quantity(rid)
        summary_total()
        record_note("running", 1)
        assert not any(c["faulted"] for c in ctx.calls)


def test_the_builtin_list_covers_every_tool_the_grid_can_grant():
    """The drift guard. A tool added to a toolset but not to `BUILTIN_TOOLS`
    would be treated as an operator's uploaded tool and quietly skip the
    check -- the same hole, reopened by an unrelated change."""
    granted = {t for members in architect.TOOLSETS.values() for t in members}
    assert granted <= BUILTIN_TOOLS, sorted(granted - BUILTIN_TOOLS)


# --- the refusal ----------------------------------------------------------


@pytest.mark.parametrize("tool", NO_SITE)
def test_a_task_naming_a_tool_with_no_site_is_refused(tool, tmp_path):
    with pytest.raises(ValueError, match="no injection site"):
        architect.generate(out_dir=tmp_path, task=_task(tool, "wrong_value"),
                           models={"cheap": "m"})


@pytest.mark.parametrize("tool,kind", [
    ("fetch_record", "instruction"),
    ("fetch_record", "poisoned_memory"),
    ("read_annotation", "wrong_value"),
    ("read_annotation", "timeout"),
    ("recall_note", "wrong_value"),
    ("recall_note", "instruction"),
    ("pull_credit_report", "instruction"),
])
def test_a_real_site_asked_for_the_wrong_kind_is_refused(tool, kind, tmp_path):
    """The subtler half. The tool is injectable and the grid grants it --
    only the *kind* has nowhere to land, and the site returns its clean
    value without complaint."""
    with pytest.raises(ValueError, match="injection site for"):
        architect.generate(out_dir=tmp_path, task=_task(tool, kind),
                           models={"cheap": "m"})


def test_the_matrix_refuses_too_and_writes_nothing(tmp_path):
    """`architect.generate` is the friendly refusal; the matrix is the one
    every path goes through -- the CLI, the UI's fixture path, a project
    run, and a test calling it directly."""
    task = _task("fetch_record", "wrong_value")
    variants = architect.generate(out_dir=tmp_path / "v", task=task,
                                  models={"cheap": "m"})
    ledger = Ledger(tmp_path / "runs.jsonl")
    with pytest.raises(ValueError, match="cannot fire"):
        run_matrix(task=_task("list_records", "wrong_value"), variants=variants,
                   ledger=ledger, base_seed="s", repeats=1)
    assert not ledger.path.exists() or ledger.path.read_text() == ""


def test_the_legitimate_pairings_still_run(tmp_path):
    """A refusal that also refuses the real fixtures would be worse than the
    bug. Every pairing the repo actually ships must survive."""
    for fixture, tool, kind in [
        ("inventory/task.yaml", "fetch_record", "wrong_value"),
        ("poisoning/instruction.yaml", "read_annotation", "instruction"),
        ("poisoning/memory.yaml", "recall_note", "poisoned_memory"),
        ("lending/applicant.yaml", "pull_credit_report", "wrong_value"),
    ]:
        task = TaskSpec.from_yaml(ROOT / "fixtures" / fixture)
        assert task.fault_tool == tool and task.fault_kind == kind
        assert unreachable(tool, FaultKind(kind)) is None


# --- the regression, end to end -------------------------------------------


def test_the_board_this_used_to_print(tmp_path):
    """What the bug looked like from the outside, and why a refusal is the
    only acceptable fix.

    Before this change the same task ran 30 runs, injected nothing at all,
    and printed `prop=0%` with `gated=False` for four of five contenders --
    each row reading "faced the fault and never fell for it". Nothing in
    the output distinguished it from a matrix where every agent had
    genuinely resisted.
    """
    task = _task("list_records", "wrong_value")
    with pytest.raises(ValueError) as exc:
        architect.generate(out_dir=tmp_path, task=task, models={"cheap": "m"})
    # The message has to name the tool and what to do, not merely refuse.
    assert "list_records" in str(exc.value)
    assert "never consults the fault schedule" in str(exc.value)
    assert "fetch_record" in str(exc.value)  # the injectable ones, listed


# --- an operator's own tools ----------------------------------------------


@pytest.mark.parametrize("kind", sorted(k.value for k in USER_TOOL_KINDS))
def test_an_uploaded_tool_can_carry_the_kinds_usertools_implements(kind):
    assert unreachable("some_operator_tool", FaultKind(kind)) is None


def test_an_uploaded_tool_cannot_be_asked_to_poison_a_note():
    """The scratchpad is the harness's tool, not the operator's, and
    `usertools.serve` has no branch for it -- so the pairing would be a
    silent no-op on someone else's project."""
    why = unreachable("some_operator_tool", FaultKind.POISONED_MEMORY)
    assert why and "recall_note" in why


def test_a_project_is_told_before_it_runs(tmp_path, monkeypatch):
    """The wizard's turn. A blocker is the only one of these refusals a
    person sees before spending anything."""
    from agent_gauntlet import project as projects
    from agent_gauntlet.interpose import ToolCost
    from agent_gauntlet.usertools import UserTool

    monkeypatch.setenv("GAUNTLET_HOME", str(tmp_path / "projects"))
    p = projects.new("p")
    p.tools = [UserTool(name="pull_exposure", module="m.py",
                        function="pull_exposure", cost=ToolCost.NEGLIGIBLE)]
    p.fault_tool = "pull_exposure"
    p.fault_kind = "poisoned_memory"
    assert any("recall_note" in b for b in p.blockers())

    p.fault_kind = "wrong_value"
    assert not any("injection site" in b for b in p.blockers())
