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
AUDITED = Path(__file__).resolve().parents[1] / "fixtures" / "inventory" / "audited.yaml"
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


# --- probe exit codes, because CI reads them -------------------------------
#
# The probe now runs unattended on every push. That only works if it can tell
# "this commit broke the path" from "Anthropic had a bad afternoon" -- a build
# that goes red for the second is the ci.yml failure mode all over again: a
# signal nobody can act on, hiding the ones that matter.


def _probe_argv(fixture: Path, tmp_path: Path, *extra: str):
    from agent_gauntlet.cli import build_parser

    # --no-faulted by default here: these exercise the clean path and the
    # exit-code classifier with stubs that never touch the interposer, so
    # the faulted half would (correctly) fail them for lack of tool calls.
    # test_probe_faulted.py covers that half with a stub that does.
    return build_parser().parse_args(
        ["probe", str(fixture), "--out", str(tmp_path / "p"),
         "--target", "langgraph", "--no-faulted", *extra]
    )


def _run_probe(monkeypatch, tmp_path, executor, fixture):
    from agent_gauntlet import live as live_mod
    from agent_gauntlet.cli import _probe

    monkeypatch.setattr(live_mod, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(live_mod, "live_executor", lambda target: executor)
    return _probe(_probe_argv(fixture, tmp_path))


def test_probe_reports_an_unreachable_model_separately(tmp_path, monkeypatch):
    """Provider outage -> 4. Never a red build."""
    def never_returns(variant, seed):
        raise RuntimeError("overloaded_error: the model is overloaded")

    assert _run_probe(monkeypatch, tmp_path, never_returns, FIXTURE) == 4


def test_probe_fails_when_the_trace_arrives_and_then_breaks(tmp_path, monkeypatch):
    """A trace came back and something downstream broke -> 1, ours to fix.

    Classified on evidence -- did anything reach `_final_text` -- rather than
    on the exception type, which would be six SDKs' worth of guesswork.
    """
    from agent_gauntlet import live as live_mod

    def breaks_after_the_trace(variant, seed):
        live_mod._final_text(object())
        raise RuntimeError("something downstream of the trace")

    assert _run_probe(monkeypatch, tmp_path, breaks_after_the_trace, FIXTURE) == 1


def test_probe_restores_the_patched_final_text_after_a_failure(tmp_path, monkeypatch):
    """The probe swaps `_final_text` to capture the raw reply. If a failure
    left the swap in place, every later run in the same process would write
    into a dead dict."""
    from agent_gauntlet import live as live_mod

    before = live_mod._final_text
    _run_probe(
        monkeypatch, tmp_path,
        lambda v, s: (_ for _ in ()).throw(RuntimeError("boom")),
        FIXTURE,
    )
    assert live_mod._final_text is before


def test_probe_runs_against_the_hardened_fixture(tmp_path, monkeypatch, capsys):
    """The fixture CI actually probes, and the diagnostic it exists for.

    A model that reports the audited figure has not reconciled, and every
    verifying variant in the matrix will undercount by the same amount --
    the prompt factor would measure nothing and the $5 would be wasted. The
    probe says so, and does NOT fail: correctness is the experiment's
    subject, not a build invariant.
    """
    from agent_gauntlet import live as live_mod
    from agent_gauntlet.score import Answer

    from agent_gauntlet.cli import hardest_scenario

    audited = hardest_scenario(TaskSpec.from_yaml(AUDITED))

    def reports_the_audit(variant, seed):
        live_mod._final_text(object())
        return Answer(total=audited.audited_total, flagged_anomaly=False)

    code = _run_probe(monkeypatch, tmp_path, reports_the_audit, AUDITED)
    out = capsys.readouterr().out

    assert code == 0, "a wrong answer is a result, not a broken path"
    assert "ANSWER: the AUDITED figure" in out
    assert str(audited.expected_total) in out


def test_missing_dependency_is_never_reported_as_a_provider_outage(tmp_path, monkeypatch):
    """The regression that shipped a green probe having called no model.

    `live-probe.yml` installed the package but not the langgraph extra, so
    the run died on `ModuleNotFoundError: No module named 'langchain_core'`.
    The classifier decided that by absence -- nothing had reached
    `_final_text`, so "the call never came back, so it must be the network"
    -- reported a provider outage, exited 0 and went green.

    A false green is worse than the false red it was avoiding: nobody
    investigates a pass.
    """
    def missing_dep(variant, seed):
        raise ModuleNotFoundError("No module named 'langchain_core'")

    assert _run_probe(monkeypatch, tmp_path, missing_dep, FIXTURE) == 1


def test_a_wrapped_missing_dependency_is_also_ours(tmp_path, monkeypatch):
    """SDKs wrap. The cause chain has to be inspected, not just the top."""
    def wrapped(variant, seed):
        try:
            raise ModuleNotFoundError("No module named 'langchain_core'")
        except ModuleNotFoundError as inner:
            raise RuntimeError("runner failed to start") from inner

    assert _run_probe(monkeypatch, tmp_path, wrapped, FIXTURE) == 1


def test_an_unrecognised_failure_defaults_to_ours(tmp_path, monkeypatch):
    """Unknown is OURS. A false red is actionable; a false green is not."""
    def mystery(variant, seed):
        raise RuntimeError("something nobody predicted")

    assert _run_probe(monkeypatch, tmp_path, mystery, FIXTURE) == 1


def test_an_import_error_mentioning_timeout_is_still_ours(tmp_path, monkeypatch):
    """The message matches a provider marker; the type settles it anyway."""
    def confusing(variant, seed):
        raise ImportError("cannot import name 'Timeout' from 'httpx'")

    assert _run_probe(monkeypatch, tmp_path, confusing, FIXTURE) == 1


@pytest.mark.parametrize("exc", [
    RuntimeError("overloaded_error: the model is overloaded"),
    RuntimeError("HTTP 529 from api.anthropic.com"),
    RuntimeError("rate limit exceeded, please try again"),
    ConnectionError("connection reset by peer"),
    TimeoutError("read timed out"),
])
def test_genuine_provider_failures_still_earn_code_4(tmp_path, monkeypatch, exc):
    """The build must not go red because a provider had a bad afternoon."""
    def unreachable(variant, seed):
        raise exc

    assert _run_probe(monkeypatch, tmp_path, unreachable, FIXTURE) == 4


def test_probe_defaults_to_the_cheapest_model():
    """It runs on every push; the default must be the cheap grid level.

    Haiku 4.5 is $1/$5 per MTok against Sonnet 5's $2/$10. The probe proves
    the path, and the path does not care which model walked it.
    """
    from agent_gauntlet.cli import DEFAULT_MODELS, build_parser

    args = build_parser().parse_args(["probe", "x.yaml"])
    assert args.model == "cheap"
    assert DEFAULT_MODELS["cheap"] == "anthropic/claude-haiku-4-5"


def test_probe_actually_runs_the_model_level_it_was_given(tmp_path, monkeypatch, capsys):
    """Asserted on the variant, not on the flag.

    The flag existing proves nothing -- the previous version hardcoded
    `smart` and would have kept doing so with a --model option bolted on.
    """
    from agent_gauntlet import live as live_mod
    from agent_gauntlet.cli import _probe, build_parser
    from agent_gauntlet.score import Answer

    seen: dict = {}

    def record(variant, seed):
        seen["variant"] = variant
        live_mod._final_text(object())
        from agent_gauntlet.cli import hardest_scenario

        return Answer(
            total=hardest_scenario(TaskSpec.from_yaml(FIXTURE)).expected_total
        )

    monkeypatch.setattr(live_mod, "preflight", lambda *a, **k: None)
    monkeypatch.setattr(live_mod, "live_executor", lambda target: record)
    _probe(build_parser().parse_args(
        ["probe", str(FIXTURE), "--out", str(tmp_path / "p"), "--no-faulted"]
    ))

    assert seen["variant"].factors["model"] == "cheap"
    assert "claude-haiku-4-5" in capsys.readouterr().out


def test_probe_walks_the_hardest_scenario_not_the_first():
    """The first scenario is the smallest. Certifying the matrix against it
    tests the case the matrix contains least of."""
    from agent_gauntlet.cli import hardest_scenario

    task = TaskSpec.from_yaml(AUDITED)
    chosen = hardest_scenario(task)
    assert chosen is not task.scenarios[0]
    assert len(chosen.records) == max(len(s.records) for s in task.scenarios)
