"""The fixture whose clean answer is not the sum of the world (#45).

Experiment 010 read both live matrices this project has run and found the
clean leaderboard was an eight-way tie on both: every contender answered
correctly every time its tools told the truth. Kendall's tau between the
clean ranking and the robustness ranking was not low, it was UNDEFINED --
a correlation needs two rankings and there was only one.

The cause was structural. Every contender tool set carries `list_records`
and `fetch_record`, and on every task written before this one,
enumerate-and-sum IS the clean answer.

Here the answer is a subset of the world. What is pinned below is the
arithmetic that keeps the fault oracle intact, and the two ways this
fixture could have failed: a clean ceiling at 1.00 (the old problem) and a
clean floor at 0.00 (the same problem upside down).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board, run_matrix
from agent_gauntlet.analyze import kendall_tau
from agent_gauntlet.offline import DISCONTINUED

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "inventory" / "discontinued.yaml"
MODELS = {"cheap": "m", "smart": "s"}


@pytest.fixture(scope="module")
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


@pytest.fixture(scope="module")
def results(task):
    with tempfile.TemporaryDirectory() as d:
        variants = architect.generate(out_dir=Path(d) / "v", task=task,
                                      models=MODELS)
        ledger = Ledger(Path(d) / "runs.jsonl")
        run_matrix(task=task, variants=variants, ledger=ledger,
                   base_seed="seed-a", repeats=30)
        rows = board.summarize(ledger.records())
    sentinel = {v.id for v in variants if v.is_sentinel}
    return [r for r in rows if r.variant_id not in sentinel]


def _stocked_total(scenario) -> int:
    return sum(v for k, v in scenario.records.items()
               if not k.endswith(DISCONTINUED))


# --- the oracle ----------------------------------------------------------


def test_expected_is_the_stocked_sum_in_every_scenario(task):
    """`expected` is hand-written in the YAML and nothing validates it
    against the records. A typo there would not fail loudly -- it would
    quietly grade every config against the wrong answer."""
    for s in task.scenarios:
        assert s.expected_total == _stocked_total(s), s.id


def test_every_audited_record_is_stocked(task):
    """This is what keeps the fault arithmetic unchanged.

    Faults land only in audited records, and the harness computes the
    credulous figure as `expected_total + total_delta` -- one unit of
    corruption shifting the answer by one unit. That holds only while every
    faultable record is one that counts toward the answer. An excluded
    record could be corrupted with no effect on the right total, and the
    harness would still expect one.
    """
    for s in task.scenarios:
        excluded = [a for a in s.audited_ids if a.endswith(DISCONTINUED)]
        assert not excluded, f"{s.id} audits excluded records: {excluded}"


def test_the_audit_is_never_the_answer(task):
    """Experiment 003's ceiling: when the cross-check covers everything it
    IS the answer, every variant that can reach it scores 1.00, and the
    gate measures nothing."""
    for s in task.scenarios:
        assert s.audited_total < s.expected_total, s.id


def test_summing_everything_is_wrong_where_it_should_be(task):
    """The clean-side step has to bite, and has to bite on more than one
    scenario -- noticing a single excluded line must not be enough."""
    biting = [s for s in task.scenarios
              if sum(s.records.values()) != s.expected_total]
    assert len(biting) >= 2
    assert any(len([k for k in s.records if k.endswith(DISCONTINUED)]) >= 2
               for s in biting), "no scenario splits the exclusion across lines"


def test_one_scenario_marks_nothing_so_the_clean_side_has_a_floor(task):
    """The load-bearing scenario, and the reason this fixture does not
    simply trade a ceiling for a floor.

    Without it, a config that never applies the exclusion scores exactly
    0.00 clean on every scenario -- and every such config ties at zero,
    which is as unrankable as every config tying at one.
    """
    unmarked = [s for s in task.scenarios
                if sum(s.records.values()) == s.expected_total]
    assert len(unmarked) >= 1


# --- what it was built to make possible ----------------------------------


def test_the_clean_ranking_exists(results):
    """The whole point. On both live matrices this number was 1."""
    clean = [r.clean_quality for r in results]
    assert all(c is not None for c in clean)
    assert len({round(c, 4) for c in clean}) > 1
    assert kendall_tau(clean, clean) is not None, "the clean ranking is rankable"


def test_the_clean_winner_is_not_the_robust_winner(results):
    """A fixture where one config tops both boards cannot show the two
    rankings diverging; a fixture where none can top both cannot show them
    agreeing. This grid contains configs for either outcome, so the
    comparison measures something instead of asserting it."""
    best_clean = max(results, key=lambda r: r.clean_quality)
    best_robust = max(results, key=lambda r: r.faulted_quality)
    assert best_clean.variant_id != best_robust.variant_id
    assert "reconciling" in best_robust.factors["prompt"]


def test_the_two_capabilities_sit_on_different_prompt_levels(task):
    """Put the clean step and the cross-check on one level and the two
    rankings agree by construction -- the mirror image of the ceiling this
    fixture removes, and just as useless."""
    assert task.grid is not None
    assert set(task.grid.prompts) == {"naive", "filtering", "verifying",
                                      "reconciling"}


def test_filtering_cannot_catch_a_lie(results):
    """One capability each: `filtering` reads the statement and has nothing
    to reconcile against, so it propagates."""
    rows = [r for r in results if r.factors["prompt"] == "filtering"]
    assert rows
    assert all(r.faulted_quality == 0.0 for r in rows)


# --- and does not disturb anything else ----------------------------------


def test_the_marker_is_a_no_op_on_every_other_fixture():
    """`verifying` and `reconciling` both filter. If any shipped fixture
    used this suffix by accident, their totals would silently change."""
    for fixture in sorted(ROOT.glob("fixtures/*/*.yaml")):
        if fixture == FIXTURE:
            continue
        spec = TaskSpec.from_yaml(fixture)
        for s in spec.scenarios:
            marked = [k for k in s.records if k.endswith(DISCONTINUED)]
            assert not marked, f"{fixture.name}/{s.id}: {marked}"
