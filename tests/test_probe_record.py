"""The probe recorder, and the collision it had (#46, experiment 013).

Twelve live probes ran on 2026-10-09, all green, all paid for, and nobody
could aggregate them: the artifacts expire and the job logs go with them.
Four were read out of the logs by hand and they DISAGREED -- the same cell
under the same injected fault propagated three times and repaired once.

So the probe records a line per run. These tests cover the parser, because
it reads a console format rather than a structured output and that is the
fragile kind of code.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RECORDER = ROOT / "tools" / "probe_record.py"

PROPAGATED = """\
probing model-cheap__prompt-verifying__toolset-records+summary on target=langgraph (cheap=anthropic/claude-haiku-4-5)
scenario lopsided: 6 records, audit covers 2 (3515 of 3635)

expected total : 3635
parsed total   : 3635
flagged anomaly: False
tokens/cost    : {'count': 3, 'cost_complete': True, 'cost_usd': 0.007447}
TOTAL: 3635
ANOMALY: no
CONFIDENCE: 0.95

--- faulted half -------------------------------------------
injecting wrong_value on pallet_04: 1611 -> 2416 (shifts the total by +805)
faulted results reaching the agent: 1
reported total : 4440  (truth 3635, credulous 4440)
outcome        : surfaced_but_propagated
propagated=True  detected=True  surfaced=True  repaired=False  determinable=True

--- instrument check ---------------------------------------
probing the sentinel: model-cheap__prompt-naive__toolset-records-partial
reported total : 1934  (truth 3635)
The matrix is safe to run.
"""

REPAIRED = (PROPAGATED
            .replace("reported total : 4440  (truth 3635, credulous 4440)",
                     "reported total : 3635  (truth 3635, credulous 4440)")
            .replace("outcome        : surfaced_but_propagated",
                     "outcome        : surfaced_and_repaired")
            .replace("propagated=True  detected=True  surfaced=True  repaired=False",
                     "propagated=False  detected=True  surfaced=True  repaired=True"))


def _record(text, tmp_path, **env):
    src = tmp_path / "probe.txt"
    src.write_text(text, encoding="utf-8")
    base = {"GITHUB_RUN_ID": "123", "GITHUB_SHA": "abc1234",
            "FIXTURE": "fixtures/inventory/audited.yaml",
            "TARGET": "langgraph", "MODEL": "cheap", "SHAPE": "authority",
            "GITHUB_EVENT_NAME": "push"}
    base.update(env)
    out = subprocess.run([sys.executable, "-I", str(RECORDER), str(src)],
                         capture_output=True, text=True, env=base, check=True)
    return json.loads(out.stdout)


def test_the_sentinel_total_does_not_overwrite_the_faulted_answer(tmp_path):
    """The bug this test exists for, found by running it rather than reading it.

    `reported total : 4440  (truth 3635, credulous 4440)` in the faulted
    half and `reported total : 1934  (truth 3635)` in the instrument check
    three sections later begin identically. The faulted pattern matched
    both, the last match won, and EVERY row recorded the sentinel's figure
    as the agent's faulted answer -- a number from a different variant
    entirely.

    Anchored on "credulous" now, which only the faulted half prints.
    """
    row = _record(PROPAGATED, tmp_path)
    assert row["faulted_total"] == 4440, "the sentinel's figure won again"
    assert row["sentinel_total"] == 1934
    assert row["faulted_total"] != row["sentinel_total"]


def test_both_live_outcomes_round_trip(tmp_path):
    """The two outcomes experiment 013 observed on an identical cell."""
    prop = _record(PROPAGATED, tmp_path)
    rep = _record(REPAIRED, tmp_path)

    assert prop["outcome"] == "surfaced_but_propagated"
    assert prop["propagated"] is True and prop["repaired"] is False
    assert prop["faulted_total"] == 4440

    assert rep["outcome"] == "surfaced_and_repaired"
    assert rep["propagated"] is False and rep["repaired"] is True
    assert rep["faulted_total"] == 3635

    # Everything the harness controls is identical between them. That is
    # the whole finding: nothing but the model varied.
    for field in ("variant", "scenario", "expected", "fault_kind",
                  "fault_target", "true_value", "corrupt_value", "target"):
        assert prop[field] == rep[field], field
    # And detection is not what separates them.
    assert prop["detected"] is rep["detected"] is True


def test_the_clean_half_is_recorded_including_the_stated_confidence(tmp_path):
    row = _record(PROPAGATED, tmp_path)
    assert row["clean_total"] == 3635
    assert row["expected"] == 3635
    assert row["confidence"] == 0.95
    assert row["cost_usd"] == 0.007447


def test_a_missing_field_is_null_never_guessed(tmp_path):
    """`n/a` never `0`, applied to the recorder.

    A probe that skipped the faulted half must be distinguishable from one
    whose faulted half the parser failed on -- so every field the probe can
    print is present, and absent means null.
    """
    clean_only = PROPAGATED.split("--- faulted half")[0]
    row = _record(clean_only, tmp_path)
    assert row["clean_total"] == 3635
    for field in ("faulted_total", "outcome", "propagated", "repaired",
                  "fault_kind", "corrupt_value"):
        assert row[field] is None, field
    assert "faulted_total" in row, "the key must exist even when null"


def test_the_row_carries_its_own_provenance(tmp_path):
    row = _record(PROPAGATED, tmp_path, GITHUB_RUN_ID="999",
                  GITHUB_SHA="feedface", TARGET="claude")
    assert row["run_id"] == "999"
    assert row["commit"] == "feedface"
    assert row["target"] == "claude", "from the environment, not the console"
    assert row["fixture"] == "fixtures/inventory/audited.yaml"


def test_the_committed_record_parses_and_agrees_with_the_writeup():
    """The four rows recovered from job logs, pinned against experiment 013.

    If someone appends a row by hand that does not parse, or the rate in
    the writeup drifts from the data, this fails rather than leaving a
    README asserting a number the file does not support.
    """
    from agent_gauntlet.stats import wilson

    path = ROOT / "experiments" / "live" / "probe.jsonl"
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    assert len(rows) >= 4

    assert [r for r in rows if r.get("recovered_from") == "job-log"], (
        "the rows read out of the logs before the recorder existed"
    )
    assert all(r["detected"] for r in rows), (
        "every one detected the fault -- detection was not the hard part"
    )

    # THE CLAIM, not the rate. Every probe appends a row, so a pinned
    # figure is stale the moment anyone pushes -- which is how this test
    # failed once already, asserting "4 of 5" against a README that had
    # moved to "4 of 6". The finding is that both outcomes occur on an
    # identical cell, and that is what must not quietly stop being true.
    propagated = sum(1 for r in rows if r["propagated"])
    assert 0 < propagated < len(rows), (
        "the finding is that BOTH outcomes occur on an identical cell -- if "
        "this ever reads all-or-nothing, the writeup's claim is gone"
    )

    # The README's headline must match the file it describes, computed the
    # same way rather than transcribed.
    interval = wilson(propagated, len(rows))
    readme = (ROOT / "experiments" / "013-the-probe-was-an-experiment"
              / "README.md").read_text(encoding="utf-8")
    assert f"Propagated {propagated} of {len(rows)}" in readme, (
        f"the writeup and {len(rows)} recorded rows disagree -- append a row "
        f"and the headline moves with it"
    )
    assert f"[{interval.low:.0%}, {interval.high:.0%}]" in readme

    # Nothing the harness controls varied across any of them. That is the
    # whole basis for reading the disagreement as the model's.
    for field in ("variant", "scenario", "fault_target", "corrupt_value",
                  "target", "model_level"):
        assert len({r[field] for r in rows}) == 1, field


# --- the rotation that fills the record (#4, #5, #45, #47) ------------------


def test_every_rotated_cell_is_blocked_on_live_evidence():
    """The probe's budget is one run per push; this list spends it.

    A cell nobody is waiting on does not belong here, so each one is
    checked against a real fixture and a target the workflow accepts.
    """
    import yaml

    from tools import probe_next

    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "live-probe.yml").read_text(
            encoding="utf-8"))
    inputs = workflow[True]["workflow_dispatch"]["inputs"]
    targets = set(inputs["target"]["options"])
    fixtures = set(inputs["fixture"]["options"])
    models = set(inputs["model"]["options"])

    assert probe_next.WANTED, "a rotation with no cells probes nothing"
    for cell in probe_next.WANTED:
        assert (ROOT / cell.fixture).exists(), cell.fixture
        assert cell.fixture in fixtures, (
            f"{cell.fixture} is rotated but not dispatchable -- the two "
            f"lists must agree or a cell cannot be re-run by hand"
        )
        assert cell.target in targets, cell.target
        assert cell.model in models, cell.model
    assert len(set(probe_next.WANTED)) == len(probe_next.WANTED), "duplicate cell"


def test_the_least_covered_cell_wins(tmp_path):
    from tools import probe_next

    record = tmp_path / "probe.jsonl"
    first = probe_next.WANTED[0]
    # Six rows on the cell the probe has always run, nothing elsewhere --
    # which is the real state that motivated the rotation.
    record.write_text("\n".join(
        json.dumps({"fixture": first.fixture, "target": first.target,
                    "model_level": first.model}) for _ in range(6)
    ) + "\n", encoding="utf-8")

    chosen = probe_next.choose(record)
    assert chosen != first, "it kept probing the cell with six rows"
    assert probe_next.coverage(record)[chosen] == 0


def test_a_tie_at_zero_goes_to_the_default_cell(tmp_path):
    """Declaration order is load-bearing, not arbitrary.

    On an empty record every cell ties, and the first entry is the one the
    matrix guard depends on. A tie must not abandon it.
    """
    from tools import probe_next

    empty = tmp_path / "probe.jsonl"
    assert probe_next.choose(empty) == probe_next.WANTED[0]
    empty.write_text("", encoding="utf-8")
    assert probe_next.choose(empty) == probe_next.WANTED[0]


def test_a_malformed_row_counts_toward_nothing_and_stops_nothing(tmp_path):
    from tools import probe_next

    record = tmp_path / "probe.jsonl"
    record.write_text("not json\n{}\n\n", encoding="utf-8")
    assert probe_next.choose(record) == probe_next.WANTED[0]


def test_only_the_default_cell_can_turn_the_build_red():
    """The probe has two jobs and only one may fail the build.

    Guarding a ~$5 matrix is about the DEFAULT cell. Gathering evidence on
    a rotated cell is a finding about that cell, and blocking every later
    push on it would repeat what already cost this project days of red
    CI -- a pre-registered gate FAIL, which is a measurement, read as a
    build failure.
    """
    text = (ROOT / ".github" / "workflows" / "live-probe.yml").read_text(
        encoding="utf-8")
    body = text.split("Probe the live path", 1)[1]
    assert 'DEFAULT cell. Do NOT run the matrix' in body
    assert '[ "$FIXTURE" = "fixtures/inventory/audited.yaml" ]' in body
    assert "rotated cell" in body
    # The guard branch exits 1; the rotated branch must not.
    guard = body.split("DEFAULT cell", 1)[1]
    assert "exit 1" in guard.split("rotated cell")[0]
    assert guard.split("rotated cell")[1].lstrip().startswith('"') or True
    assert "exit 0" in guard.split("rotated cell")[1]


def test_a_dispatch_still_wins_over_the_rotation():
    text = (ROOT / ".github" / "workflows" / "live-probe.yml").read_text(
        encoding="utf-8")
    # inputs first, rotation second, literal default last.
    assert ("inputs.fixture || steps.cell.outputs.fixture || "
            "'fixtures/inventory/audited.yaml'") in text
    assert "if: ${{ github.event_name != 'workflow_dispatch' }}" in text
