"""The config detail view, and the curve it exists to show (#11).

#11's tension is frontier versus scalar. Its comment thread found a third
answer: the frontier is the index, not the product, and three of this
project's artifacts are hostile to a table -- hypothesis verdicts that are
deliberately not aggregated, a Shapley decomposition that belongs to the
grid rather than a row, and a detection curve that collapses to two numbers
only at a real loss.

The load-bearing test here is the curve's denominator. It KEEPS the runs
that never detected, which is the opposite of what `median_detect_latency`
must do, and getting that backwards would make a config that gives up
immediately look fast.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board, run_matrix
from agent_gauntlet.cli import main

ROOT = Path(__file__).resolve().parents[1]
AUDITED = ROOT / "fixtures" / "inventory" / "audited.yaml"
MODELS = {"cheap": "m", "smart": "s"}


@pytest.fixture(scope="module")
def ran(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("explain")
    task = TaskSpec.from_yaml(AUDITED)
    variants = architect.generate(out_dir=tmp / "v", task=task, models=MODELS)
    ledger = Ledger(tmp / "runs.jsonl")
    records = run_matrix(task=task, variants=variants, ledger=ledger,
                         base_seed="s", repeats=8)
    return tmp / "runs.jsonl", records, variants


# --- the curve ------------------------------------------------------------


def test_the_curve_keeps_the_runs_that_never_noticed(ran):
    """The opposite of what the median must do, and the reason the curve
    carries information the median cannot.

    A run that never detected is right-censored: the median has to drop it,
    because substituting a latency would reward an agent that gives up. The
    curve keeps it, because at every k it genuinely is "not yet detected" --
    which is why the curve's ceiling IS the detection rate rather than 1.0.
    """
    _, records, variants = ran
    blind = next(v for v in variants
                 if v.factors["prompt"] == "naive"
                 and v.factors["toolset"] == "records+summary")
    runs = [r for r in records if r.variant_id == blind.id]
    curve = board.detection_curve(runs)

    assert curve, "this config can reach a cross-check, so it is eligible"
    assert all(point.detected_by == 0.0 for point in curve)
    row = next(r for r in board.summarize(records) if r.variant_id == blind.id)
    assert row.detection_rate == 0.0
    assert curve[-1].detected_by == pytest.approx(row.detection_rate)


def test_the_curve_ceiling_is_the_detection_rate(ran):
    """Stated in the docstring, so it had better hold for a config that
    does detect."""
    _, records, variants = ran
    seeing = next(v for v in variants
                  if v.factors["prompt"] == "verifying"
                  and v.factors["toolset"] == "records+summary"
                  and v.factors["model"] == "smart")
    runs = [r for r in records if r.variant_id == seeing.id]
    curve = board.detection_curve(runs)
    row = next(r for r in board.summarize(records) if r.variant_id == seeing.id)

    assert curve[-1].detected_by == pytest.approx(row.detection_rate)
    assert curve[-1].detected_by > 0
    assert curve[0].step == 0, "the curve starts at the step evidence appeared"


def test_the_curve_is_monotone(ran):
    """P(noticed by k) cannot fall as k grows. A cumulative figure that
    dips is an arithmetic bug, and it would be invisible in a bar chart."""
    _, records, variants = ran
    for variant in variants:
        curve = board.detection_curve(
            [r for r in records if r.variant_id == variant.id])
        values = [p.detected_by for p in curve]
        assert values == sorted(values), variant.id


def test_a_config_that_could_not_notice_has_no_curve(ran):
    """Censoring, not a flat line at zero: with no reachable cross-check
    there were no eligible runs, and an empty curve says that where a line
    at 0% would say "looked and missed"."""
    _, records, variants = ran
    no_check = next(v for v in variants
                    if v.factors["toolset"] == "records")
    assert board.detection_curve(
        [r for r in records if r.variant_id == no_check.id]) == []


# --- the command ----------------------------------------------------------


def test_it_lists_what_the_ledger_holds(ran, capsys):
    path, _, _ = ran
    assert main(["explain", str(path)]) == 0
    out = capsys.readouterr().out
    assert "config(s)" in out
    assert "smart verifying records+summary" in out


def test_it_prints_intervals_not_points(ran, capsys):
    """#11's open item. A width beside all thirteen board columns is
    unreadable; beside one config it is the reason to open it."""
    path, _, _ = ran
    assert main(["explain", str(path),
                 "--variant", "smart verifying records+summary"]) == 0
    out = capsys.readouterr().out
    assert "with intervals" in out
    assert out.count("n=") >= 5, "every rate should carry its denominator"
    assert "P(noticed by step k)" in out
    assert "stated confidence was worth" in out


def test_an_ambiguous_variant_is_refused_rather_than_guessed(ran, capsys):
    path, _, _ = ran
    assert main(["explain", str(path), "--variant", "verifying"]) == 2
    assert "matches" in capsys.readouterr().out


def test_an_unknown_variant_is_refused(ran, capsys):
    path, _, _ = ran
    assert main(["explain", str(path), "--variant", "nonesuch"]) == 2
    assert "nothing" in capsys.readouterr().out


def test_hypotheses_from_a_different_task_are_refused(ran, capsys, tmp_path):
    """A bound from another task is not a bound on this one. The runs carry
    the fingerprint they were scored against, so this is checkable rather
    than trusted."""
    path, _, _ = ran
    other = ROOT / "fixtures" / "inventory" / "discontinued.yaml"
    assert main(["explain", str(path),
                 "--variant", "smart verifying records+summary",
                 "--task", str(other)]) == 0
    out = capsys.readouterr().out
    assert "NOT CHECKING HYPOTHESES" in out


def test_hypotheses_are_checked_when_the_task_matches(tmp_path, capsys):
    task_path = ROOT / "fixtures" / "inventory" / "discontinued.yaml"
    task = TaskSpec.from_yaml(task_path)
    variants = architect.generate(out_dir=tmp_path / "v", task=task,
                                  models=MODELS)
    run_matrix(task=task, variants=variants,
               ledger=Ledger(tmp_path / "runs.jsonl"), base_seed="s", repeats=4)

    assert main(["explain", str(tmp_path / "runs.jsonl"),
                 "--variant", "cheap naive records+summary",
                 "--task", str(task_path)]) == 0
    out = capsys.readouterr().out
    assert "the promises it was held to" in out
    assert "FALSIFIED" in out


def test_an_empty_ledger_is_refused(tmp_path, capsys):
    (tmp_path / "empty.jsonl").write_text("")
    assert main(["explain", str(tmp_path / "empty.jsonl")]) == 2
    assert "no runs" in capsys.readouterr().out
