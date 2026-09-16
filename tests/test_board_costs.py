"""Cost and time-to-detect on the board (#11, #14, #16).

Both were measured from the first run and neither reached the output: a
"cost-aware leaderboard" that ranked on accuracy alone, and a project
founded on "how fast does it surface the fault" that never printed it.

The rule both share: **"not measured" must never render as a number.** An
unpriced run is not a free one, and a run that never detected has no
latency -- printing 0 for either would read as the best possible result.
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


def _record(variant, seed, *, latency=None, rollup=None, detected=False):
    return RunRecord(
        run_id=f"{variant}{seed}", task_id="t", task_fingerprint="f",
        variant_id=variant, factors={"model": "cheap", "prompt": "naive"},
        scenario_id="s", repeat=0, seed=seed, condition="faulted",
        schedule=FaultSchedule.clean(seed),
        score=Score(
            correct=True, accuracy=1.0, outcome=Outcome.UNDETECTED_HARMLESS,
            propagated=False, detected=detected, surfaced=False,
            false_alarm=False, steps=5, detect_latency=latency,
            evidence_available=True,
        ),
        rollup=rollup or {},
    )


# --- time to detect (#16) -------------------------------------------------


def test_latency_is_none_when_nothing_detected_never_zero():
    """0 would read as 'noticed instantly' -- the opposite of the truth."""
    rows = board.summarize([_record("v", "s0"), _record("v", "s1")])
    assert rows[0].median_detect_latency is None
    assert rows[0].n_detect_latency == 0


def test_undetected_runs_do_not_drag_the_median_down():
    """Right-censored, not fast. Averaging them in rewards giving up."""
    rows = board.summarize([
        _record("v", "s0", latency=4, detected=True),
        _record("v", "s1", latency=6, detected=True),
        _record("v", "s2"),            # never detected
        _record("v", "s3"),            # never detected
    ])
    assert rows[0].median_detect_latency == pytest.approx(5.0)
    assert rows[0].n_detect_latency == 2, "the reader must see n, not just a median"


def test_the_board_reports_latency_for_a_detecting_variant(tmp_path):
    """End to end, so the column cannot silently stay empty."""
    task = TaskSpec.from_yaml(FIXTURE)
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="s", repeats=2)

    rows = board.summarize(ledger.records())
    detecting = [r for r in rows if r.detection_rate]
    assert detecting, "no variant detected anything -- fixture problem"
    for r in detecting:
        assert r.median_detect_latency is not None
        assert r.n_detect_latency > 0


# --- cost honesty (#14) ---------------------------------------------------


def test_an_unpriced_run_is_not_a_free_one():
    rows = board.summarize([_record("v", "s0"), _record("v", "s1")])
    assert rows[0].cost_complete is False
    assert rows[0].cost_per_run is None, "None, not 0.0 -- nothing priced this"


def test_cost_and_tokens_come_from_the_rollup():
    rollup = {"llm_calls": {"cost_usd": 0.02, "total_tokens": 4609,
                            "cost_complete": True}}
    rows = board.summarize([
        _record("v", "s0", rollup=rollup), _record("v", "s1", rollup=rollup),
    ])
    assert rows[0].cost_usd == pytest.approx(0.04)
    assert rows[0].total_tokens == 9218
    assert rows[0].cost_complete is True
    assert rows[0].cost_per_run == pytest.approx(0.02)


def test_one_unpriced_run_makes_the_whole_total_a_floor():
    """Partial pricing must not render as a confident number."""
    priced = {"llm_calls": {"cost_usd": 0.02, "total_tokens": 100,
                            "cost_complete": True}}
    rows = board.summarize([
        _record("v", "s0", rollup=priced),
        _record("v", "s1", rollup={"llm_calls": {"cost_usd": 0.02,
                                                 "cost_complete": False}}),
    ])
    assert rows[0].cost_complete is False
    assert rows[0].cost_per_run is None


def test_cost_is_an_axis_of_the_frontier_not_just_a_column():
    """A cheaper config that matches on accuracy must not be dominated."""
    expensive = {"llm_calls": {"cost_usd": 1.0, "total_tokens": 10,
                               "cost_complete": True}}
    cheap = {"llm_calls": {"cost_usd": 0.01, "total_tokens": 10,
                           "cost_complete": True}}
    rows = board.summarize([
        _record("pricey", "s0", rollup=expensive),
        _record("thrifty", "s0", rollup=cheap),
    ])
    front = {r.variant_id for r in board.pareto(
        rows, objectives=("accuracy", "cost_usd"), maximize=(True, False)
    )}
    assert front == {"thrifty"}, (
        "equal accuracy at 100x the price should not survive the frontier"
    )


# --- readability ----------------------------------------------------------


def test_the_label_drops_the_constant_factor_names():
    rows = board.summarize([_record("model-cheap__prompt-naive", "s0")])
    assert rows[0].label == "cheap naive"


def test_the_label_falls_back_to_the_id_when_there_are_no_factors():
    rec = _record("bare", "s0")
    rec.factors = {}
    assert board.summarize([rec])[0].label == "bare"
