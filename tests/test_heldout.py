"""The winner's curse (#20): never report a score the selection could see.

Picking the best of N on a set of runs and quoting that run's score reports
the maximum of N noisy estimates. It is biased upward by construction, and
the bias grows with the width of the search -- so the headline would look
best exactly when the search was widest and the noise worst.

These pin the split, and the case that actually matters: a winner chosen on
noise must be *visible* as one, not quietly reported as the answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board, run_matrix
from agent_gauntlet.faults import FaultSchedule
from agent_gauntlet.ledger import RunRecord
from agent_gauntlet.score import Outcome, Score

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "audited.yaml"
MODELS = {"cheap": "openai/gpt-4o-mini", "smart": "anthropic/claude-sonnet-5"}


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


@pytest.fixture
def run(task, tmp_path):
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    for seed in ("seed0", "seed1", "seed2"):
        run_matrix(task=task, variants=variants, ledger=ledger, base_seed=seed,
                   repeats=2)
    return ledger.records(), [v.id for v in variants if v.is_sentinel]


# --- the split ------------------------------------------------------------


def test_base_seed_is_the_replication_not_the_run():
    """`seed` is per-run so two variants never draw the same corruption.
    Independence is defined over the part before the first colon."""
    rec = _record("v", "seed7:v:small:0", accuracy=1.0)
    assert rec.base_seed == "seed7"


def test_split_holds_out_the_last_replication():
    sel, held = board.split_seeds(["seed2", "seed0", "seed1"])
    assert sel == ["seed0", "seed1"]
    assert held == ["seed2"]


def test_split_is_deterministic_from_the_ledger_alone():
    """A random split would make the reported score depend on something the
    run record does not carry."""
    a = board.split_seeds(["seed0", "seed1", "seed2"])
    b = board.split_seeds(["seed2", "seed1", "seed0"])
    assert a == b


def test_split_refuses_when_there_is_nothing_to_hold_out():
    with pytest.raises(ValueError, match="need more than"):
        board.split_seeds(["seed0"])


def test_one_seed_yields_no_held_out_winner(task, tmp_path):
    """And must not silently fall back to the biased number."""
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="only",
               repeats=2)
    assert board.held_out_winner(ledger.records()) is None


# --- what it reports ------------------------------------------------------


def test_the_reported_score_comes_from_runs_that_did_not_choose_it(run):
    records, sentinels = run
    held = board.held_out_winner(records, sentinels=sentinels)
    assert held is not None

    assert set(held.selection_seeds).isdisjoint(held.holdout_seeds), (
        "the held-out replications must take no part in the selection"
    )

    # The headline number must be computable from the held-out runs alone.
    only_held = board.summarize(
        [r for r in records if r.base_seed in set(held.holdout_seeds)]
    )
    row = next(r for r in only_held if r.variant_id == held.variant_id)
    assert held.holdout_score == pytest.approx(row.accuracy)


def test_optimism_is_the_gap_and_is_reported_not_hidden(run):
    records, sentinels = run
    held = board.held_out_winner(records, sentinels=sentinels)
    assert held.optimism == pytest.approx(
        held.selection_score - held.holdout_score
    )


def test_a_winner_that_does_not_hold_up_is_flagged():
    """The case the whole mechanism exists for.

    `lucky` is built to top the selection replications and collapse on the
    held-out one -- exactly what a search fitting noise produces. Reporting
    its selection score would be the winner's curse in its purest form.
    """
    records = []
    for seed, lucky, steady in (
        ("seed0", 1.00, 0.80),   # selection: lucky wins
        ("seed1", 1.00, 0.80),   # selection: lucky wins
        ("seed2", 0.10, 0.80),   # held out:  lucky collapses
    ):
        records.append(_record("lucky", f"{seed}:lucky:s:0", accuracy=lucky))
        records.append(_record("steady", f"{seed}:steady:s:0", accuracy=steady))

    held = board.held_out_winner(records)
    assert held is not None
    assert held.variant_id == "lucky", "it did win the selection half"
    assert held.selection_score == pytest.approx(1.00)
    assert held.holdout_score == pytest.approx(0.10)
    assert held.optimism > 0.8
    assert not held.held_up
    assert held.holdout_rank == 2

    # And the biased path would have reported something far rosier.
    naive = board.winner(board.summarize(records))
    assert naive.accuracy > held.holdout_score


def test_a_tied_selection_yields_no_winner_to_score():
    """A tied front is a real answer (#11); there is nothing to hold out."""
    records = []
    for seed in ("seed0", "seed1", "seed2"):
        records.append(_record("a", f"{seed}:a:s:0", accuracy=0.9))
        records.append(_record("b", f"{seed}:b:s:0", accuracy=0.9))
    assert board.held_out_winner(records) is None


def test_sentinels_cannot_win_the_selection(run):
    records, sentinels = run
    held = board.held_out_winner(records, sentinels=sentinels)
    assert held.variant_id not in sentinels


def _record(variant: str, seed: str, *, accuracy: float) -> RunRecord:
    return RunRecord(
        run_id=f"{variant}{seed}", task_id="t", task_fingerprint="f",
        variant_id=variant, factors={"prompt": variant},
        scenario_id="s", repeat=0, seed=seed, condition="clean",
        schedule=FaultSchedule.clean(seed),
        score=Score(
            correct=accuracy >= 0.999, accuracy=accuracy, outcome=Outcome.CLEAN,
            propagated=False, detected=False, surfaced=False, false_alarm=False,
            steps=3,
        ),
    )
