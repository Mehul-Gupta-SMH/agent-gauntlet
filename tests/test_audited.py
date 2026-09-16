"""The hardened fixture: a cross-check that covers only part of the data.

Experiment 003's gate failed on a ceiling rather than on instability --
median tau 1.0 with top-1 stability 0%. `get_summary` covered every record,
so it *was* the answer: every variant that could reach it scored exactly
1.00, the top two tied, and no seed pair had a unique winner to agree on.

These pin the properties that removing the ceiling must not cost.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import (
    Answer, FaultSchedule, Ledger, TaskSpec, architect, board,
    run_context, run_matrix, score_run,
)
from agent_gauntlet.offline import naive, verifying

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "inventory"
AUDITED = FIXTURES / "audited.yaml"
ORIGINAL = FIXTURES / "task.yaml"
MODELS = {"cheap": "openai/gpt-4o-mini", "smart": "anthropic/claude-sonnet-5"}


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(AUDITED)


def _tools(name: str) -> set[str]:
    return set(architect.TOOLSETS[name])


# --- the ceiling is gone --------------------------------------------------


def test_the_audited_figure_is_not_the_answer(task):
    """The shortcut that used to score 1.00 must now be wrong.

    Every scenario has to leave something outside the audit, or the
    cross-check is the answer again and the ceiling comes straight back.
    """
    for s in task.scenarios:
        assert s.audited, f"{s.id} declares no partial coverage"
        assert s.audited_total != s.expected_total, (
            f"{s.id}: reporting the audited figure would still be correct"
        )
        assert s.audited_total < s.expected_total


def test_reporting_the_audited_figure_scores_wrong(task):
    """Stated as a score rather than as arithmetic."""
    scenario = task.scenario("partial")
    clean = FaultSchedule.clean("x")
    with run_context(scenario.records, clean, None, scenario.audited_ids) as ctx:
        score = score_run(
            task=task, scenario=scenario, schedule=clean, ctx=ctx,
            answer=Answer(total=scenario.audited_total),
        )
    assert not score.correct


def test_verifying_recovers_the_grand_total_not_the_audit(task):
    """Reconciliation has to carry the correction into the full total.

    The old policy reported the cross-check verbatim, which was right only
    because coverage was total. Under partial coverage that undercounts by
    the unaudited remainder -- a variant that looks careful and is wrong.
    """
    scenario = task.scenario("lopsided")
    sched = FaultSchedule.build(
        seed="x", records=scenario.records, targets=scenario.audited_ids
    )
    with run_context(
        scenario.records, sched, _tools("records+summary"), scenario.audited_ids
    ) as ctx:
        answer = verifying()
        score = score_run(
            task=task, scenario=scenario, schedule=sched, ctx=ctx, answer=answer
        )

    assert answer.total == scenario.expected_total
    assert answer.total != scenario.audited_total
    assert score.correct and score.detected and not score.propagated


# --- what the change must not cost ---------------------------------------


def test_faults_land_only_on_audited_records(task):
    """The fair-fault rule (#5, #16).

    A fault outside the audit contradicts nothing reachable, so it is
    undetectable in principle -- but the run would still look like one where
    detection was possible and score as a miss. Censoring has to stay a
    property of the tool set alone.
    """
    for scenario in task.scenarios:
        covered = set(scenario.audited_ids)
        assert covered != set(scenario.records), f"{scenario.id} audits everything"
        for i in range(60):
            sched = FaultSchedule.build(
                seed=f"s{i}", records=scenario.records, targets=scenario.audited_ids
            )
            for fault in sched.faults:
                assert fault.target_key in covered, (
                    f"{scenario.id}: fault on unaudited record {fault.target_key}"
                )


def test_naive_still_propagates_under_partial_coverage(task):
    """The failure the gauntlet exists to catch must survive the change."""
    scenario = task.scenario("lopsided")
    sched = FaultSchedule.build(
        seed="x", records=scenario.records, targets=scenario.audited_ids
    )
    with run_context(
        scenario.records, sched, _tools("records"), scenario.audited_ids
    ) as ctx:
        answer = naive()
        score = score_run(
            task=task, scenario=scenario, schedule=sched, ctx=ctx, answer=answer
        )
    assert score.propagated and not score.correct


def test_coverage_list_needs_the_figure_it_applies_to(task):
    """`list_audited_records` ships with `get_summary` or not at all."""
    for name, tools in architect.TOOLSETS.items():
        if "list_audited_records" in tools:
            assert "get_summary" in tools, f"{name} lists coverage with no figure"
    assert "list_audited_records" not in _tools("records")
    assert "list_audited_records" in _tools("records+summary")


# --- pre-registration -----------------------------------------------------


def test_the_bar_did_not_move_when_the_task_did():
    """The distinction that makes this not goalpost-moving.

    A failed gate is a reason to fix the measurement, which is a reason to
    change the *task*. It is never a reason to lower the bar. The thresholds
    must be byte-identical to the ones pre-registered before experiment 003;
    only the fingerprint may differ, and it must.
    """
    original = TaskSpec.from_yaml(ORIGINAL)
    hardened = TaskSpec.from_yaml(AUDITED)

    assert original.gate is not None and hardened.gate is not None
    assert hardened.gate == original.gate, "the pre-registered bar moved"
    assert hardened.gate.set_at == "2026-09-15"

    assert hardened.fingerprint() != original.fingerprint(), (
        "a different task must hash differently, or run records lie about "
        "which task they were judged against"
    )


def test_original_fixture_is_unchanged_by_the_new_machinery():
    """Backwards compatibility, and experiment 003's reproducibility.

    A scenario that declares no coverage is audited in full, exactly as
    before. If this hash moves, the record of what 003 actually ran is gone.
    """
    original = TaskSpec.from_yaml(ORIGINAL)
    assert original.fingerprint() == "5c9848747223eaa4"
    for s in original.scenarios:
        assert not s.audited
        assert s.audited_total == s.expected_total


def test_scenario_cannot_audit_a_record_that_does_not_exist():
    from agent_gauntlet.spec import Scenario

    with pytest.raises(ValueError, match="do not exist"):
        Scenario(id="s", records={"a": 1}, audited=["a", "ghost"])


# --- the board it produces ------------------------------------------------


def test_hardened_board_separates_the_top_two(task, tmp_path):
    """The specific defect: the gate cannot pass while the top two tie.

    Offline policies are not agents and this says nothing about whether the
    live gate will pass. It says the fixture is now capable of producing a
    ranking that could fail -- which the old one was not.
    """
    variants = architect.generate(
        out_dir=tmp_path / "v", task=task, models=MODELS
    )
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="s", repeats=2)
    results = board.summarize(ledger.records())
    for r in results:
        r.is_sentinel = r.variant_id in {v.id for v in variants if v.is_sentinel}

    tiers = board.rank(results)
    assert len(tiers[0]) == 1, f"top tier is still tied: {[r.variant_id for r in tiers[0]]}"
    assert tiers[-1][0].is_sentinel, "the sentinel must still rank last"

    effects = {e.factor: e for e in board.factor_effects(results)}
    assert effects["model"].spread > 0, (
        "the model axis moved 0 points in experiment 003; the fixture must "
        "leave it somewhere to show up"
    )
