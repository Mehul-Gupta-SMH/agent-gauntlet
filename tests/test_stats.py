"""Intervals and detectability (#33, #34).

Before this, every rate on the board was a bare point estimate and `n_runs`
was decoration. That made three separate claims unanswerable at once -- was
that a regression or a reseed, is A really better than B, 40% compliance
plus or minus what -- and all three are things this tool is positioned to
answer.

The tests that matter here are the ones about behaviour at the edges. A
statistics helper that is right in the middle and overconfident at 0% and
100% would be worse than none, because the edges are exactly where this
board spends its time.
"""

from __future__ import annotations

import pytest

from agent_gauntlet import board
from agent_gauntlet.stats import (
    Interval, bootstrap, detectable_difference, runs_needed, wilson,
)


# --- the edges, which is the whole reason for Wilson ----------------------


def test_a_rate_of_zero_does_not_get_a_zero_width_interval():
    """The normal approximation gives ± 0 at p = 0. A board printing
    "propagation 0% ± 0" after eight runs would be claiming certainty it has
    not got, dressed as rigour."""
    got = wilson(0, 8)
    assert got.value == 0.0
    assert got.low == 0.0
    assert got.high > 0.30, "eight clean runs do not prove a rate of zero"


def test_a_rate_of_one_is_equally_uncertain_from_below():
    got = wilson(8, 8)
    assert got.value == 1.0 and got.high == 1.0
    assert got.low < 0.70


def test_the_interval_narrows_as_runs_accumulate():
    widths = [wilson(n // 2, n).width for n in (8, 32, 128, 512)]
    assert widths == sorted(widths, reverse=True)
    assert widths[-1] < widths[0] / 3


def test_wilson_is_asymmetric_near_the_edges():
    """Reported as low/high rather than ±, because a symmetric width would
    misstate which direction the uncertainty runs."""
    got = wilson(1, 20)
    assert (got.high - got.value) > (got.value - got.low) * 2


def test_no_runs_means_no_interval():
    """The same censoring the values get: absent, never zero."""
    assert wilson(0, 0) is None
    assert bootstrap([]) is None
    assert detectable_difference(0) is None


def test_one_observation_constrains_nothing():
    got = bootstrap([0.9])
    assert (got.low, got.high) == (0.0, 1.0)
    assert "uninformative" in got.method


# --- the bootstrap --------------------------------------------------------


def test_the_bootstrap_stays_inside_the_range_it_is_told_about():
    """A t-interval would happily report bounds outside [0, 1] on a skewed
    sample. Every bound here is an actual resampled mean, so it cannot."""
    got = bootstrap([0.0, 0.0, 0.0, 0.0, 1.0])
    assert 0.0 <= got.low <= got.value <= got.high <= 1.0


def test_the_same_data_gives_the_same_interval_twice():
    """A confidence interval that moves when you look at it again is not one
    a gate can be built on."""
    data = [0.1, 0.4, 0.9, 0.3, 0.7]
    assert bootstrap(data, seed="x").model_dump() == bootstrap(data, seed="x").model_dump()
    assert bootstrap(data, seed="x").low != bootstrap(data, seed="y").low


# --- overlap --------------------------------------------------------------


def test_non_overlap_is_a_conservative_claim_of_difference():
    """Non-overlap implies a difference; overlap does NOT imply its absence.
    Used where the alternative is comparing two bare point estimates."""
    a, b = wilson(18, 20), wilson(2, 20)
    assert a.excludes(b) and b.excludes(a)

    c, d = wilson(11, 20), wilson(9, 20)
    assert not c.excludes(d)


# --- detectability --------------------------------------------------------


def test_the_detectable_difference_is_sobering_at_small_n():
    """The number that separates 'no regression' from 'not enough runs to
    see one'. Most deltas this board prints are inside it."""
    assert detectable_difference(8) > 0.60
    assert detectable_difference(54) == pytest.approx(0.27, abs=0.02)
    assert detectable_difference(324) < 0.12


def test_it_shrinks_with_the_square_root_of_n():
    assert detectable_difference(400) == pytest.approx(
        detectable_difference(100) / 2, rel=0.01)


def test_runs_needed_inverts_it():
    for effect in (0.05, 0.10, 0.25):
        n = runs_needed(effect)
        assert detectable_difference(n) <= effect + 1e-9
    assert runs_needed(0) is None


# --- reaching the board ---------------------------------------------------


def _records(tmp_path, fixture="fixtures/inventory/audited.yaml"):
    from pathlib import Path

    from agent_gauntlet import Ledger, TaskSpec, VariantSpec, run_matrix

    task = TaskSpec.from_yaml(Path(__file__).resolve().parents[1] / fixture)
    variants = [
        VariantSpec(id="verifying", factors={"prompt": "verifying",
                                             "toolset": "records+summary"}),
        VariantSpec(id="naive", factors={"prompt": "naive",
                                         "toolset": "records+summary"}),
    ]
    ledger = Ledger(tmp_path / "r.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="s",
               repeats=4)
    return ledger.records()


def test_every_reported_rate_carries_an_interval(tmp_path):
    rows = {r.variant_id: r for r in board.summarize(_records(tmp_path))}
    for row in rows.values():
        for key in ("quality", "propagation_rate", "detection_rate",
                    "false_alarm_rate"):
            value = getattr(row, key)
            if value is None:
                assert key not in row.intervals, (
                    f"{key} is n/a but carries a width -- a width for a value "
                    f"nobody measured is worse than none")
            else:
                assert key in row.intervals, f"{key} has no interval"


def test_an_interval_uses_the_same_denominator_as_its_value(tmp_path):
    """A width computed over a different subset than its value would look
    like it belonged, which is the dangerous kind of wrong."""
    rows = board.summarize(_records(tmp_path))
    for row in rows:
        scored = row.n_runs - row.n_errored
        halves = scored // 2          # each cell runs a clean and a faulted twin

        if row.propagation_rate is not None:
            ci = row.intervals["propagation_rate"]
            assert ci.value == pytest.approx(row.propagation_rate)
            # Faulted runs, minus the ones whose corruption fell inside the
            # noise band and left the denominator.
            assert ci.n == halves - row.n_propagation_undecidable

        if row.false_alarm_rate is not None:
            ci = row.intervals["false_alarm_rate"]
            assert ci.value == pytest.approx(row.false_alarm_rate)
            assert ci.n == halves          # false alarms are a clean-run rate

        if row.repair_rate is not None:
            assert row.intervals["repair_rate"].n == row.n_repair_eligible

        # Nothing may claim more evidence than the run produced.
        for ci in row.intervals.values():
            assert 0 < ci.n <= scored


def test_the_interval_says_what_it_ranges_over():
    """A width quoted without its scope is the same overclaim as an
    uncensored zero."""
    got = wilson(4, 10)
    assert "seed and repeat" in got.over
    assert "scenario" in got.over
