"""Steady-state hypotheses: falsifiable bounds instead of a score (#17).

Chaos engineering states the claim first and then tries to break it. What
is pinned here is mostly the ways that goes wrong quietly: a bound nothing
could have tested reading as a pass, a bound of exactly zero reading as
proof of zero, and a bound held by less than the runs could resolve reading
as a bound held.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, VariantSpec, run_matrix
from agent_gauntlet.hypotheses import (
    MAX_NAMED_RUNS, Verdict, check_all, evaluate,
)
from agent_gauntlet.spec import Bound, Hypothesis

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "inventory" / "discontinued.yaml"


@pytest.fixture(scope="module")
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


@pytest.fixture(scope="module")
def records(task):
    grid = [
        VariantSpec(id=f"{p}-{t}", factors={"model": "cheap", "prompt": p,
                                            "toolset": t})
        for p in ("naive", "reconciling") for t in ("records", "records+summary")
    ]
    with tempfile.TemporaryDirectory() as d:
        return run_matrix(task=task, variants=grid,
                          ledger=Ledger(Path(d) / "runs.jsonl"),
                          base_seed="seed-a", repeats=8)


def _of(records, variant_id):
    return [r for r in records if r.variant_id == variant_id]


# --- the spec refuses statements nothing could falsify --------------------


def test_a_hypothesis_with_no_bounds_is_rejected():
    with pytest.raises(ValueError, match="nothing can falsify"):
        Hypothesis(id="vibes", says="it is good", bounds=[])


def test_a_bound_with_neither_end_is_rejected():
    with pytest.raises(ValueError, match="neither max nor min"):
        Bound(metric="propagation_rate")


def test_detected_within_must_say_how_many_steps():
    with pytest.raises(ValueError, match="how many steps"):
        Bound(metric="detected_within", min=0.9)


# --- censoring, which is where this would fail quietly --------------------


def test_a_bound_nothing_could_test_is_not_a_pass(records):
    """`detected_within` has the detection denominator: a config whose tool
    set holds no cross-check could not have noticed at all. Scoring that
    as a late detection blames it for a capability it was never given, and
    scoring it as a pass credits it with one."""
    h = Hypothesis(id="notices", says="notices fast",
                   bounds=[Bound(metric="detected_within", steps=3, min=0.9)])
    result = evaluate(h, _of(records, "naive-records"), variant_id="naive-records")
    assert result.verdict is Verdict.NOT_TESTED
    assert result.bounds[0].n == 0
    assert result.bounds[0].observed is None, "an untested bound has no value"


def test_an_untested_clause_makes_the_whole_conjunction_untested(records):
    """"We checked and it held" is a different claim from "we checked what
    we could"."""
    h = Hypothesis(
        id="mixed", says="both",
        bounds=[Bound(metric="clean_quality", min=0.0),        # trivially held
                Bound(metric="detected_within", steps=3, min=0.9)],
    )
    result = evaluate(h, _of(records, "naive-records"), variant_id="naive-records")
    assert result.bounds[0].verdict is Verdict.UPHELD
    assert result.bounds[1].verdict is Verdict.NOT_TESTED
    assert result.verdict is Verdict.NOT_TESTED


def test_falsified_beats_untested(records):
    """A broken promise is a finding whatever else could not be checked."""
    h = Hypothesis(
        id="mixed", says="both",
        bounds=[Bound(metric="clean_quality", min=1.01),       # impossible
                Bound(metric="detected_within", steps=3, min=0.9)],
    )
    result = evaluate(h, _of(records, "naive-records"), variant_id="naive-records")
    assert result.verdict is Verdict.FALSIFIED


def test_an_unknown_metric_is_never_a_pass(records):
    h = Hypothesis(id="typo", says="...",
                   bounds=[Bound(metric="propogation_rate", max=0)])
    result = evaluate(h, records, variant_id="any")
    assert result.verdict is Verdict.NOT_TESTED
    assert "unknown metric" in result.bounds[0].says


# --- zero is falsifiable but never confirmable ---------------------------


def test_upholding_a_zero_bound_reports_its_ceiling(records):
    """"Never propagated, in 16 runs" is consistent with a true rate near
    20%. A bound of exactly zero can be falsified by one run and confirmed
    by none, so the report bounds the rate instead of ticking it."""
    h = Hypothesis(id="containment", says="never propagates",
                   bounds=[Bound(metric="propagation_rate", max=0)])
    # Constructed rather than hoped for: the unit under test is what the
    # report says when NOTHING propagated, so the runs handed in are the
    # ones where nothing did. A skip here would let the interesting branch
    # go unexercised on a seed that happens to propagate.
    quiet = [r for r in _of(records, "reconciling-records+summary")
             if not r.score.propagated]
    result = evaluate(h, quiet, variant_id="reconciling-records+summary")
    assert result.verdict is Verdict.UPHELD
    bound = result.bounds[0]
    assert bound.observed == 0.0
    assert bound.ceiling is not None and bound.ceiling > 0.0
    assert bound.n_offending == 0


def test_one_offending_run_falsifies_a_zero_bound(records):
    h = Hypothesis(id="containment", says="never propagates",
                   bounds=[Bound(metric="propagation_rate", max=0)])
    result = evaluate(h, _of(records, "naive-records"), variant_id="naive-records")
    assert result.verdict is Verdict.FALSIFIED
    assert result.bounds[0].n_offending >= 1


def test_the_offending_runs_are_named_but_capped(records):
    """The artifact #17 asks for is "propagated in 3 of 10 runs" with the
    three readable. Three hundred readable is a number again."""
    h = Hypothesis(id="containment", says="never propagates",
                   bounds=[Bound(metric="propagation_rate", max=0)])
    bound = evaluate(h, _of(records, "naive-records"),
                     variant_id="naive-records").bounds[0]
    assert 0 < len(bound.offending) <= MAX_NAMED_RUNS
    assert bound.n_offending >= len(bound.offending)
    ids = {r.run_id for r in _of(records, "naive-records")}
    assert set(bound.offending) <= ids, "named runs must be real runs"


# --- a bound held by a hair is not a bound held ---------------------------


def test_a_margin_under_the_resolution_is_flagged(records):
    """#34: at n runs a difference smaller than `detectable_difference(n)`
    was never observable, so a verdict resting on one is a coin flip."""
    runs = _of(records, "reconciling-records+summary")
    clean = [r for r in runs if r.condition == "clean"]
    observed = sum(float(r.score.correct) for r in clean) / len(clean)

    hair = Hypothesis(id="hair", says="...",
                      bounds=[Bound(metric="clean_quality",
                                    min=round(observed - 0.001, 4))])
    result = evaluate(hair, runs, variant_id="v")
    assert result.verdict is Verdict.UPHELD
    assert result.bounds[0].too_close_to_call


def test_a_wide_margin_is_not_flagged(records):
    runs = _of(records, "reconciling-records+summary")
    wide = Hypothesis(id="wide", says="...",
                      bounds=[Bound(metric="clean_quality", min=0.0)])
    bound = evaluate(wide, runs, variant_id="v").bounds[0]
    assert bound.verdict is Verdict.UPHELD
    assert not bound.too_close_to_call


# --- pre-registration and wiring -----------------------------------------


def test_the_fixture_pre_registers_its_hypotheses(task):
    assert len(task.hypotheses) == 3
    assert {h.id for h in task.hypotheses} == {
        "integrity-containment", "notices-and-does-not-cry-wolf",
        "steady-state-holds",
    }
    assert all(h.says.strip() for h in task.hypotheses)


def test_moving_a_bound_moves_the_fingerprint(task):
    """The whole pre-registration argument. A bound edited after seeing a
    result must not be able to hide."""
    before = task.fingerprint()
    moved = task.model_copy(update={
        "hypotheses": [
            task.hypotheses[0].model_copy(update={
                "bounds": [Bound(metric="propagation_rate", max=0.5)]}),
            *task.hypotheses[1:],
        ]
    })
    assert moved.fingerprint() != before


def test_declaring_none_leaves_the_hash_alone():
    """A new field that rehashes every spec that does not use it detaches
    historical run records -- the rule in TaskSpec.fingerprint."""
    assert TaskSpec.from_yaml(
        ROOT / "fixtures" / "inventory" / "audited.yaml"
    ).fingerprint() == "d11a0b2a6801ee74"


def test_check_all_covers_every_config_and_scores_nothing(task, records):
    results = check_all(task.hypotheses, records)
    variants = {r.variant_id for r in records}
    assert len(results) == len(variants) * len(task.hypotheses)
    assert not hasattr(results, "score")
    for r in results:
        assert r.verdict in set(Verdict)
