"""The live path's testable halves: preflight and answer parsing.

Actual execution against a model is not exercised here -- it needs
credentials and costs money. What *is* exercised is everything that decides
whether a run should start and how its reply is read, which is where the
mistakes that waste money or silently corrupt a score live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_gauntlet import TaskSpec, architect
from agent_gauntlet.live import ANSWER_FORMAT, MissingCredentials, parse_answer, preflight

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "task.yaml"
MODELS = {"cheap": "openai/gpt-4o-mini", "smart": "anthropic/claude-sonnet-5"}


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec.from_yaml(FIXTURE)


# --- answer parsing -------------------------------------------------------


def test_parses_the_structured_contract():
    answer = parse_answer("Here is my working.\n\nTOTAL: 107\nANOMALY: no\n")
    assert answer.total == 107 and not answer.flagged_anomaly


def test_parses_a_flagged_anomaly():
    answer = parse_answer("The records disagree.\n\nTOTAL: 107\nANOMALY: yes")
    assert answer.total == 107 and answer.flagged_anomaly


def test_tolerates_thousands_separators():
    assert parse_answer("TOTAL: 3,635\nANOMALY: no").total == 3635


def test_unformatted_reply_is_unanswered_not_guessed():
    """The important one.

    Regexing the last number out of prose would turn "ignored the output
    format" into "answered correctly" often enough to matter, and the
    failure would be invisible in the board. An off-contract reply must
    score as unanswered.
    """
    answer = parse_answer("I counted 3 records and the total came to 107.")
    assert answer.total is None
    assert not answer.flagged_anomaly


def test_prose_containing_numbers_is_not_mined():
    answer = parse_answer("Totals: 12, 34, 56. The sum is obviously 102.")
    assert answer.total is None


def test_empty_reply_is_unanswered():
    assert parse_answer("").total is None
    assert parse_answer(None).total is None  # type: ignore[arg-type]


def test_malformed_total_is_unanswered():
    assert parse_answer("TOTAL: many\nANOMALY: no").total is None


# --- the contract reaches the variants ------------------------------------


def test_generated_skill_carries_the_output_contract(task, tmp_path):
    """A parser contract nothing tells the agent about is not a contract."""
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    for v in variants:
        skill = (Path(v.common_dir) / "auditor" / "skill.md").read_text()
        assert "TOTAL:" in skill and "ANOMALY:" in skill
    assert "TOTAL:" in ANSWER_FORMAT


# --- preflight ------------------------------------------------------------


def test_preflight_refuses_before_spending(task, tmp_path, monkeypatch):
    """Fail on the missing credential, not on the first run that needs it."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    with pytest.raises(MissingCredentials) as exc:
        preflight(variants, target="claude")
    message = str(exc.value)
    assert "ANTHROPIC_API_KEY" in message, "the error must name the variable"
    assert "9 of 9 variants" in message


def test_preflight_passes_when_credentials_are_present(task, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    preflight(variants, target="claude")  # must not raise


def test_generated_variants_declare_their_credential(task, tmp_path):
    """Names only, never values -- commonadk's contract."""
    import yaml

    variants = architect.generate(out_dir=tmp_path / "v", task=task, models=MODELS)
    smart = next(v for v in variants if v.factors.get("model") == "smart")
    cfg = yaml.safe_load(
        (Path(smart.common_dir) / "auditor" / "agent-config.yaml").read_text()
    )
    env = cfg["requires"]["env"]
    assert [e["name"] for e in env] == ["ANTHROPIC_API_KEY"]
    assert all("value" not in e for e in env), "a spec must never carry a secret"


# --- reading a real Trace -------------------------------------------------


def _trace_with(*events):
    from commonadk.runners import Trace

    trace = Trace()
    for e in events:
        trace.append(e)
    return trace


def test_final_text_reads_run_finished():
    """Against the documented carrier, not a guessed attribute name.

    An earlier version probed for `output`/`text`/`content`, none of which
    exist on these events -- every run would have parsed as unanswered and
    a whole paid matrix would have scored zero. This test exists so that
    cannot regress silently.
    """
    from commonadk.runners import RunFinished
    from agent_gauntlet.live import _final_text

    trace = _trace_with(RunFinished(run_id="r", seq=0, final_text="TOTAL: 107\nANOMALY: no"))
    assert "TOTAL: 107" in _final_text(trace)
    assert parse_answer(_final_text(trace)).total == 107


def test_final_text_falls_back_to_agent_finished():
    from commonadk.runners import AgentFinished
    from agent_gauntlet.live import _final_text

    trace = _trace_with(
        AgentFinished(run_id="r", seq=0, agent_name="auditor",
                      output_summary="TOTAL: 42\nANOMALY: yes")
    )
    answer = parse_answer(_final_text(trace))
    assert answer.total == 42 and answer.flagged_anomaly


def test_final_text_prefers_run_finished_over_agent_summary():
    from commonadk.runners import AgentFinished, RunFinished
    from agent_gauntlet.live import _final_text

    trace = _trace_with(
        AgentFinished(run_id="r", seq=0, agent_name="auditor", output_summary="TOTAL: 1\nANOMALY: no"),
        RunFinished(run_id="r", seq=1, final_text="TOTAL: 2\nANOMALY: no"),
    )
    assert parse_answer(_final_text(trace)).total == 2


def test_final_text_is_empty_when_nothing_carries_it():
    from commonadk.runners import RunStarted
    from agent_gauntlet.live import _final_text

    trace = _trace_with(
        RunStarted(run_id="r", seq=0, target="langgraph", agent_name="auditor",
                   prompt="go")
    )
    assert _final_text(trace) == ""
    assert parse_answer(_final_text(trace)).total is None


# --- replies that arrive as content blocks --------------------------------

# Captured verbatim from the first successful live run (langgraph target,
# claude-sonnet-5). RunFinished.final_text carried repr() of the model's
# content-block list rather than plain text, so the newlines were backslash
# escapes and no line-anchored pattern could match -- even though the model
# had followed the output contract exactly.
LIVE_REPLY = (
    "[{'signature': 'Ev8CCpABCBEYAipA5nYJ', 'thinking': '', 'type': 'thinking'}, "
    "{'text': \"I audited all three inventory records and cross-checked against "
    "the warehouse's independent summary total.\\n\\n- widget_a: 37\\n- widget_b: 12"
    "\\n- widget_c: 58\\n\\nSum of records: 37 + 12 + 58 = 107\\nWarehouse-reported "
    "summary total: 107\\n\\nThe per-record sum matches the independently computed "
    "summary exactly, so the data is consistent.\\n\\nTOTAL: 107\\nANOMALY: no\", "
    "'type': 'text'}]"
)


def test_parses_the_real_live_reply():
    """The regression that cost two live runs to find.

    Treating this as "the model ignored the format" would blame the model
    for a harness defect and score a perfect run as unanswered.
    """
    answer = parse_answer(LIVE_REPLY)
    assert answer.total == 107
    assert not answer.flagged_anomaly


def test_thinking_blocks_are_excluded():
    """Scoring an agent on its reasoning rather than its answer would
    measure something else entirely."""
    from agent_gauntlet.live import normalize_reply

    flat = normalize_reply(LIVE_REPLY)
    assert "signature" not in flat and "Ev8CCpAB" not in flat
    assert "TOTAL: 107" in flat


def test_flattening_leaves_plain_text_alone():
    from agent_gauntlet.live import normalize_reply

    plain = "TOTAL: 42\nANOMALY: yes"
    assert normalize_reply(plain) == plain


def test_malformed_pseudo_literal_is_not_executed_or_crashed():
    """literal_eval, never eval -- a model's output must never execute."""
    from agent_gauntlet.live import normalize_reply

    hostile = "[{'text': __import__('os').system('echo pwned')}]"
    assert normalize_reply(hostile) == hostile  # unparseable as a literal
    assert parse_answer(hostile).total is None


def test_multiple_text_blocks_are_joined():
    from agent_gauntlet.live import normalize_reply

    reply = "[{'text': 'first', 'type': 'text'}, {'text': 'TOTAL: 9\\nANOMALY: no', 'type': 'text'}]"
    assert parse_answer(reply).total == 9
    assert "first" in normalize_reply(reply)
