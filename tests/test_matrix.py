"""End-to-end: spec -> matrix -> ledger -> stability.

These exercise the full C0.1-C0.7 path offline. They prove the machinery
grades and aggregates correctly; they say nothing about real agents, whose
variance is the actual subject of the gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, VariantSpec, run_matrix, seed_for, stability
from agent_gauntlet.analyze import kendall_tau

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "task.yaml"

VARIANTS = [
    VariantSpec(id="v_naive", factors={"prompt": "naive", "model": "cheap"}),
    VariantSpec(id="v_verify", factors={"prompt": "verifying", "model": "cheap"}),
    VariantSpec(id="v_summary", factors={"prompt": "summary_only", "model": "cheap"}),
    VariantSpec(
        id="v_sentinel", factors={"prompt": "sentinel", "model": "cheap"},
        is_sentinel=True,
    ),
]


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


def test_fixture_loads_and_declares_a_full_oracle(task):
    assert task.oracle.value == "full"
    assert len(task.scenarios) == 3
    assert task.scenario("small").expected_total == 107


def test_fingerprint_changes_when_the_bar_changes(task):
    before = task.fingerprint()
    moved = task.model_copy(update={"tolerance": 5})
    assert moved.fingerprint() != before, "a looser bar must not reuse the old hash"


def test_matrix_runs_every_cell(task, tmp_path):
    ledger = Ledger(tmp_path / "runs.jsonl")
    records = run_matrix(
        task=task, variants=VARIANTS, ledger=ledger, base_seed="seedA", repeats=2
    )
    expected = len(VARIANTS) * len(task.scenarios) * 2 * 2  # x2 conditions
    assert len(records) == expected
    assert len(ledger.records()) == expected


def test_counterfactual_pairs_share_a_seed(task, tmp_path):
    """Robustness is degradation from a variant's *own* baseline, so the
    clean and faulted halves must differ only in the fault."""
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=VARIANTS[:1], ledger=ledger, base_seed="s", repeats=1)
    by_pair: dict[tuple, set[str]] = {}
    for r in ledger:
        by_pair.setdefault(r.pair_key, set()).add(r.seed)
    assert by_pair, "no runs recorded"
    for pair, seeds in by_pair.items():
        assert len(seeds) == 1, f"pair {pair} used more than one seed: {seeds}"


def test_every_variant_faces_identical_scenarios_and_seeds(task, tmp_path):
    """A fairness invariant: a ranking must not be an artifact of one
    variant drawing an easier scenario."""
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=VARIANTS, ledger=ledger, base_seed="s", repeats=1)
    by_variant: dict[str, set[str]] = {}
    for r in ledger:
        by_variant.setdefault(r.variant_id, set()).add(r.scenario_id)
    assert len({frozenset(v) for v in by_variant.values()}) == 1


def test_seeds_differ_per_variant():
    """Deliberate: two variants on the same scenario get different
    corruptions, so no variant can luck into an easier fault."""
    a = seed_for(base="b", variant="v1", scenario="s", repeat=0)
    b = seed_for(base="b", variant="v2", scenario="s", repeat=0)
    assert a != b


def test_ledger_round_trips(task, tmp_path):
    ledger = Ledger(tmp_path / "runs.jsonl")
    written = run_matrix(
        task=task, variants=VARIANTS[:2], ledger=ledger, base_seed="s", repeats=1
    )
    read_back = ledger.records()
    assert [r.run_id for r in read_back] == [r.run_id for r in written]
    assert read_back[0].schedule.seed == written[0].schedule.seed
    assert ledger.fingerprints() == {task.fingerprint()}


def _totals(ledger, condition=None) -> dict[str, float]:
    totals: dict[str, float] = {}
    for per_variant in ledger.scores_by_seed(condition=condition).values():
        for variant, value in per_variant.items():
            totals[variant] = totals.get(variant, 0.0) + value
    return totals


def test_sentinel_ranks_last_overall(task, tmp_path):
    """The instrument check (#29).

    If the board cannot rank a deliberately degraded variant last, no
    amount of seed analysis matters -- the measurement is broken.

    Ranked on overall correctness, across both conditions. See the test
    below for why the faulted-only view cannot do this job.
    """
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=VARIANTS, ledger=ledger, base_seed="s", repeats=3)
    totals = _totals(ledger)
    ranked = sorted(totals, key=lambda v: totals[v])
    assert ranked[0] == "v_sentinel", f"sentinel not last: {totals}"


def test_faulted_only_cannot_separate_credulous_from_broken(task, tmp_path):
    """A degeneracy worth pinning down rather than discovering later.

    Scored on correctness under fault alone, a competent-but-credulous
    variant and a deliberately broken one both sit at zero: one is wrong
    because it trusted a lie, the other because it is broken. The clean
    half of the counterfactual pair is what separates them, which is a
    concrete reason the headline metric cannot be faulted-only.
    """
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=VARIANTS, ledger=ledger, base_seed="s", repeats=3)
    faulted = _totals(ledger, condition="faulted")
    assert faulted["v_naive"] == faulted["v_sentinel"] == 0.0

    clean = _totals(ledger, condition="clean")
    assert clean["v_naive"] > clean["v_sentinel"], "the clean half must discriminate"


def test_verifier_beats_naive_across_the_matrix(task, tmp_path):
    """The positive control, at matrix scale rather than one run."""
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=VARIANTS, ledger=ledger, base_seed="s", repeats=3)
    faulted = [r for r in ledger if r.condition == "faulted"]
    rate = lambda vid: sum(  # noqa: E731
        r.score.correct for r in faulted if r.variant_id == vid
    ) / max(1, sum(1 for r in faulted if r.variant_id == vid))
    assert rate("v_verify") > rate("v_naive")


def test_propagation_is_confined_to_the_credulous_variant(task, tmp_path):
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=VARIANTS, ledger=ledger, base_seed="s", repeats=2)
    propagators = {r.variant_id for r in ledger if r.score.propagated}
    assert propagators == {"v_naive"}, propagators


def test_no_false_alarms_on_clean_runs(task, tmp_path):
    ledger = Ledger(tmp_path / "runs.jsonl")
    run_matrix(task=task, variants=VARIANTS, ledger=ledger, base_seed="s", repeats=2)
    assert not [r for r in ledger if r.score.false_alarm]


# --- the gate statistic ---------------------------------------------------


def test_kendall_tau_bounds():
    assert kendall_tau([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert kendall_tau([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)


def test_kendall_tau_undefined_rather_than_zero():
    """All-tied is 'not measured', not 'no association'. Returning 0.0 here
    would let a ceiling effect masquerade as a real (bad) correlation."""
    assert kendall_tau([1, 1, 1], [1, 2, 3]) is None
    assert kendall_tau([1], [1]) is None


def test_stability_reports_a_distribution(task, tmp_path):
    ledger = Ledger(tmp_path / "runs.jsonl")
    for seed in ("seedA", "seedB", "seedC"):
        run_matrix(
            task=task, variants=VARIANTS, ledger=ledger, base_seed=seed, repeats=2
        )
    by_seed: dict[str, dict[str, float]] = {}
    for record in ledger:
        base = record.seed.split(":")[0]
        bucket = by_seed.setdefault(base, {})
        if record.condition == "faulted":
            bucket[record.variant_id] = bucket.get(record.variant_id, 0.0) + float(
                record.score.correct
            )
    report = stability(by_seed)
    assert report.n_variants == len(VARIANTS)
    assert report.n_pairs == 3
    assert len(report.taus) == 3


def test_stability_rejects_mismatched_variant_sets():
    with pytest.raises(ValueError, match="same variants"):
        stability({"a": {"v1": 1.0, "v2": 0.0}, "b": {"v1": 1.0}})


def test_stability_needs_two_seeds():
    with pytest.raises(ValueError, match="at least two seeds"):
        stability({"only": {"v1": 1.0}})


def test_tied_top_is_not_a_winner():
    """Manufacturing agreement between seeds by breaking ties arbitrarily
    would inflate top-1 stability, the headline gate number."""
    report = stability(
        {"a": {"v1": 1.0, "v2": 1.0}, "b": {"v1": 1.0, "v2": 1.0}}
    )
    assert report.top1_stability == 0.0
