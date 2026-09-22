"""Regression checking (#35): comparing a run against a certificate.

The claim this module has to earn is the one post 002 says out loud is
unearned: that this tool can notice an agent quietly getting worse.

The tests that matter are the three verdicts, and especially the third.
PASS and FAIL are easy. INCONCLUSIVE is the one that keeps a check honest --
a run too small to see the drop it is checking for must not report success,
because "I could not have caught it" and "it is fine" are different
statements and only one of them is true.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, VariantSpec, board, certify, run_matrix
from agent_gauntlet.certify import (
    Certificate, MetricVerdict, RegressionBudget, Verdict,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "audited.yaml"


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


def _run(task, tmp_path, name, prompt, *, repeats=30, seed="s"):
    """One variant, enough repeats that the resolution can see a big drop."""
    variants = [VariantSpec(id="candidate",
                            factors={"prompt": prompt, "toolset": "records+summary"})]
    ledger = Ledger(tmp_path / f"{name}.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed=seed,
               repeats=repeats)
    return board.summarize(ledger.records())


# --- the third verdict ----------------------------------------------------


def test_a_run_too_small_to_see_the_drop_will_not_say_pass(task, tmp_path):
    """The whole reason this module has three verdicts.

    Checking a 10% budget with runs that resolve 27% establishes nothing.
    Reporting PASS there would be the fail-green shape sitting directly
    under the claim the project most wants to make.
    """
    rows = _run(task, tmp_path, "small", "verifying", repeats=3)
    certs = certify.issue_all(rows, task_fingerprint=task.fingerprint())

    report = certify.compare(certs, rows,
                             budget=RegressionBudget(max_quality_drop=0.10))
    assert report.verdict is Verdict.INCONCLUSIVE
    assert "resolution" in report.reason or "resolve" in report.variants[0].reason


def test_the_same_runs_against_a_matching_budget_do_pass(task, tmp_path):
    """And it is not merely always inconclusive: give it a budget its runs
    can actually resolve and it concludes."""
    rows = _run(task, tmp_path, "ok", "verifying", repeats=30)
    certs = certify.issue_all(rows, task_fingerprint=task.fingerprint())

    report = certify.compare(certs, rows,
                             budget=RegressionBudget(max_quality_drop=0.50))
    assert report.verdict is Verdict.PASS


# --- catching a real degradation -----------------------------------------


def test_it_catches_a_configuration_that_got_worse(task, tmp_path, monkeypatch):
    """The claim the positioning rests on, exercised end to end.

    A config is certified, then the policy behind it is degraded -- the
    offline stand-in for somebody editing a prompt six weeks later -- and
    the check has to notice without being told where to look.
    """
    from agent_gauntlet import offline

    good = _run(task, tmp_path, "before", "verifying", repeats=30)
    certs = certify.issue_all(good, task_fingerprint=task.fingerprint())
    assert certs[0].metrics["propagation_rate"] == 0.0

    # The edit: `verifying` stops reconciling and just believes the tool.
    monkeypatch.setitem(offline.POLICIES, "verifying", offline.naive)
    after = _run(task, tmp_path, "after", "verifying", repeats=30, seed="s2")

    report = certify.compare(certs, after,
                             budget=RegressionBudget(max_quality_drop=0.20))
    assert report.verdict is Verdict.FAIL

    regressions = [
        c for v in report.variants for c in v.changes
        if c.verdict is MetricVerdict.REGRESSED and c.exceeds_budget
    ]
    assert regressions, "the degradation was not caught"
    assert any(c.metric == "propagation_rate" for c in regressions), (
        "propagation going 0% -> 100% is the regression that matters most")


def test_a_gate_metric_worsening_fails_whatever_the_budget(task, tmp_path):
    """A config that starts propagating has not degraded -- it has stopped
    doing the job. No budget forgives that."""
    rows = _run(task, tmp_path, "base", "verifying", repeats=30)
    certs = certify.issue_all(rows, task_fingerprint=task.fingerprint())

    worse = rows[0].model_copy(update={
        "propagation_rate": 1.0,
        "intervals": {**rows[0].intervals,
                      "propagation_rate": certify.Interval(
                          value=1.0, low=0.9, high=1.0, n=90, method="wilson")},
    })
    report = certify.compare(
        certs, [worse],
        # An enormous budget, which must not save it.
        budget=RegressionBudget(max_quality_drop=0.99, require_resolution=0.99),
    )
    assert report.verdict is Verdict.FAIL
    change = next(c for v in report.variants for c in v.changes
                  if c.metric == "propagation_rate")
    assert change.is_gate and change.exceeds_budget


def test_an_improvement_is_not_a_regression(task, tmp_path, monkeypatch):
    from agent_gauntlet import offline

    monkeypatch.setitem(offline.POLICIES, "verifying", offline.naive)
    bad = _run(task, tmp_path, "bad", "verifying", repeats=30)
    certs = certify.issue_all(bad, task_fingerprint=task.fingerprint())

    monkeypatch.undo()
    good = _run(task, tmp_path, "good", "verifying", repeats=30, seed="s2")
    report = certify.compare(certs, good,
                             budget=RegressionBudget(max_quality_drop=0.20))
    assert report.verdict is not Verdict.FAIL
    assert any(c.verdict is MetricVerdict.IMPROVED
               for v in report.variants for c in v.changes)


# --- the things that are not a pass --------------------------------------


def test_a_certified_configuration_that_vanished_is_not_a_pass(task, tmp_path):
    """An unanswered question, not a clean bill of health."""
    rows = _run(task, tmp_path, "base", "verifying", repeats=30)
    certs = certify.issue_all(rows, task_fingerprint=task.fingerprint())
    certs.append(Certificate(variant_id="ghost",
                             task_fingerprint=task.fingerprint(),
                             metrics={"quality": 1.0}))

    report = certify.compare(certs, rows,
                             budget=RegressionBudget(max_quality_drop=0.50))
    assert report.verdict is Verdict.INCONCLUSIVE
    assert "ghost" in report.reason


def test_overlapping_intervals_are_inside_noise_not_unchanged(task, tmp_path):
    """"Unchanged" is a claim. Overlap is the absence of one."""
    rows = _run(task, tmp_path, "a", "verifying", repeats=30)
    certs = certify.issue_all(rows, task_fingerprint=task.fingerprint())
    again = _run(task, tmp_path, "b", "verifying", repeats=30, seed="s2")

    report = certify.compare(certs, again,
                             budget=RegressionBudget(max_quality_drop=0.50))
    verdicts = {c.verdict for v in report.variants for c in v.changes}
    assert MetricVerdict.INSIDE_NOISE in verdicts


def test_a_metric_only_one_side_measured_is_not_comparable(task, tmp_path):
    rows = _run(task, tmp_path, "base", "verifying", repeats=30)
    cert = certify.issue(rows[0], task_fingerprint=task.fingerprint())
    cert.metrics.pop("detection_rate", None)

    report = certify.compare([cert], rows,
                             budget=RegressionBudget(max_quality_drop=0.50))
    change = next(c for v in report.variants for c in v.changes
                  if c.metric == "detection_rate")
    assert change.verdict is MetricVerdict.NOT_COMPARABLE


# --- the certificate itself ----------------------------------------------


def test_a_certificate_carries_the_variant_fingerprint(task, tmp_path):
    """Without it, two different agents sharing a name compare as one --
    which is exactly the regression this exists to catch, since the usual
    cause is somebody editing a prompt."""
    rows = _run(task, tmp_path, "base", "verifying", repeats=30)
    cert = certify.issue(rows[0], task_fingerprint=task.fingerprint(),
                         variant_fingerprint="abc123")
    assert cert.variant_fingerprint == "abc123"
    assert cert.task_fingerprint == task.fingerprint()
    assert cert.resolution is not None and cert.n_runs > 0


def test_a_certificate_expires(task, tmp_path):
    """Robustness is a property of an agent against a world, and both move.
    An assertion with no date claims nothing has changed since."""
    old = Certificate(variant_id="v", task_fingerprint="f",
                      issued_at=(datetime.now(timezone.utc)
                                 - timedelta(days=45)).isoformat(timespec="seconds"))
    assert old.expired() and old.age_days() >= 45

    fresh = Certificate(variant_id="v", task_fingerprint="f")
    assert not fresh.expired()

    forever = Certificate(variant_id="v", task_fingerprint="f",
                          expires_after_days=None)
    assert not forever.expired()


def test_certificates_round_trip_through_disk(task, tmp_path):
    rows = _run(task, tmp_path, "base", "verifying", repeats=30)
    certs = certify.issue_all(rows, task_fingerprint=task.fingerprint(),
                              issued_by="tester")
    certify.save(certs, tmp_path / "cert.json")
    back = certify.load(tmp_path / "cert.json")

    assert [c.variant_id for c in back] == [c.variant_id for c in certs]
    assert back[0].issued_by == "tester"
    assert back[0].intervals.keys() == certs[0].intervals.keys()


def test_the_direction_table_covers_every_reported_rate():
    """A comparison that got a sign wrong would report a regression as an
    improvement, which is worse than no comparison. `false_alarm_rate` and
    `detection_rate` both end in `_rate` and point opposite ways."""
    known = certify.HIGHER_IS_BETTER | certify.HIGHER_IS_WORSE
    assert "false_alarm_rate" in certify.HIGHER_IS_WORSE
    assert "detection_rate" in certify.HIGHER_IS_BETTER
    assert certify.GATE_METRICS <= certify.HIGHER_IS_WORSE
    assert not (certify.HIGHER_IS_BETTER & certify.HIGHER_IS_WORSE), (
        "a metric cannot point both ways")


# --- the retry instinct, refused in the tooling (#27) ---------------------


def test_one_matrix_produces_each_cell_exactly_once(task, tmp_path):
    """The premise the refusal below rests on. A cell is fully determined
    by (variant, scenario, repeat, seed, condition), so seeds and repeats
    do not collide with each other."""
    from agent_gauntlet.ledger import duplicate_cells

    ledger = Ledger(tmp_path / "once.jsonl")
    variants = [VariantSpec(id="v", factors={"prompt": "naive",
                                             "toolset": "records+summary"})]
    for seed in ("seed0", "seed1"):
        run_matrix(task=task, variants=variants, ledger=ledger,
                   base_seed=seed, repeats=3)
    assert duplicate_cells(ledger.records()) == {}


def test_a_ledger_holding_a_cell_twice_is_refused(task, tmp_path, capsys):
    """The one place the testing analogy inverts.

    CI treats a flaky test as a defect to retry until green. Here an agent
    that succeeds 7 times in 10 IS 70% reliable, and that 70% is the
    measurement -- so re-running until the gate passes does not fix the
    config, it deletes the number. Averaging two attempts at one cell is
    that mistake made quietly, and #27 asks whether the instinct is
    prevented by tooling or by documentation nobody reads.
    """
    from agent_gauntlet.cli import _certify, _check, build_parser
    from agent_gauntlet.ledger import duplicate_cells

    ledger = Ledger(tmp_path / "twice.jsonl")
    variants = [VariantSpec(id="v", factors={"prompt": "naive",
                                             "toolset": "records+summary"})]
    # The same matrix, run again into the same ledger. Exactly what a
    # "just re-run it" reflex produces.
    for _ in range(2):
        run_matrix(task=task, variants=variants, ledger=ledger,
                   base_seed="seed0", repeats=2)

    dupes = duplicate_cells(ledger.records())
    assert dupes and all(n == 2 for n in dupes.values())

    rc = _certify(build_parser().parse_args(
        ["certify", str(FIXTURE), "--runs", str(ledger.path),
         "--out", str(tmp_path / "cert.json")]))
    assert rc == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "flaky agent is a 70%" in out
    # Refused before writing: a certificate is the bar a later run is held
    # to, and an averaged retry must not become the baseline.
    assert not (tmp_path / "cert.json").exists()

    # And the same refusal on the checking side, against a certificate
    # issued from a clean ledger.
    clean = Ledger(tmp_path / "clean.jsonl")
    run_matrix(task=task, variants=variants, ledger=clean,
               base_seed="seed0", repeats=2)
    assert _certify(build_parser().parse_args(
        ["certify", str(FIXTURE), "--runs", str(clean.path),
         "--out", str(tmp_path / "cert.json")])) == 0
    capsys.readouterr()

    rc = _check(build_parser().parse_args(
        ["check", str(tmp_path / "cert.json"), "--runs", str(ledger.path)]))
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().out


# --- gate on safety, report on quality (#27) ------------------------------


def _cert_and_row(quality_before, quality_after, prop_before, prop_after):
    """A certificate and a later board row, with intervals wide apart so
    the comparison is never short-circuited as noise."""
    from agent_gauntlet.stats import Interval

    def iv(v):
        return Interval(value=v, low=max(0.0, v - 0.02),
                        high=min(1.0, v + 0.02), n=200, method="wilson")

    cert = Certificate(
        variant_id="v", task_fingerprint="f", n_runs=200, resolution=0.05,
        metrics={"quality": quality_before, "propagation_rate": prop_before},
        intervals={"quality": iv(quality_before),
                   "propagation_rate": iv(prop_before)},
    )
    row = board.VariantResult(
        variant_id="v", factors={}, n_runs=200, quality=quality_after,
        accuracy=quality_after, clean_quality=quality_after,
        faulted_quality=quality_after, propagation_rate=prop_after,
        detection_rate=None, false_alarm_rate=0.0, cost_usd=0.0,
        mean_steps=1.0,
        intervals={"quality": iv(quality_after),
                   "propagation_rate": iv(prop_after)},
    )
    return cert, row


def test_a_quality_regression_fails_by_default():
    # The budget is above the run's own resolution (0.14 at n=200), or the
    # verdict would be INCONCLUSIVE before it was ever FAIL.
    cert, row = _cert_and_row(0.90, 0.50, 0.0, 0.0)
    report = certify.compare([cert], [row],
                             budget=RegressionBudget(max_quality_drop=0.20))
    assert report.verdict is Verdict.FAIL


def test_safety_only_reports_that_same_regression_without_failing():
    """The shape #27 argues for wiring into CI. A gate that blocks a merge
    queue at random gets switched off, and then nothing is gated --.
    including propagation."""
    cert, row = _cert_and_row(0.90, 0.50, 0.0, 0.0)
    report = certify.compare(
        [cert], [row],
        budget=RegressionBudget(max_quality_drop=0.20, gate_on_quality=False))
    assert report.verdict is Verdict.PASS

    # Reported, not hidden: the drop is still in the comparison, still
    # marked REGRESSED, and only `exceeds_budget` changes.
    change = next(c for c in report.variants[0].changes if c.metric == "quality")
    assert change.verdict is MetricVerdict.REGRESSED
    assert not change.exceeds_budget
    assert "reported, not gated" in change.note


def test_safety_only_still_fails_on_propagation():
    """The half that must not move. A config that starts propagating has
    stopped doing the job, and no mode forgives it."""
    cert, row = _cert_and_row(0.90, 0.90, 0.0, 0.40)
    report = certify.compare(
        [cert], [row],
        budget=RegressionBudget(max_quality_drop=0.20, gate_on_quality=False))
    assert report.verdict is Verdict.FAIL
    change = next(c for c in report.variants[0].changes
                  if c.metric == "propagation_rate")
    assert change.is_gate and change.exceeds_budget


def test_the_split_is_between_binary_and_statistical_metrics():
    """Which side each metric falls on, pinned. `propagation_rate` and
    `compliance_rate` are properties an agent either has or does not;
    quality is an estimate with a width."""
    assert certify.GATE_METRICS == {"propagation_rate", "compliance_rate"}
    assert "quality" not in certify.GATE_METRICS
    assert "accuracy" not in certify.GATE_METRICS
