"""Shapley attribution (#22).

The marginal table this replaces was wrong twice over: wrong under
interaction, and -- once #34 supplied the resolution -- reporting numbers
that were never distinguishable from zero. Both failure modes are pinned
here, because an attribution table that repeats them with better arithmetic
is the same defect in a more convincing wrapper.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, VariantSpec, attribution, board
from agent_gauntlet.attribution import attribute, _shapley
from agent_gauntlet.matrix import run_matrix

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "inventory" / "audited.yaml"

GRID = [
    VariantSpec(id=f"{m}-{p}-{t}",
                factors={"model": m, "prompt": p, "toolset": t})
    for m in ("cheap", "smart") for p in ("naive", "verifying")
    for t in ("records", "records+summary")
]


@pytest.fixture(scope="module")
def records():
    task = TaskSpec.from_yaml(FIXTURE)
    with tempfile.TemporaryDirectory() as d:
        return run_matrix(task=task, variants=GRID,
                          ledger=Ledger(Path(d) / "runs.jsonl"),
                          base_seed="seed-a", repeats=6)


# --- the axioms, on synthetic value functions -----------------------------
#
# Tested here rather than on a matrix because the point of an axiom is that
# it holds for every value function, and a fixture can only show one.


def test_efficiency_the_shares_add_to_the_whole_gap():
    """The property the one-at-a-time table does not have.

    Marginal effects add up to whatever they add up to; Shapley shares are
    guaranteed to account for the entire difference between the two
    configurations, which is what makes the table a decomposition rather
    than three loosely related numbers.
    """
    values = {
        frozenset(): 0.1, frozenset({"a"}): 0.2, frozenset({"b"}): 0.15,
        frozenset({"c"}): 0.3, frozenset({"a", "b"}): 0.7,
        frozenset({"a", "c"}): 0.35, frozenset({"b", "c"}): 0.4,
        frozenset({"a", "b", "c"}): 0.9,
    }
    shares = _shapley(["a", "b", "c"], values)
    assert sum(shares.values()) == pytest.approx(0.9 - 0.1)


def test_symmetry_two_interchangeable_factors_get_the_same_share():
    values = {
        frozenset(): 0.0, frozenset({"a"}): 0.3, frozenset({"b"}): 0.3,
        frozenset({"a", "b"}): 0.8,
    }
    shares = _shapley(["a", "b"], values)
    assert shares["a"] == pytest.approx(shares["b"])


def test_dummy_a_factor_that_changes_nothing_gets_nothing():
    """And gets exactly zero, not a small number.

    A factor that never moves the metric in any coalition must not collect
    credit for sitting next to one that does -- that is the whole objection
    to averaging."""
    values = {
        frozenset(): 0.2, frozenset({"a"}): 0.6,
        frozenset({"d"}): 0.2, frozenset({"a", "d"}): 0.6,
    }
    shares = _shapley(["a", "d"], values)
    assert shares["d"] == 0.0
    assert shares["a"] == pytest.approx(0.4)


def test_interaction_is_zero_when_factors_are_additive():
    """The interaction index has to be able to say 'nothing here'.

    An index that is always positive would make every grid look like it
    interacts, and the warning it drives would mean nothing."""
    values = {
        frozenset(): 0.0, frozenset({"a"}): 0.2, frozenset({"b"}): 0.3,
        frozenset({"a", "b"}): 0.5,
    }
    report = attribution._interactions(["a", "b"], values)
    assert report[0].index == pytest.approx(0.0)


# --- against the real grid ------------------------------------------------


def test_cells_agree_with_the_board_exactly(records):
    """An attribution table that disagrees with the board above it would be
    worse than no table, so the denominators are the board's, not a
    re-derivation of them."""
    rows = {r.variant_id: r for r in board.summarize(records)}
    report = attribute(records, metric="quality", resamples=1)
    assert report.refusal is None

    baseline_id = "-".join(report.baseline[f] for f in ("model", "prompt", "toolset"))
    target_id = "-".join(report.target[f] for f in ("model", "prompt", "toolset"))
    assert report.baseline_value == pytest.approx(rows[baseline_id].quality)
    assert report.target_value == pytest.approx(rows[target_id].quality)


def test_the_corners_are_chosen_by_name_not_by_score(records):
    """A gap measured between the best and worst cells is selected on the
    data it is then explained from -- the winner's curse (#20) in a
    different hat. Chosen by level name, the gap is whatever it is."""
    report = attribute(records, metric="quality", resamples=1)
    assert report.chose_corners_by_name
    assert report.baseline == {"model": "cheap", "prompt": "naive",
                               "toolset": "records"}
    assert report.target == {"model": "smart", "prompt": "verifying",
                             "toolset": "records+summary"}


def test_efficiency_holds_on_the_real_grid(records):
    report = attribute(records, metric="quality", resamples=1)
    assert report.explains_all_of_it


def test_prompt_and_toolset_interaction_exceeds_either_share(records):
    """The case #22 predicted and the grid actually contains.

    A `verifying` prompt is worth a great deal WITH a cross-check and
    nothing at all without one. Any attribution that hands prompt a single
    number without flagging this is repeating the marginal table's mistake
    in nicer notation."""
    report = attribute(records, metric="faulted_quality", resamples=1)
    pair = {frozenset(i.factors): i.index for i in report.interactions}
    prompt_toolset = pair[frozenset({"prompt", "toolset"})]
    biggest_share = max(abs(s.share) for s in report.shares)
    assert prompt_toolset > biggest_share

    prompt = next(s for s in report.shares if s.factor == "prompt")
    # Near zero without the cross-check, large with it. The baseline
    # coalition is the one where no factor has been switched on yet.
    assert prompt.conditional["(baseline)"] == pytest.approx(0.0, abs=0.05)
    assert prompt.conditional["toolset"] > 0.5


def test_a_share_under_the_resolution_is_flagged_as_unmeasured(records):
    """#34's lesson, enforced rather than remembered: the old table's
    15/15/10 points all sat under a 16.5% floor and were reported as
    quantities anyway."""
    report = attribute(records, metric="quality", resamples=1)
    assert report.resolution is not None
    model = next(s for s in report.shares if s.factor == "model")
    assert model.below_resolution(report.resolution)
    assert not model.below_resolution(None), "no resolution means no claim"


def test_every_share_carries_an_interval_containing_it(records):
    report = attribute(records, metric="quality", resamples=200)
    for share in report.shares:
        assert share.interval is not None
        assert share.interval.low <= share.share <= share.interval.high


def test_intervals_do_not_move_when_you_look_twice(records):
    """A confidence interval that changes between runs is not one."""
    a = attribute(records, metric="quality", resamples=200)
    b = attribute(records, metric="quality", resamples=200)
    assert [s.interval.low for s in a.shares] == [s.interval.low for s in b.shares]


# --- the refusals ---------------------------------------------------------


def test_censored_metrics_refuse_rather_than_impute(records):
    """`repair_rate` does not exist for a toolset with no cross-check --
    those runs are censored, not zero. Half the design therefore has no
    value at all, and there is no coalition to compare. Refusing is the
    only honest move; a zero there would say 'never repaired' about runs
    where repair was not askable."""
    report = attribute(records, metric="repair_rate", resamples=1)
    assert report.refusal is not None
    assert "not complete" in report.refusal
    assert not report.shares, "a refusal must not also hand back a table"


def test_an_incomplete_factorial_refuses(records):
    """Successive halving (#10) will produce exactly this: a design where
    the coalitions Shapley needs were never run."""
    pruned = [r for r in records
              if not (r.factors["model"] == "cheap"
                      and r.factors["prompt"] == "naive")]
    report = attribute(pruned, metric="quality", resamples=1)
    assert report.refusal is not None
    assert "not complete" in report.refusal


def test_three_levels_refuse_to_guess_the_corners(records):
    """With more than two levels there is no 'the other level', so the
    caller has to say which comparison they meant."""
    extra = [r.model_copy(update={"factors": {**r.factors, "prompt": "third"},
                                  "variant_id": r.variant_id + "-x"})
             for r in records[:4]]
    report = attribute(list(records) + extra, metric="quality", resamples=1)
    assert report.refusal is not None
    assert "two levels" in report.refusal


def test_unknown_metric_refuses(records):
    report = attribute(records, metric="vibes", resamples=1)
    assert report.refusal is not None
    assert "unknown metric" in report.refusal


def test_the_sentinel_is_excluded_when_named(records):
    """A deliberately broken configuration is not evidence about the factor
    levels it happens to carry.

    The real sentinel carries its own toolset level (`records-partial`), so
    leaving it in turns a two-level factor into a three-level one and the
    corners can no longer be chosen by name. Named as a sentinel, it leaves
    and the table is the same one the clean grid produces.
    """
    sentinel = [
        r.model_copy(update={
            "variant_id": "the-sentinel",
            "factors": {**r.factors, "toolset": "records-partial"},
        })
        for r in records if r.factors["toolset"] == "records"
    ]
    polluted = list(records) + sentinel

    assert attribute(polluted, metric="quality", resamples=1).refusal

    cleaned = attribute(polluted, metric="quality",
                        sentinel_ids=["the-sentinel"], resamples=1)
    baseline = attribute(records, metric="quality", resamples=1)
    assert cleaned.refusal is None
    assert [s.share for s in cleaned.shares] == [s.share for s in baseline.shares]


def test_removing_a_cell_refuses_rather_than_attributing_around_it(records):
    """Excluding runs is not a way to make an awkward cell go away."""
    report = attribute(records, metric="quality",
                       sentinel_ids=["cheap-naive-records"], resamples=1)
    assert report.refusal is not None
    assert "not complete" in report.refusal


def test_no_runs_at_all_refuses():
    assert attribute([], metric="quality").refusal == "no non-sentinel runs"
