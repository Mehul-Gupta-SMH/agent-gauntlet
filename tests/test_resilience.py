"""A matrix must survive one bad call (#14, #16).

Before this, an executor exception propagated out of `run_matrix`: the
matrix stopped, the failed run left no trace, and the ledger simply ended.
One transient provider error 40 calls into a 108-call matrix discarded the
other 68 -- on a live run that is real money, and nothing on disk said why.

Two rules here. Retry only what is worth retrying, and never let an
infrastructure failure read as an agent failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board, run_matrix
from agent_gauntlet.offline import naive

AUDITED = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "audited.yaml"
MODELS = {"cheap": "anthropic/claude-haiku-4-5", "smart": "anthropic/claude-sonnet-5"}


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(AUDITED)


def _run(task, tmp_path, executor, *, variants=1, repeats=1, **kw):
    vs = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=vs[:variants], ledger=ledger, base_seed="s",
               repeats=repeats, executor=executor, backoff=0, **kw)
    return ledger


# --- the matrix keeps going ----------------------------------------------


def test_one_bad_call_no_longer_discards_the_rest(task, tmp_path):
    calls = {"n": 0}

    def flaky(variant, seed):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("overloaded_error: the model is overloaded")
        return naive()

    ledger = _run(task, tmp_path, flaky, variants=3, repeats=2)
    records = ledger.records()

    assert len(records) == 3 * len(task.scenarios) * 2 * 2, (
        "every planned cell must still be attempted"
    )
    assert calls["n"] > 3, "the matrix continued past the failure"


def test_a_failed_run_leaves_a_record_saying_why(task, tmp_path):
    def always_fails(variant, seed):
        raise ModuleNotFoundError("No module named 'langchain_core'")

    ledger = _run(task, tmp_path, always_fails)
    errors = ledger.errors()

    assert errors, "a failed run must leave a trace"
    assert all("langchain_core" in (r.error or "") for r in errors)
    assert all(r.answer is None or r.answer.total is None for r in errors)


# --- retry only what is worth retrying -----------------------------------


def test_a_provider_error_is_retried(task, tmp_path):
    calls = {"n": 0}

    def twice_then_fine(variant, seed):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("overloaded_error: the model is overloaded")
        return naive()

    ledger = _run(task, tmp_path, twice_then_fine)
    first = ledger.records()[0]
    assert first.error is None, "it recovered"
    assert first.attempts == 3, "and the record says how many tries it took"


def test_a_missing_import_is_not_retried(task, tmp_path):
    """It will fail identically three times; retrying only wastes patience."""
    calls = {"n": 0}

    def broken(variant, seed):
        calls["n"] += 1
        raise ModuleNotFoundError("No module named 'langchain_core'")

    ledger = _run(task, tmp_path, broken)
    assert all(r.attempts == 1 for r in ledger.errors())
    assert calls["n"] == len(ledger.records()), "one attempt per cell, no more"


def test_retries_are_bounded(task, tmp_path):
    calls = {"n": 0}

    def never_recovers(variant, seed):
        calls["n"] += 1
        raise RuntimeError("overloaded_error: the model is overloaded")

    ledger = _run(task, tmp_path, never_recovers, max_attempts=2)
    assert all(r.attempts == 2 for r in ledger.errors())
    assert calls["n"] == 2 * len(ledger.records())


# --- an infra failure is not an agent failure ----------------------------


def test_errored_runs_are_censored_from_every_rate(task, tmp_path):
    """Counting them as wrong answers would make the board a measure of the
    weather rather than of the configuration."""
    calls = {"n": 0}

    def flaky(variant, seed):
        calls["n"] += 1
        if calls["n"] % 4 == 0:
            raise RuntimeError("overloaded_error: the model is overloaded")
        return naive()

    ledger = _run(task, tmp_path, flaky, repeats=2, max_attempts=1)
    records = ledger.records()
    assert ledger.errors(), "this test needs some failures to be meaningful"

    row = board.summarize(records)[0]
    assert row.n_errored == len(ledger.errors())
    assert row.n_runs == len(records) - row.n_errored, (
        "errored runs must not sit in the denominator"
    )

    # The same variant scored with the failures dropped by hand must agree.
    clean_only = board.summarize([r for r in records if not r.error])[0]
    assert row.accuracy == pytest.approx(clean_only.accuracy)
    assert row.quality == pytest.approx(clean_only.quality)


def test_a_variant_that_only_ever_errored_is_not_scored_at_all(task, tmp_path):
    """Not 0% -- absent. A rate over zero measurements is not a rate."""
    def always_fails(variant, seed):
        raise ModuleNotFoundError("No module named 'langchain_core'")

    ledger = _run(task, tmp_path, always_fails)
    assert ledger.errors()
    assert board.summarize(ledger.records()) == []


# --- provenance ----------------------------------------------------------


def test_every_record_names_the_model_that_produced_it(task, tmp_path):
    """`factors` carries the alias; the alias is a label into a mapping that
    lives outside the record."""
    ledger = _run(task, tmp_path, lambda v, s: naive(), variants=9)
    records = ledger.records()

    assert records
    for r in records:
        assert r.model in MODELS.values(), f"{r.variant_id} has model={r.model!r}"
        assert r.model != r.factors.get("model"), "the alias is not the model"
