"""Importing an operator's own agent as a baseline (#50, H1).

The claims are the ones that would fail green if they broke: a re-imported
agent that kept its own tool code would serve the truth on every faulted
run, and an imported agent run offline would be scored as a scripted
stand-in that never read its prompt.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_gauntlet import architect, cli, interpose
from agent_gauntlet.ledger import Ledger
from agent_gauntlet.matrix import (OfflineUnsupported, _allowed_tools,
                                   _has_evidence, offline_executor, run_matrix)
from agent_gauntlet.score import Answer
from agent_gauntlet.spec import TaskSpec

ROOT = Path(__file__).resolve().parents[1]
AUDITED = ROOT / "fixtures" / "inventory" / "audited.yaml"
MODELS = {"cheap": "anthropic/claude-haiku-4-5"}


@pytest.fixture
def task():
    return TaskSpec.from_yaml(AUDITED)


def _generated(task, tmp_path, prompt="verifying", toolset="records+summary"):
    return architect.generate(
        out_dir=tmp_path / "gen", task=task, models=MODELS,
        prompts=[prompt], toolsets=[toolset], include_sentinel=False)[0]


def _own_agent(tmp_path, *, tools=("fetch_record", "list_records"),
               instructions="You are careful. Cross-check before you report.\n",
               skill_tail="", model="anthropic/claude-haiku-4-5",
               tools_py="def fetch_record(rid):\n    return 1\n"):
    agent = tmp_path / "mine"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / "skill.md").write_text(instructions + skill_tail, encoding="utf-8")
    (agent / "agent-config.yaml").write_text(yaml.safe_dump(
        {"name": "mine", "model": model, "tools": list(tools)}), encoding="utf-8")
    (agent / "tools.py").write_text(tools_py, encoding="utf-8")
    return agent


def test_an_exported_variant_reimports_to_the_same_fingerprint(task, tmp_path):
    """The round trip H2 is built on: a winner exported as `common/` comes
    back as next round's baseline. If the fingerprint moved, the same
    agent would read as two agents and a rerun would detach its history."""
    v = _generated(task, tmp_path)
    back = architect.import_agent(Path(v.common_dir).parent, task=task,
                                  out_dir=tmp_path / "imp", models=MODELS)
    assert back.fingerprint == v.fingerprint
    assert back.tools == sorted(architect.granted(v))


def test_the_operators_tool_code_is_replaced_not_copied(task, tmp_path):
    """Their tools.py would answer from their own data, below which no fault
    can be injected -- every faulted run would be scored as resisted."""
    v = architect.import_agent(_own_agent(tmp_path), task=task,
                               out_dir=tmp_path / "imp")
    written = (Path(v.common_dir) / architect.AGENT_NAME / "tools.py").read_text()
    assert written == architect._TOOLS_PY
    assert "return 1" not in written


def test_a_tool_the_harness_does_not_serve_is_refused(task, tmp_path):
    """Dropping it silently would measure a different agent; keeping it
    would serve the truth through a door the fault cannot reach."""
    with pytest.raises(ValueError, match="not tools this harness serves"):
        architect.import_agent(
            _own_agent(tmp_path, tools=("fetch_record", "query_warehouse")),
            task=task, out_dir=tmp_path / "imp")


def test_an_agent_without_the_faulted_tool_is_refused(task, tmp_path):
    """The fault could never fire, and every run would be scored clean
    under a faulted label -- rule 3b, arriving through the import path."""
    with pytest.raises(ValueError, match="not granted"):
        architect.import_agent(_own_agent(tmp_path, tools=("list_records",)),
                               task=task, out_dir=tmp_path / "imp")


def test_instructions_that_contain_the_answer_are_refused(task, tmp_path):
    """The statement leak, arriving through the other file: an agent told
    the total scores 1.00 without calling a tool."""
    truth = task.scenarios[0].expected_total
    assert truth is not None
    with pytest.raises(ValueError, match="imported instructions"):
        architect.import_agent(
            _own_agent(tmp_path, instructions=f"The answer is {truth}.\n"),
            task=task, out_dir=tmp_path / "imp")


def test_a_stale_task_section_is_replaced_by_this_tasks_statement(task, tmp_path):
    """An agent carrying an older statement would answer a different
    question under the same task fingerprint."""
    agent = _own_agent(tmp_path, skill_tail=architect.TASK_HEADING
                       + "Count the widgets in warehouse 9.\n")
    v = architect.import_agent(agent, task=task, out_dir=tmp_path / "imp")
    skill = (Path(v.common_dir) / architect.AGENT_NAME / "skill.md").read_text()
    assert "warehouse 9" not in skill
    assert task.statement in skill
    assert skill.startswith("You are careful.")


def test_an_unresolvable_model_alias_is_refused(task, tmp_path):
    """An alias nobody defines would pin a fingerprint to a model string
    that is not the one that ran."""
    with pytest.raises(ValueError, match="model"):
        architect.import_agent(_own_agent(tmp_path, model="smart"),
                               task=task, out_dir=tmp_path / "imp")


def test_the_grant_and_the_evidence_come_from_the_imported_tools(task, tmp_path):
    """Its toolset factor reads "baseline", which names no set: reading the
    grant from TOOLSETS would grant nothing and censor every detection."""
    v = architect.import_agent(
        _own_agent(tmp_path, tools=("fetch_record", "list_records", "get_summary")),
        task=task, out_dir=tmp_path / "imp")
    assert _allowed_tools(v) == {"fetch_record", "list_records", "get_summary"}
    assert _has_evidence(v) is True
    bare = architect.import_agent(_own_agent(tmp_path / "b"), task=task,
                                  out_dir=tmp_path / "imp2")
    assert _has_evidence(bare) is False


def test_an_imported_agent_is_refused_offline_before_any_run(task, tmp_path):
    """Errored runs are censored, so an executor that raised per run would
    leave the row silently absent from the board. Refuse up front."""
    v = architect.import_agent(_own_agent(tmp_path), task=task,
                               out_dir=tmp_path / "imp")
    ledger = Ledger(tmp_path / "l.jsonl")
    with pytest.raises(OfflineUnsupported):
        run_matrix(task=task, variants=[v], ledger=ledger, base_seed="s",
                   repeats=1)
    assert not ledger.path.exists() or ledger.path.read_text() == ""
    with pytest.raises(OfflineUnsupported):
        offline_executor(v, "s")


def test_a_faulted_run_of_an_imported_agent_actually_meets_the_fault(task, tmp_path):
    """Ask what would happen if the import did nothing: the run would be
    labelled faulted and nothing injected. Require a faulted call logged."""
    v = architect.import_agent(_own_agent(tmp_path), task=task,
                               out_dir=tmp_path / "imp")
    seen: list[bool] = []

    def execute(variant, seed):
        total = sum(interpose.fetch_quantity(r)
                    for r in interpose.list_record_ids())
        seen.append(any(c.get("faulted") for c in interpose.active().calls))
        return Answer(total=total)

    records = run_matrix(task=task, variants=[v],
                         ledger=Ledger(tmp_path / "l.jsonl"), base_seed="s",
                         repeats=1, executor=execute, offline=False)
    faulted = [r for r in records if r.condition == "faulted"]
    assert faulted and all(r.error is None for r in records)
    assert any(seen), "no faulted call reached the imported agent"


def test_run_with_a_baseline_but_no_live_flag_is_refused(tmp_path, capsys):
    agent = _own_agent(tmp_path)
    code = cli.main(["run", str(AUDITED), "--baseline", str(agent),
                     "--out", str(tmp_path / "r"), "--repeats", "1",
                     "--seeds", "1"])
    assert code == 2
    assert "--baseline needs --live" in capsys.readouterr().out


def test_explore_with_a_baseline_but_no_live_flag_is_refused(tmp_path, capsys):
    code = cli.main(["explore", str(AUDITED), "--baseline",
                     str(_own_agent(tmp_path))])
    assert code == 2
    assert "--baseline needs --live" in capsys.readouterr().out


def test_live_explore_builds_its_executor_from_the_target(tmp_path, monkeypatch):
    """It used to call `live_executor(task, scenario)` against a signature
    that takes the SDK target, so every live explore died before its first
    cell -- unnoticed, because nothing ran it."""
    from agent_gauntlet import live

    got = {}

    def fake_live_executor(target):
        got["target"] = target
        return lambda variant, seed: Answer(total=None)

    monkeypatch.setattr(live, "preflight", lambda variants, target=None: None)
    monkeypatch.setattr(live, "live_executor", fake_live_executor)
    cli.main(["explore", str(AUDITED), "--live", "--baseline",
              str(_own_agent(tmp_path))])
    assert got.get("target") is not None
