"""The recorded demo: a real run, replayed with nothing behind it.

The point of a public demo is that someone who cannot run the tool can see
what it does. That puts the whole weight on provenance: a mocked arena for
a project whose argument is *do not trust an output because it looks
reassuring* would refute the project on its own landing page.

So the rules these pin are about where the demo's numbers come from. The
stream is CAPTURED from a matrix that actually ran, the page composing it
is the SAME page the server serves, and the board row a replay shows is
built by the same function the live board uses -- censoring included.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, architect, board, events, replay, server
from agent_gauntlet.matrix import run_matrix

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "inventory" / "task.yaml"
DEMO = ROOT / "docs" / "demo"
UI = ROOT / "src" / "agent_gauntlet" / "ui"


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


def _matrix(task, tmp_path, *, seeds=2, repeats=1):
    variants = architect.generate(out_dir=tmp_path / "v", task=task,
                                  models={"cheap": "m"})
    ledger = Ledger(tmp_path / "runs.jsonl")
    records = []
    with replay.capture() as log:
        log.emit("intake.accepted", **replay.intake_event(
            task, variants, seeds=seeds, repeats=repeats))
        for i in range(seeds):
            records.extend(run_matrix(task=task, variants=variants,
                                      ledger=ledger, base_seed=f"s{i}",
                                      repeats=repeats))
        log.emit("board", **replay.board_event(records, seeds=seeds))
    return log, records


# --- captured, never reconstructed ----------------------------------------


def test_the_recording_is_the_run_that_happened(task, tmp_path):
    """One `run.end` per ledger record, and the ids match. A stream built
    from a template rather than from the matrix could not satisfy this."""
    log, records = _matrix(task, tmp_path)
    ends = [e for e in log.since(0) if e.kind == "run.end"]
    assert len(ends) == len(records)
    assert {e.data["run_id"] for e in ends} == {r.run_id for r in records}


def test_an_injected_value_in_the_recording_is_one_the_harness_injected(task, tmp_path):
    """The arena draws a strike only on a faulted call. If a recording
    could carry a faulted call the schedule does not know about, the demo
    would be showing a fault nobody injected."""
    log, records = _matrix(task, tmp_path)
    injected = {(e.data["run"], e.data["tool"], e.data["key"])
                for e in log.since(0)
                if e.kind == "tool.call" and e.data.get("faulted")}
    assert injected, "the fixture must land at least one fault"
    known = set()
    for rec in records:
        label = f"{rec.variant_id}|{rec.scenario_id}|{rec.repeat}|{rec.condition}"
        for fault in rec.schedule.faults:
            known.add((label, fault.tool_name, fault.target_key))
    assert injected <= known


def test_nothing_is_recorded_unless_someone_is_listening(task, tmp_path):
    """The feature must be invisible to every run that did not ask for it."""
    assert events.active() is None
    architect.generate(out_dir=tmp_path / "v", task=task, models={"cheap": "m"})
    assert events.active() is None


def test_the_log_is_detached_even_when_the_run_raises():
    """A log left attached would keep collecting the next run's events, and
    a replay is one run."""
    with pytest.raises(ZeroDivisionError):
        with replay.capture():
            assert events.active() is not None
            1 / 0
    assert events.active() is None


# --- one composer, so the demo cannot drift from the product --------------


def test_the_server_and_the_recording_share_the_board_row():
    """Not "they agree" -- the same object. Two functions that happen to
    match today are two functions that can stop matching."""
    assert server._row is replay.row


def test_the_board_survives_the_round_trip_unchanged(task, tmp_path):
    log, records = _matrix(task, tmp_path)
    doc = replay.document(log, title="t", note="n", source="s")
    board_ev = [e for e in doc["events"] if e["kind"] == "board"][0]
    assert board_ev["rows"] == [replay.row(r) for r in board.summarize(records)]

    path = replay.write(tmp_path / "r.json", doc)
    again = [e for e in json.loads(path.read_text())["events"]
             if e["kind"] == "board"][0]
    assert again == board_ev


def test_an_unmeasured_rate_reaches_the_published_file_as_null():
    """The censoring rule, in the artifact that is actually served.

    `records-partial` cannot call the corrupted tool, so it has no
    propagation rate. A demo that published `0` there would print the one
    number this project exists to refuse -- and print it in the most
    flattering direction, on the page most people will ever see.
    """
    text = (DEMO / "replay-poisoned-memory.json").read_text(encoding="utf-8")
    doc = json.loads(text)
    rows = [e for e in doc["events"] if e["kind"] == "board"][0]["rows"]
    blind = [r for r in rows if r["propagation_unmeasured"]]
    assert blind, "this grid has a contender that cannot see the fault"
    for row in blind:
        assert row["propagation_rate"] is None
        assert row["gated"], "an unmeasured gate metric gates"
    assert '"propagation_rate":null' in text


def test_the_file_is_json_one_event_per_line(tmp_path):
    doc = {"format": 1, "title": "t", "note": "n", "source": "s",
           "captured_at": "x", "events": [{"seq": 1, "kind": "a"},
                                          {"seq": 2, "kind": "b"}]}
    path = replay.write(tmp_path / "r.json", doc)
    assert json.loads(path.read_text()) == doc
    assert path.read_text().count('{"seq"') == 2
    assert len([l for l in path.read_text().splitlines() if l.startswith("  {")]) == 2


# --- the caption is part of the evidence ----------------------------------


def test_an_offline_recording_says_so(tmp_path):
    """A viewer who assumes they are watching a model is a viewer the demo
    has misled. Offline means scripted policies, and the caption says it in
    those words."""
    out = tmp_path / "r.json"
    subprocess.run(
        [sys.executable, "-m", "agent_gauntlet.cli", "run", str(FIXTURE),
         "--out", str(tmp_path / "runs"), "--repeats", "1", "--seeds", "1",
         "--replay", str(out)],
        cwd=ROOT, check=False, capture_output=True,
    )
    doc = json.loads(out.read_text())
    assert "no model was called" in doc["note"]
    assert "scripted offline policies" in doc["note"]
    # And the command that made it, so the claim is checkable.
    assert doc["source"].startswith("gauntlet run ")
    assert "--replay" in doc["source"]
    assert doc["format"] == replay.FORMAT


# --- the page ------------------------------------------------------------


def test_the_arena_reads_a_file_the_same_way_it_reads_a_poll():
    src = (UI / "arena.js").read_text(encoding="utf-8")
    assert "window.Arena = { begin, replay, state };" in src
    # It refuses a format it does not know rather than drawing half a run.
    assert "doc.format !== 1" in src


def test_the_demo_page_cannot_start_a_run():
    """A static page with an intake form is a page whose main control does
    nothing. The build removes it; this is the check that it stayed
    removed."""
    html = (DEMO / "index.html").read_text(encoding="utf-8")
    assert "/api/" not in html
    assert "wizard.js" not in html
    assert 'id="wizard"' not in html
    assert 'id="recordings"' in html


def test_the_demo_ships_the_real_ui_byte_for_byte():
    """Not a copy that has been "adjusted for the demo". The arena on the
    published page has to be the arena the tool runs, or the demo is
    showing something the product does not do."""
    for name in ("arena.js", "arena.css"):
        assert (DEMO / name).read_bytes() == (UI / name).read_bytes()


def test_the_committed_demo_matches_a_fresh_build():
    """The drift check. A UI edit that never reached docs/demo leaves the
    published page showing an older product than the repo describes."""
    proc = subprocess.run(
        [sys.executable, "tools/build_demo.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("name", ["replay-wrong-value.json",
                                  "replay-poisoned-memory.json"])
def test_a_published_recording_carries_no_credential(name):
    """These files are served to the public. Nothing in the event stream
    should ever carry a secret -- but a published artifact is exactly where
    that assumption deserves a test rather than a belief."""
    text = (DEMO / name).read_text(encoding="utf-8")
    assert not re.search(r"sk-[A-Za-z0-9_\-]{8,}", text)
    assert "API_KEY" not in text
    doc = json.loads(text)
    assert doc["format"] == replay.FORMAT
    assert doc["events"], "an empty recording is not a demo"


@pytest.mark.parametrize("name", ["replay-wrong-value.json",
                                  "replay-poisoned-memory.json"])
def test_a_published_recording_is_only_events_the_page_draws(name):
    """A published file should carry what the arena consumes and nothing
    else. An unknown kind is either a page that will ignore it or a leak
    nobody looked at."""
    doc = json.loads((DEMO / name).read_text(encoding="utf-8"))
    kinds = {e["kind"] for e in doc["events"]}
    assert kinds <= {"intake.accepted", "matrix.start", "run.start",
                     "tool.call", "run.end", "matrix.end", "board"}
    assert "board" in kinds
