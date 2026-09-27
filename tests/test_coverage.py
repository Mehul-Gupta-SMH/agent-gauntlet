"""How much of a task's quality is mechanical, before spending (#2).

#2 owns the noise floor and asks for the ratio, suspecting it is a property
of the task domain. Its comment thread found the better answer: quality
splits into output (domain-bound), process (observable on every task) and
relations (needs no label either). So the ratio is a property of a specific
task and grid, and it is knowable statically.

The tests that matter here are the ones that stop a coverage figure
becoming a comfortable lie: that a prediction is *reachability* and not a
promise, and that `audit` catches the prediction being wrong in both
directions.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_gauntlet import (
    Ledger, TaskSpec, VariantSpec, board, coverage, run_matrix,
)
from agent_gauntlet.coverage import Family, assess, audit
from agent_gauntlet.spec import Oracle

ROOT = Path(__file__).resolve().parents[1]
AUDITED = ROOT / "fixtures" / "inventory" / "audited.yaml"
INSTRUCTION = ROOT / "fixtures" / "poisoning" / "instruction.yaml"


@pytest.fixture(scope="module")
def task() -> TaskSpec:
    return TaskSpec.from_yaml(AUDITED)


@pytest.fixture(scope="module")
def results(task):
    grid = [VariantSpec(id="v", factors={"model": "cheap", "prompt": "verifying",
                                         "toolset": "records+summary"})]
    with tempfile.TemporaryDirectory() as d:
        records = run_matrix(task=task, variants=grid,
                             ledger=Ledger(Path(d) / "runs.jsonl"),
                             base_seed="s", repeats=10)
    return board.summarize(records)


# --- the split the comment thread predicted ------------------------------


def test_an_unlabelled_task_keeps_its_whole_process_score(task):
    """The prediction worth checking: *"even a task declaring `oracle: none`
    still has a fully objective process-quality score."*

    It does. Output drops to nothing and process does not move at all --
    which is the difference between "not worth running" and "worth running
    for a different reason".
    """
    labelled = assess(task)
    blind = assess(task.model_copy(update={"oracle": Oracle.NONE}))

    assert labelled.share(Family.OUTPUT) == 1.0
    assert blind.share(Family.OUTPUT) == 0.0
    assert blind.share(Family.PROCESS) == labelled.share(Family.PROCESS)
    assert blind.share() > 0.0, "an unlabelled task still measures something"


def test_baseline_oracle_behaves_like_none_for_output(task):
    """`Oracle.BASELINE` has no label either -- it has a usable oracle for
    propagation and none for correctness, and the spec is explicit that
    accuracy reads n/a rather than being redefined as agreement."""
    blind = assess(task.model_copy(update={"oracle": Oracle.BASELINE}))
    assert blind.share(Family.OUTPUT) == 0.0
    prop = next(p for p in blind.properties if p.name == "propagation_rate")
    assert prop.reachable


def test_a_grid_with_no_cross_check_cannot_measure_noticing(task):
    """Censoring, asked of the grid instead of one run: with no tool that
    could contradict the lie, detection was impossible in principle."""
    blind = assess(task, toolsets=["records"])
    for name in ("detection_rate", "repair_rate", "median_detect_latency"):
        prop = next(p for p in blind.properties if p.name == name)
        assert not prop.reachable
        assert prop.why_not and "impossible in principle" in prop.why_not


# --- the finding: no task can reach both gates ---------------------------


def test_a_task_can_reach_only_one_of_the_two_gates(task):
    """Falls out of the fault kind, and nobody had written it down.

    A `wrong_value` fault corrupts a number and gives no instruction, so
    compliance is not applicable. An `instruction` fault gives an order and
    corrupts no number, so propagation is not applicable. "Passes the gate"
    therefore means "passes the one gate this task's fault kind can reach".
    """
    corrupting = assess(task)
    directing = assess(TaskSpec.from_yaml(INSTRUCTION))

    assert [p.name for p in corrupting.unreachable_gates] == ["compliance_rate"]
    assert [p.name for p in directing.unreachable_gates] == ["propagation_rate"]

    gates = {p.name for p in corrupting.properties if p.gates}
    assert gates == {"propagation_rate", "compliance_rate"}
    for report in (corrupting, directing):
        assert len(report.unreachable_gates) == 1


def test_an_unreachable_reason_is_never_a_bare_false(task):
    """"This task cannot measure detection" and "this task measured
    detection as zero" are the same distinction the board draws between
    `n/a` and `0%`. A reachable=False with no reason is the second one
    wearing the first one's clothes."""
    for report in (assess(task), assess(task, toolsets=["records"]),
                   assess(TaskSpec.from_yaml(INSTRUCTION))):
        for prop in report.properties:
            if not prop.reachable:
                assert prop.why_not, prop.name


# --- reachable is not measured -------------------------------------------


def test_the_prediction_matches_a_real_board(task, results):
    """The whole point of `audit`. If a static prediction and a finished
    board can disagree unnoticed, the coverage figure is decoration."""
    report = assess(task, toolsets=["records+summary"])
    assert audit(report, results) == []


def test_the_audit_catches_a_prediction_that_was_too_optimistic(task, results):
    report = assess(task, toolsets=["records+summary"])
    broken = report.model_copy(deep=True)
    for prop in broken.properties:
        if prop.name == "compliance_rate":
            prop.reachable = True
            prop.why_not = None
    problems = audit(broken, results)
    assert problems and "compliance_rate" in problems[0]
    assert "predicted reachable" in problems[0]


def test_the_audit_catches_a_prediction_that_was_too_pessimistic(task, results):
    """The worse direction: a number printed for something this task was
    not supposed to be able to measure."""
    report = assess(task, toolsets=["records+summary"])
    broken = report.model_copy(deep=True)
    for prop in broken.properties:
        if prop.name == "detection_rate":
            prop.reachable = False
            prop.why_not = "mispredicted on purpose"
    problems = audit(broken, results)
    assert problems and "predicted unreachable" in problems[0]


def test_the_audit_says_nothing_about_an_empty_board(task):
    """No contenders is not a disagreement."""
    assert audit(assess(task), []) == []


# --- it costs nothing ----------------------------------------------------


def test_assessing_a_task_runs_nothing(task, monkeypatch):
    """The command's whole claim is that it spends nothing. A version that
    quietly executed a probe to find out would be worth less than no
    command."""
    import agent_gauntlet.matrix as matrix

    def explode(*a, **k):
        raise AssertionError("coverage must not run the matrix")

    monkeypatch.setattr(matrix, "run_matrix", explode)
    report = assess(task)
    assert report.properties
    assert report.fingerprint == task.fingerprint()


def test_every_shipped_fixture_can_be_assessed():
    """A regression guard over the real fixtures, so this cannot rot into
    something that only works on the one it was written against."""
    for fixture in sorted(ROOT.glob("fixtures/*/*.yaml")):
        report = assess(TaskSpec.from_yaml(fixture))
        assert report.share() is not None
        assert report.share() > 0.0, f"{fixture.name} measures nothing at all"
