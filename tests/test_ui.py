"""The UI layer, held to the same rule as the board it draws.

The page is allowed to choose pacing and layout. It is not allowed to
compute a score, invent a denominator, or turn an absent measurement into a
number -- so the server hands it exactly what `score_run` and
`board.summarize` produced, nulls included.

These pin the seams that would let that slip: the event stream's shape, the
grid derived from an operator's tool inventory, and the censoring surviving
serialization.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_gauntlet import Ledger, TaskSpec, VariantSpec, architect, board, events, run_matrix
from agent_gauntlet import server

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "audited.yaml"


@pytest.fixture
def log():
    lg = events.EventLog()
    events.attach(lg)
    yield lg
    events.attach(None)


# --- the stream is the run, not a retelling of it -------------------------


def test_nothing_is_emitted_when_nothing_is_listening(tmp_path):
    """The default path has to stay exactly as it was. A harness that pays
    for the UI on every CLI run would get the instrumentation removed."""
    events.attach(None)
    task = TaskSpec.from_yaml(FIXTURE)
    v = [VariantSpec(id="v", factors={"prompt": "naive", "toolset": "records"})]
    run_matrix(task=task, variants=v, ledger=Ledger(tmp_path / "r.jsonl"),
               base_seed="s", repeats=1)
    assert events.active() is None


def test_every_faulted_call_reaches_the_stream(log, tmp_path):
    """The arena draws a hit only from one of these. If the harness injected
    a fault the page never heard about, the animation would be showing a
    quieter run than the one that happened."""
    task = TaskSpec.from_yaml(FIXTURE)
    v = [VariantSpec(id="v", factors={"prompt": "naive", "toolset": "records"})]
    produced = run_matrix(task=task, variants=v, ledger=Ledger(tmp_path / "r.jsonl"),
                          base_seed="s", repeats=2)

    emitted = [e for e in log.since(0) if e.kind == "tool.call" and e.data["faulted"]]
    actual = [c for r in produced for c in r.tool_calls if c.get("faulted")]
    assert len(emitted) == len(actual) > 0
    assert [e.data["result"] for e in emitted] == [c["result"] for c in actual]


def test_the_stream_carries_the_oracle_the_agent_cannot_see(log, tmp_path):
    """Truth and the credulous figure are the harness's to know. The page
    shows them because they are what makes a judge unnecessary."""
    task = TaskSpec.from_yaml(FIXTURE)
    v = [VariantSpec(id="v", factors={"prompt": "naive", "toolset": "records"})]
    run_matrix(task=task, variants=v, ledger=Ledger(tmp_path / "r.jsonl"),
               base_seed="s", repeats=1)

    faulted = [e for e in log.since(0)
               if e.kind == "run.start" and e.data["condition"] == "faulted"]
    assert faulted
    for e in faulted:
        assert e.data["faults"]
        assert e.data["credulous"] != e.data["expected"]
        for f in e.data["faults"]:
            assert f["true"] != f["corrupt"]


def test_a_clean_run_carries_no_lie(log, tmp_path):
    """Its twin exists to be the baseline, and a baseline with a fault in it
    is not one."""
    task = TaskSpec.from_yaml(FIXTURE)
    v = [VariantSpec(id="v", factors={"prompt": "naive", "toolset": "records"})]
    run_matrix(task=task, variants=v, ledger=Ledger(tmp_path / "r.jsonl"),
               base_seed="s", repeats=1)
    for e in log.since(0):
        if e.kind == "run.start" and e.data["condition"] == "clean":
            assert e.data["faults"] == []
            assert e.data["credulous"] == e.data["expected"]


def test_the_cursor_never_replays_an_event(log):
    log.emit("a"); log.emit("b")
    first = log.since(0)
    assert [e.kind for e in first] == ["a", "b"]
    assert log.since(first[-1].seq) == []
    log.clear()
    log.emit("c")
    # After a clear the sequence keeps climbing, so a client polling across
    # the boundary cannot be handed an old event as a new one.
    assert log.since(first[-1].seq)[0].kind == "c"


# --- the grid the operator can actually supply ----------------------------


def test_only_toolsets_the_inventory_covers_may_compete():
    assert server.toolsets_within({"list_records", "fetch_record"}) == ["records"]
    full = set(architect.TOOLSETS["records+summary"])
    assert "records+summary" in server.toolsets_within(full)
    # The sentinel is never a contender the operator picks.
    assert architect.SENTINEL_TOOLSET not in server.toolsets_within(
        set(architect.TOOLSETS[architect.SENTINEL_TOOLSET])
    )


def test_an_intake_becomes_a_real_fingerprinted_task():
    task = server.task_from_intake({
        "statement": "Total the records.",
        "records": {"a": 10, "b": 20},
        "audited": ["a"],
        "tools": sorted(architect.TOOLSETS["records+summary"]),
        "prompts": ["naive", "verifying"],
        "fault_tool": "fetch_record",
    })
    assert task.fingerprint()
    assert task.scenarios[0].expected_total == 30
    assert task.acceptable_degradation["propagation_rate"] == 0
    assert "records+summary" in task.grid.toolsets


def test_an_intake_with_no_supplyable_toolset_is_refused():
    """`list_records` alone supplies nothing -- every tool set that contains
    it wants `fetch_record` too. A half-equipped inventory cannot field a
    contender, and saying so beats running a matrix of nobody."""
    assert server.toolsets_within({"list_records"}) == []
    with pytest.raises(ValueError, match="no tool set"):
        server.task_from_intake({
            "records": {"a": 1}, "tools": ["list_records"], "prompts": ["naive"],
            "fault_tool": "list_records",
        })


def test_an_intake_whose_fault_could_never_fire_is_refused(tmp_path):
    """The same instrument check the CLI has. A UI that renders a full arena
    for a matrix where the fault cannot land would be the gimmick this page
    is written against."""
    task = server.task_from_intake({
        "records": {"a": 1}, "audited": ["a"],
        "tools": sorted(architect.TOOLSETS["records"]),
        "prompts": ["naive"], "fault_tool": "pull_credit_report",
    })
    with pytest.raises(ValueError, match="could never fire"):
        architect.generate(out_dir=tmp_path, task=task,
                           models=server.DEFAULT_MODELS)


# --- censoring survives the trip to the browser ---------------------------


def test_an_unmeasured_rate_reaches_the_page_as_null(tmp_path):
    """Not zero, and not a string. The page prints `n/a` for null and has no
    branch that would turn one into a number -- but only because it is never
    handed one."""
    task = TaskSpec.from_yaml(
        Path(__file__).resolve().parents[1] / "fixtures" / "lending" / "applicant.yaml")
    variants = [VariantSpec(id="blind",
                            factors={"prompt": "bureau_deliberate",
                                     "toolset": "records+summary"})]
    ledger = Ledger(tmp_path / "r.jsonl")
    run_matrix(task=task, variants=variants, ledger=ledger, base_seed="s", repeats=1)

    row = server._row(board.summarize(ledger.records())[0])
    payload = json.loads(json.dumps(row))       # exactly what the page receives
    assert payload["propagation_rate"] is None
    assert payload["detection_rate"] is None
    assert payload["propagation_unmeasured"] is True
    assert payload["gated"] is True


def test_the_page_is_never_handed_a_selection_score_as_the_result():
    """`held_out_winner` reports both, and the page labels the selection one
    'not the number to quote'. The shape has to keep them separable."""
    src = (Path(server.__file__).parent / "ui" / "arena.js").read_text()
    assert "not the number to quote" in src
    assert "holdout_score" in src


def test_the_page_computes_no_rates_of_its_own():
    """A guard on the one rule that keeps the arena honest. If the page ever
    starts deriving a rate, it can disagree with the board underneath it."""
    src = (Path(server.__file__).parent / "ui" / "arena.js").read_text()
    for banned in ("propagated / ", "/ total", "Math.round(prop", "accSum / total"):
        assert banned not in src
