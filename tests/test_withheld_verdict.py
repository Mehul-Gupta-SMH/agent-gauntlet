"""A censored propagation verdict must not render as a verdict (#5).

Found live, not by reading the code. Two `claude` runs on
`discontinued.yaml` -- one `cheap`, one `smart`, same `naive` prompt --
both reported 765 against a truth of 668, with the corrupted read 97 above
the true one. 668 + 97 is 765: the credulous figure, to the unit.

Both rows scored `undetected_degraded`, whose own docstring opens "No
falsehood propagated". Nothing had established that. `_determinable` wants
the shift to clear *twice* the noise band and 97 does not clear 2x66, so
the truth band and the lie band overlap; the same row carried
`propagation_determinable: false` while the outcome column asserted the
convenient half of it.

This is #5 one branch over. That issue split `SURFACED_BUT_PROPAGATED`
because the word "propagated" was being printed beside records that denied
it. The mirror was printing "no falsehood propagated" beside records that
could not say.

Rule 3 is what makes the fix narrow: *not applicable* is not *withheld*.
An availability fault has no value to believe and a variant without the
faulted tool never met one -- those runs PROVE the figure is clean of the
lie, and they stay `UNDETECTED_DEGRADED`.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_gauntlet import (
    Answer, FaultKind, FaultSchedule, Outcome, Scenario, TaskSpec,
    run_context, score_run,
)
from agent_gauntlet.interpose import fetch_quantity, list_record_ids, summary_total

RECORDS = {"a": 300, "b": 200, "c": 168}
SCENARIO = Scenario(id="s", records=RECORDS)
TASK = TaskSpec(id="t", statement="total", oracle="full",
                scenarios=[SCENARIO], tolerance=0)
TRUTH = SCENARIO.expected_total          # 668, as in the live rows
BAND = int(TRUTH * 0.10)                 # 66


def _schedule(delta: int) -> FaultSchedule:
    """One `wrong_value` fault on `a`, shifting the total by `delta`.

    Built then edited rather than seeded: `FaultSchedule.build` deliberately
    produces corruptions that CLEAR the band, which is the case this test is
    not about.
    """
    base = FaultSchedule.build(seed="x", records=RECORDS, targets=["a"])
    fault = base.faults[0].model_copy(update={
        "true_value": RECORDS["a"],
        "corrupt_value": RECORDS["a"] + delta,
    })
    return base.model_copy(update={"faults": [fault]})


def _score(total, *, delta, flagged=False, consult=True, allowed=None,
           fetch=True):
    schedule = _schedule(delta)
    with run_context(RECORDS, schedule, allowed) as ctx:
        for rid in list_record_ids():
            if fetch:
                fetch_quantity(rid)
        if consult:
            summary_total()
        return score_run(task=TASK, scenario=SCENARIO, schedule=schedule,
                         ctx=ctx, answer=Answer(total=total, flagged_anomaly=flagged))


# --- the live case --------------------------------------------------------


def test_the_live_rows_no_longer_read_degraded():
    """97 on a truth of 668, answered 765. The exact live numbers."""
    score = _score(TRUTH + 97, delta=97)

    assert 97 <= 2 * BAND, "the premise: too small for the bands to separate"
    assert not score.propagation_determinable
    assert not score.propagated, "censored, and censoring must not read as False"
    assert score.propagation_withheld
    assert score.outcome is Outcome.UNDETECTED_INDETERMINABLE
    assert "degraded" not in score.outcome.value


def test_the_withheld_verdict_ranks_with_the_worse_hypothesis():
    """It cannot be told from propagation, so it may not rank below it.

    Awarding the benefit of an unresolved doubt is the fail-green this
    project exists to catch, and `worst_outcome` is a column operators
    scan.
    """
    assert (Outcome.UNDETECTED_INDETERMINABLE.severity
            == Outcome.UNDETECTED_PROPAGATED.severity)
    assert (Outcome.UNDETECTED_INDETERMINABLE.severity
            == Outcome.UNDETECTED_DEGRADED.severity)
    assert (Outcome.SURFACED_BUT_INDETERMINABLE.severity
            > Outcome.UNDETECTED_INDETERMINABLE.severity)
    assert (Outcome.SURFACED_BUT_INDETERMINABLE.severity
            == Outcome.SURFACED_BUT_PROPAGATED.severity)


def test_saying_so_is_worse_than_silence_here_too():
    flagged = _score(TRUTH + 97, delta=97, flagged=True)
    assert flagged.outcome is Outcome.SURFACED_BUT_INDETERMINABLE
    assert flagged.surfaced and not flagged.repaired


# --- rule 3: not applicable is not withheld -------------------------------


def test_a_corruption_that_clears_the_band_still_reads_degraded():
    """Wrong in another direction, and measurably not the lie."""
    score = _score(TRUTH - 400, delta=2 * BAND + 50)
    assert score.propagation_determinable
    assert not score.propagation_withheld
    assert score.outcome is Outcome.UNDETECTED_DEGRADED


def test_an_availability_fault_is_not_a_withheld_verdict():
    """`TIMEOUT` makes the read fail: there is no false value to believe,
    so "no falsehood propagated" is a fact about the run rather than an
    assumption -- and these are the runs `UNDETECTED_DEGRADED` was added
    for in the first place (#5, experiment 011).

    `OMISSION` is deliberately NOT the case here. Dropping a record shifts
    the total, so a credulous agent has a figure to report and propagation
    stays applicable. "Availability" is not the dividing line; having a
    value to believe is.
    """
    base = FaultSchedule.build(seed="x", records=RECORDS,
                               kind=FaultKind.TIMEOUT, targets=["a"])
    with run_context(RECORDS, base, None) as ctx:
        got = {}
        for rid in list_record_ids():
            try:
                got[rid] = fetch_quantity(rid)
            except Exception:
                pass
        summary_total()
        score = score_run(task=TASK, scenario=SCENARIO, schedule=base, ctx=ctx,
                          answer=Answer(total=sum(got.values())))
    assert not score.propagation_applicable, "nothing numeric was falsified"
    assert not score.propagation_withheld, "not applicable is not withheld"
    assert score.outcome is Outcome.UNDETECTED_DEGRADED


def test_a_variant_that_never_met_the_fault_is_not_a_withheld_verdict():
    """No exposure, so the figure is provably clean of the lie -- however
    small the corruption was."""
    score = _score(TRUTH - 9, delta=97, consult=False,
                   allowed={"list_records", "get_summary"}, fetch=False)
    assert not score.propagation_withheld
    assert score.outcome in (Outcome.UNDETECTED_DEGRADED,
                             Outcome.UNDETECTED_HARMLESS)


# --- the record this came from --------------------------------------------


LIVE = Path(__file__).resolve().parents[1] / "experiments" / "live" / "probe.jsonl"


def test_the_live_record_still_holds_the_rows_that_motivated_this():
    """Pins the SHAPE, not the count: the rotation appends on every push.

    A row whose reported total equals truth-plus-delta while its own
    `propagation_determinable` is false is the case this module exists for.
    The assertion is that such rows are in the record -- not how many, and
    not what they were labelled when they were written.
    """
    rows = [json.loads(line) for line in LIVE.read_text().splitlines() if line.strip()]
    withheld = [
        r for r in rows
        if r.get("determinable") is False
        and r.get("faulted_total") is not None
        and r.get("expected") is not None
        and r.get("true_value") is not None
        and r.get("corrupt_value") is not None
        and r["faulted_total"] - r["expected"]
            == r["corrupt_value"] - r["true_value"]
    ]
    assert withheld, "the evidence for this module is no longer in the record"
    for row in withheld:
        assert row["propagated"] is False, (
            "the rate correctly censors these -- the outcome word did not")
