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


# --- the injector, drawn (#12 arena) --------------------------------------


def _arena_js() -> str:
    return (Path(server.__file__).parent / "ui" / "arena.js").read_text(encoding="utf-8")


def test_the_dragon_breathes_only_on_a_real_faulted_call():
    """It is the only figure on the page standing for a mechanism rather
    than a measurement, so it is bound to the one event that is allowed to
    draw a hit. An idle attack, or a flourish between runs, would be the
    page inventing a fault -- which is the single thing it must never do.
    """
    src = _arena_js()
    # Called from exactly one place, and that place is guarded by `faulted`.
    assert src.count("breatheAt(") == 2, "one definition, one call site"
    guard = src.index("if (ev.faulted) {")
    call = src.index("breatheAt(v, ev.fault_kind)")
    end = src.index("} else setFighterState(v, 'acting', 500);")
    assert guard < call < end, "the breath must sit inside the faulted branch"

    # Nothing schedules it on its own.
    for banned in ("setInterval", "Math.random"):
        assert banned not in src, f"the arena must not {banned}"


def test_the_page_is_told_which_lie_it_was_rather_than_guessing():
    """It used to decide a directive had arrived by searching the result
    for "SYSTEM NOTICE" -- true of exactly one of the five shapes, and
    silently false for the other four from the day the family landed."""
    src = _arena_js()
    # The check, not the comment that records why it is gone.
    assert "includes('SYSTEM NOTICE')" not in src
    assert "ev.fault_kind === 'instruction'" in src


def test_every_fault_kind_has_a_colour_and_they_come_from_the_event():
    src = _arena_js()
    for kind in ("wrong_value", "timeout", "instruction", "poisoned_memory"):
        assert f"{kind}:" in src, kind


def test_the_arena_has_somewhere_to_draw_it():
    html = (Path(server.__file__).parent / "ui" / "index.html").read_text(encoding="utf-8")
    # Under the fighters, over the floor: it must never hide a contender.
    assert html.index('id="dragon"') < html.index('id="fighters"')
    assert html.index('id="floor-marks"') < html.index('id="dragon"')
    # And the legend says what it is, in the same breath as what it means.
    assert "the injector" in html
    assert "breathes only when a" in html


def test_a_crowded_floor_gets_no_dragon():
    """Past three rings the floor belongs to the contenders, and a picture
    of a crowd with a dragon on top is a picture of neither."""
    src = _arena_js()
    assert "if (scale < 0.7) return;" in src
    assert "buildDragon(cx, cy, grid ? 0 : scale)" in src


def test_the_dragon_is_artwork_the_repo_can_regenerate():
    """Not a binary somebody once dropped in. The glyph is rendered from a
    font that ships with the container, by a committed script, on the same
    terms as `demo.gif`: if the asset is wrong, the generator is wrong, and
    both are in git.

    The hand-drawn version it replaced was three rendering bugs and,
    fairly, unrecognisable as a dragon.
    """
    ui = Path(server.__file__).parent / "ui"
    art = ui / "dragon.png"
    assert art.is_file() and art.stat().st_size > 2000

    tool = Path(server.__file__).resolve().parents[2] / "tools" / "make_dragon_png.py"
    assert tool.is_file()
    text = tool.read_text(encoding="utf-8")
    assert "U+1F409" in text or "1F409" in text
    # The licence question, answered where the asset is made.
    assert "Open Font License" in text


def test_the_published_demo_ships_the_dragon_too():
    """A page that references artwork it does not carry is a broken image
    on the one link anybody clicks."""
    demo = Path(server.__file__).resolve().parents[2] / "docs" / "demo"
    assert (demo / "dragon.png").read_bytes() == (
        Path(server.__file__).parent / "ui" / "dragon.png").read_bytes()
    assert "dragon.png" in _arena_js()


def test_the_flip_and_the_lunge_are_separate_groups():
    """A CSS transform replaces an SVG transform attribute outright rather
    than composing with it. Animating the group that carries the facing
    flip snapped the dragon back round mid-strike -- the same defect the
    comment at `strikeAt` exists for, made twice."""
    src = _arena_js()
    assert "wyrm-facing" in src and "wyrm-lunge" in src
    css = (Path(server.__file__).parent / "ui" / "arena.css").read_text(encoding="utf-8")
    assert ".wyrm-lunge { animation" in css
    assert ".wyrm-facing { animation" not in css


# --- harden: the page submits text, the verdict is harden's own ------------

AGENT = {"fixture": "inventory/audited.yaml",
         "instructions": "You add up stock across inventory records. Fetch "
                         "each record, sum the quantities, and report the "
                         "total. Be quick.",
         "tools": ["fetch_record", "list_records"], "model": "cheap",
         "seeds": 4, "repeats": 6}


def test_the_harden_form_can_only_name_a_fixture_never_a_path(tmp_path):
    """The request is a key into the fixture directory. A route that read
    whatever file it was pointed at would be an arbitrary read on a server
    that may sit on a shared machine."""
    for bad in ("../../etc/passwd", "/etc/passwd", "inventory/nope.yaml", ""):
        with pytest.raises(ValueError):
            server.harden_plan({**AGENT, "fixture": bad}, tmp_path)


def test_the_plan_is_free_and_runs_nothing(tmp_path, monkeypatch):
    """A plan imports the agent and names the candidates. It must not reach
    for an executor: offline cannot run an imported agent, and a plan that
    quietly did would be a paid run with a free label."""
    from agent_gauntlet import live

    def boom(*a, **k):
        raise AssertionError("a plan built an executor")

    monkeypatch.setattr(live, "live_executor", boom)
    plan = server.harden_plan(AGENT, tmp_path)
    assert plan["task"]["fingerprint"] == "d11a0b2a6801ee74"
    assert plan["gate"] == "propagation_rate" and plan["rounds"] == 1
    assert [c["name"] for c in plan["candidates"]] == ["cross_check"]
    assert plan["sizing"]["enough"] and plan["sizing"]["held_runs"] == 18
    assert plan["matrix_runs"] == 2 * 3 * 6 * 4 * 2


def test_a_missing_key_blocks_the_live_button_and_says_why(tmp_path, monkeypatch):
    """The page enables 'run' from `live_blockers` and from nothing it
    computed itself, so the button cannot be lit by a second opinion."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    plan = server.harden_plan(AGENT, tmp_path)
    assert any("ANTHROPIC_API_KEY" in b for b in plan["live_blockers"])


def test_an_undersized_round_is_blocked_in_the_plan_and_refused_live(tmp_path):
    small = {**AGENT, "repeats": 2}
    plan = server.harden_plan(small, tmp_path)
    assert not plan["sizing"]["enough"]
    assert any("cannot return HARDENED" in b for b in plan["live_blockers"])
    with pytest.raises(ValueError, match="too small"):
        server.start_harden({**small, "confirm": "spend"})
    assert not server.HARDEN.running()


def test_a_live_round_needs_the_spend_sentence_not_a_default():
    """The sentence carries the ceiling, so a stale or defaulted confirm
    cannot approve a number the operator did not read."""
    paid = {**AGENT, "budget_usd": 5}
    for confirm in (None, True, "spend", "spend 9.00"):
        with pytest.raises(ValueError, match='confirm "spend 5.00"'):
            server.start_harden({**paid, "confirm": confirm})
    assert not server.HARDEN.running()


def test_a_live_round_without_a_ceiling_is_refused_and_blocked_in_the_plan(
        tmp_path):
    """`runner.Budget` allows no ceiling only offline (#10). This route is
    never offline, and it shipped once without one: a browser button that
    could spend without limit."""
    for missing in ({}, {"budget_usd": 0}, {"budget_usd": -1},
                    {"budget_usd": "lots"}, {"budget_usd": None}):
        with pytest.raises(ValueError, match="spend ceiling"):
            server.start_harden({**AGENT, **missing, "confirm": "spend 0.00"})
    assert not server.HARDEN.running()
    plan = server.harden_plan(AGENT, tmp_path)
    assert plan["budget_usd"] is None
    assert any("spend ceiling" in b for b in plan["live_blockers"])
    assert server.harden_plan({**AGENT, "budget_usd": 5}, tmp_path)["budget_usd"] == 5.0


def test_a_harden_round_and_a_matrix_cannot_overlap(monkeypatch):
    """Two interleaving would make per-run timings measure contention."""
    monkeypatch.setattr(server.HARDEN, "status", "running")
    with pytest.raises(RuntimeError, match="already in progress"):
        server.start_run(server.Session(), {})
    with pytest.raises(RuntimeError, match="already in progress"):
        server.start_harden({**AGENT, "budget_usd": 5, "confirm": "spend 5.00"})


def test_an_unmeasured_metric_reaches_the_page_as_null(tmp_path):
    """Rule 2: `before`/`after` of None must cross as null, which the page
    prints n/a. A coerced 0 would read as 'never propagates'."""
    from agent_gauntlet import harden
    from agent_gauntlet.certify import MetricChange, MetricVerdict

    c = harden.Contrast(
        harden.NOT_SHOWN, metric="propagation_rate", reason="x",
        changes=[MetricChange(metric="propagation_rate", before=None,
                              after=None,
                              verdict=MetricVerdict.NOT_COMPARABLE)])
    rnd = harden.Round(baseline=None, findings=[], found=set(), candidates=[],
                       contrast=c)
    view = server.contrast_view(rnd)
    assert view["changes"][0]["before"] is None
    assert view["changes"][0]["after"] is None
    assert view["cost"] is None and view["exported"] is None


def test_a_live_round_is_refused_through_a_proxy_header(monkeypatch):
    """The key is the server's own. A tunnel to loopback must not be able
    to spend it; the bind alone does not say who is on the other end.

    The worker is stubbed so that a broken guard fails this test rather than
    starting a paid round on whoever's machine runs it."""
    monkeypatch.setattr(server, "_harden_worker", lambda *a, **k: None)
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_port)
        conn.request("POST", "/api/harden/run",
                     body=json.dumps({**AGENT, "budget_usd": 5,
                                      "confirm": "spend 5.00"}),
                     headers={"Content-Type": "application/json",
                              "X-Forwarded-For": "203.0.113.9"})
        resp = conn.getresponse()
        assert resp.status == 403
        assert server.HARDEN.status == "idle"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_page_scripts_declare_no_top_level_name_twice():
    """Classic scripts share one global scope, so a second top-level `const`
    of the same name is a SyntaxError that kills the WHOLE script -- the
    harden panel shipped once with an empty form because `pct` was already
    arena.js's. `node --check` parses each file alone and cannot see it."""
    import re

    ui = Path(server.__file__).parent / "ui"
    seen: dict[str, str] = {}
    for js in sorted(ui.glob("*.js")):
        for name in re.findall(
                r"^(?:async\s+)?(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)",
                js.read_text("utf-8"), flags=re.M):
            assert name not in seen, f"{name} is declared in {seen[name]} and {js.name}"
            seen[name] = js.name


def test_the_harden_sample_says_it_is_a_sample_and_its_gate_is_honest():
    """Sample data is the one place the page shows numbers nobody measured,
    so the file carries its own label (the page cannot forget it) and its
    headline must at least be a result the real Wilson test would call
    shown -- an illustration that `contrast` would have refused teaches the
    wrong thing. Unmeasured metrics stay null, as on a real board."""
    from agent_gauntlet import harden

    doc = json.loads((Path(server.__file__).parent / "ui"
                      / "sample-harden.json").read_text("utf-8"))
    assert doc["sample"] is True and "no model was called" in doc["sample_note"]
    gate = next(c for c in doc["changes"] if c["is_gate"])
    assert [c["metric"] for c in doc["changes"] if c["is_gate"]] == [
        "propagation_rate"]          # exactly one gate (rule 2d)
    assert harden.separates(gate["before"], gate["after"], 18)
    assert [c["before"] for c in doc["changes"]
            if c["verdict"] == "not_comparable"] == [None, None]
    assert doc["candidates"][0]["name"] == "cross_check"


def test_a_real_result_never_carries_the_sample_flag(tmp_path):
    from agent_gauntlet import harden
    from agent_gauntlet.certify import MetricChange, MetricVerdict

    c = harden.Contrast(harden.NOT_SHOWN, metric="propagation_rate", reason="x",
                        changes=[MetricChange(metric="propagation_rate",
                                              verdict=MetricVerdict.NOT_COMPARABLE)])
    view = server.contrast_view(harden.Round(
        baseline=None, findings=[], found=set(), candidates=[], contrast=c))
    assert "sample" not in view
