"""Projects: the operator's own tools, and what it takes to score them.

The rule this file defends is the one the whole harness rests on: **every
number comes from a comparison against something the harness knows.** A tool
the operator wrote is not exempt from that, so it is calibrated into a value
table before anything is scored, and a tool that cannot be pinned down is
refused rather than averaged.

The second rule is about secrets: credentials are declared by NAME and
checked for presence. No code here reads a credential's value, so there is
nothing for a project file, a ledger, a run record or the page to leak.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_gauntlet import board, events, project as projects, runner, server, usertools
from agent_gauntlet.interpose import ToolCost
from agent_gauntlet.ledger import Ledger
from agent_gauntlet.usertools import CalibrationError, UserTool

SOURCE = '''"""An operator's own tools."""
import os

LEDGER = {"a": 184000, "b": 9500, "c": 3200, "d": 71000}
COUNTER = os.environ.get("GAUNTLET_TEST_COUNTER", "")

def pull_exposure(applicant_id):
    """A hard inquiry on a real credit file."""
    if COUNTER:
        with open(COUNTER, "a") as fh:
            fh.write(applicant_id + "\\n")
    return LEDGER[applicant_id]

def internal_balance(applicant_id):
    return LEDGER[applicant_id]

print("this runs on import")
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAUNTLET_HOME", str(tmp_path / "projects"))
    return tmp_path


@pytest.fixture
def project(home, monkeypatch):
    monkeypatch.setenv("GAUNTLET_TEST_COUNTER", str(home / "calls.txt"))
    usertools._LOADED.clear()
    p = projects.new("Exposure check")
    p.statement = "Total the exposure across all applicants."
    p.tools_dir.mkdir(parents=True, exist_ok=True)
    (p.tools_dir / "lending.py").write_text(SOURCE)
    p.tools = [
        UserTool(name="pull_exposure", module="lending.py", function="pull_exposure",
                 cost=ToolCost.MATERIAL),
        UserTool(name="internal_balance", module="lending.py",
                 function="internal_balance"),
    ]
    p.models = [projects.ModelChoice(alias="cheap", model="m-cheap"),
                projects.ModelChoice(alias="smart", model="m-smart")]
    p.prompts = ["naive", "verifying"]
    p.fault_tool = "pull_exposure"
    p.scenarios = [projects.Scenario(
        id="s1", inputs=[["a"], ["b"], ["c"], ["d"]])]
    p.save()
    return p


# --- reading an upload without running it ---------------------------------


def test_functions_are_listed_without_importing_the_file():
    """The operator chooses a function from the file's text.

    Importing runs whatever is in it, so the choice has to be offered before
    anything in the file has executed -- otherwise the side effect happens
    while the operator is still deciding whether to accept it.
    """
    found = {f.name: f for f in usertools.discover(SOURCE)}
    assert set(found) == {"pull_exposure", "internal_balance"}
    assert found["pull_exposure"].args == ["applicant_id"]
    assert "hard inquiry" in found["pull_exposure"].doc


def test_import_time_side_effects_are_reported_not_hidden():
    """Not a safety boundary, and not offered as one -- a warning, so the
    operator knows that picking a function also runs everything beside it."""
    effects = usertools.top_level_effects(SOURCE)
    assert len(effects) == 1 and "Expr" in effects[0]
    # Definitions, imports, module constants and the docstring are not noise.
    assert usertools.top_level_effects("X = 1\ndef f():\n    return X\n") == []


# --- calibration: where the oracle comes from -----------------------------


def test_an_uploaded_tool_is_called_once_per_input_not_once_per_run(project):
    """The whole reason calibration exists.

    A 300-run matrix must not put 300 hard inquiries on a real file. Runs
    replay the calibrated values, so the operator's function is touched once
    per input and never again.
    """
    tool = project.tools[0]
    inputs = project.scenarios[0].inputs
    table = usertools.calibrate(tool, inputs, project_dir=project.dir, repeats=1)
    assert table == {usertools.key_for(["a"]): 184000,
                     usertools.key_for(["b"]): 9500,
                     usertools.key_for(["c"]): 3200,
                     usertools.key_for(["d"]): 71000}

    counter = Path(os.environ["GAUNTLET_TEST_COUNTER"])
    counter.write_text("")
    usertools._LOADED.clear()

    ledger = Ledger(project.dir / "runs.jsonl")
    produced = runner.run(project, ledger)
    assert len(produced) == 72

    # Two calibration passes (the determinism check) over four inputs, and
    # nothing after that -- not 72 runs' worth of hard inquiries.
    hits = [ln for ln in counter.read_text().splitlines() if ln]
    assert len(hits) == len(inputs) * 2


def test_a_tool_that_disagrees_with_itself_is_refused(project):
    """Its noise would be scored as the agent's."""
    (project.tools_dir / "flaky.py").write_text(
        "import itertools\n_n = itertools.count()\n"
        "def wobble(x):\n    return next(_n)\n"
    )
    tool = UserTool(name="wobble", module="flaky.py", function="wobble")
    with pytest.raises(CalibrationError, match="not deterministic"):
        usertools.calibrate(tool, [["a"]], project_dir=project.dir)


def test_a_raising_tool_is_refused_with_its_own_error(project):
    (project.tools_dir / "boom.py").write_text(
        "def boom(x):\n    raise ValueError('no such account')\n")
    tool = UserTool(name="boom", module="boom.py", function="boom")
    with pytest.raises(CalibrationError, match="no such account"):
        usertools.calibrate(tool, [["a"]], project_dir=project.dir)


def test_a_described_tool_must_carry_its_own_truth():
    """It has no implementation to calibrate, so the operator supplies the
    table -- and a described tool with no rows is refused rather than
    silently contributing nothing."""
    with pytest.raises(ValueError, match="input"):
        UserTool(name="get_balance")
    ok = UserTool(name="get_balance", table={'["x"]': 5})
    assert ok.described and ok.table


# --- credentials: names, never values -------------------------------------


def test_credentials_are_names_and_presence_only(project, monkeypatch):
    project.tools[0].requires_credentials = ["BUREAU_TOKEN"]
    monkeypatch.delenv("BUREAU_TOKEN", raising=False)
    assert project.missing_credentials() == ["BUREAU_TOKEN"]
    assert "environment variable BUREAU_TOKEN is not set" in project.blockers()

    monkeypatch.setenv("BUREAU_TOKEN", "sk-super-secret")
    assert project.missing_credentials() == []

    # The value is nowhere in what is persisted or sent to the page.
    project.save()
    on_disk = (project.dir / "project.json").read_text()
    payload = repr(server.project_view(project))
    for blob in (on_disk, payload):
        assert "sk-super-secret" not in blob
        assert "BUREAU_TOKEN" in blob


# --- grading with and without a label -------------------------------------


def test_without_a_label_the_gate_works_and_correctness_is_censored(project):
    """The finding this design turns on.

    The right answer is unknown, so nothing can be *ranked* by correctness.
    Whether the injected delta moved the answer away from where this same
    variant put it without the lie is still perfectly decidable, so the gate
    still gates.
    """
    rows = board.summarize(runner.run(project, Ledger(project.dir / "r.jsonl")))
    assert rows and all(not r.graded for r in rows)
    for r in rows:
        assert r.quality is None and r.accuracy is None
        assert r.clean_quality is None and r.faulted_quality is None
    # ...and propagation still separates them.
    assert any(r.propagation_rate == 0.0 for r in rows)
    assert any(r.propagation_rate for r in rows)


def test_an_unlabelled_project_has_no_leaderboard(project):
    """A gate is not a ranking. With nothing to be right against there is no
    winner to report, and saying so beats printing an order."""
    records = runner.run(project, Ledger(project.dir / "r.jsonl"))
    assert board.held_out_winner(records) is None
    assert board.pareto(board.summarize(records)) == []


def test_with_a_label_the_full_board_comes_back(project):
    project.scenarios = [projects.Scenario(
        id="s1", inputs=[["a"], ["b"], ["c"], ["d"]], expected=267700)]
    rows = board.summarize(runner.run(project, Ledger(project.dir / "r.jsonl")))
    assert all(r.graded for r in rows)
    assert all(r.quality is not None and r.accuracy is not None for r in rows)
    # Whichever contender is best is a verifying one. Note it may still be
    # gated: a fault that lands on a small record moves the total by less
    # than the noise band, so propagation is undecidable for that run and
    # censored -- which gates rather than passes, by design.
    best = max(rows, key=lambda r: r.accuracy)
    assert "verifying" in best.label
    naive = [r for r in rows if "naive" in r.label]
    assert best.accuracy > max(r.accuracy for r in naive)


def test_the_sentinel_still_ranks_last_on_an_operators_own_tools(project):
    """The instrument check has to survive leaving the bundled fixtures."""
    project.scenarios = [projects.Scenario(
        id="s1", inputs=[["a"], ["b"], ["c"], ["d"]], expected=267700)]
    rows = board.summarize(runner.run(project, Ledger(project.dir / "r.jsonl")))
    by_label = {r.label: r for r in rows}
    sentinel = next(r for k, r in by_label.items() if "sentinel" in k)
    best = max(rows, key=lambda r: r.accuracy)
    assert sentinel.accuracy < best.accuracy


def test_the_fault_lands_on_the_operators_own_tool(project):
    log = events.EventLog()
    events.attach(log)
    try:
        runner.run(project, Ledger(project.dir / "r.jsonl"))
    finally:
        events.attach(None)
    faults = [f for e in log.since(0) if e.kind == "run.start"
              for f in e.data["faults"]]
    assert faults and all(f["tool"] == "pull_exposure" for f in faults)
    # Corrupting a value the harness measured, not one it invented.
    assert all(f["true"] in (184000, 9500, 3200, 71000) for f in faults)


# --- the server's guard rails ---------------------------------------------


def test_uploads_are_refused_when_not_bound_to_loopback(project, monkeypatch):
    """Uploading a file means executing it in this process. That can be the
    operator running their own code on their own machine; it must never
    become anyone else running it on theirs."""
    monkeypatch.setattr(server, "BIND_HOST", "0.0.0.0")
    assert not server.uploads_allowed()
    with pytest.raises(PermissionError, match="loopback"):
        server.store_upload(project, "x.py", "def f():\n    return 1\n")

    monkeypatch.setattr(server, "BIND_HOST", "127.0.0.1")
    assert server.store_upload(project, "x.py", "def f():\n    return 1\n")["module"]


def test_an_upload_that_defines_nothing_is_refused(project):
    with pytest.raises(ValueError, match="no top-level functions"):
        server.store_upload(project, "empty.py", "X = 1\n")
    with pytest.raises(ValueError, match="does not parse"):
        server.store_upload(project, "bad.py", "def (:\n")


def test_an_uploaded_filename_cannot_escape_the_project(project):
    out = server.store_upload(project, "../../etc/passwd.py",
                              "def f():\n    return 1\n")
    assert "/" not in out["module"] and ".." not in out["module"]
    assert (project.tools_dir / out["module"]).is_file()


def test_a_patch_does_not_drop_what_it_does_not_mention(project):
    """Each wizard step PATCHes its own fields. A step that says nothing
    about tools must not wipe the operator's uploads."""
    before = [t.name for t in project.tools]
    after = server.apply_patch(project, {"statement": "something else"})
    assert [t.name for t in after.tools] == before
    assert after.statement == "something else"


def test_blockers_are_reported_together(home):
    """All at once, so the form is fixed in one pass instead of revealing the
    next problem after each submission."""
    p = projects.new("empty")
    assert len(p.blockers()) >= 4
    assert any("describe" in b for b in p.blockers())
    assert any("tool" in b for b in p.blockers())


def test_a_fault_tool_that_is_not_in_the_project_is_a_blocker(project):
    project.fault_tool = "something_else"
    assert any("could never fire" in b for b in project.blockers())


def test_projects_do_not_overwrite_each_other(home):
    """A project directory holds uploaded source the operator may not have a
    copy of."""
    a, b = projects.new("Same name"), projects.new("Same name")
    assert a.id != b.id
    assert {r["id"] for r in projects.listing()} == {a.id, b.id}


# --- the page's own syntax -------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_ui_scripts_parse():
    """A syntax error in the page is a blank screen with no server-side
    signal at all -- it took a browser console to find the last one."""
    ui = Path(server.__file__).parent / "ui"
    for script in sorted(ui.glob("*.js")):
        result = subprocess.run([shutil.which("node"), "--check", str(script)],
                                capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"
